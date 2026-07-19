"""
預覽「總經/風險摘要 → 波動度評分」的 prompt 效果。純測試，不寫入資料庫、不改 schema。

同一集（同 source_filename）不管有幾個 mode，總經/風險內容本質相同，
只挑 pro 版本（沒有 pro 才退而求其次用 both / novice）打一次分，
其餘 mode 的 row 共用同一個結果 —— 省一半以上的 LLM 呼叫。

用法：
  python manage.py preview_macro_volatility                      # 最新 2 集
  python manage.py preview_macro_volatility --limit 5            # 最新 5 集
  python manage.py preview_macro_volatility --ids 12 34          # 指定 SummaryRecord id（會依 source_filename 自動分組）
  python manage.py preview_macro_volatility --model models/gemini-2.5-flash-lite
"""
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.summaries.models import SummaryRecord
from apps.summaries.services.gemini import (
    make_client,
    gemini_generate_with_retry,
    extract_json_object,
)

_MACRO_TOPIC = "總體經濟環境"
_RISK_TOPIC = "風險提示"
_MODE_PREFERENCE = {"pro": 0, "both": 1, "novice": 2}


def _extract_context(row: SummaryRecord) -> dict:
    args = row.arguments or []
    macro = next((a.get("summary", "") for a in args if a.get("topic") == _MACRO_TOPIC), "")
    risk = next((a.get("summary", "") for a in args if a.get("topic") == _RISK_TOPIC), "")
    tw = row.investment_takeaways or {}
    return {
        "macro": macro,
        "risk": risk,
        "bearish": tw.get("bearish") or [],
        "watchlist": tw.get("watchlist") or [],
        "stance": tw.get("podcaster_stance", ""),
    }


def _build_prompt(ctx: dict) -> str:
    bearish_txt = "\n".join(f"  - {b}" for b in ctx["bearish"]) or "  （無）"
    watchlist_txt = "\n".join(f"  - {w}" for w in ctx["watchlist"]) or "  （無）"
    return f"""你是「總經與風險敏感度」評分助手。
以下是一集投資理財 Podcast 摘要中，被結構化萃取出來的「總體經濟環境」與「風險提示」段落，以及該集的看空/觀察名單。

請判斷「這一集反映出的市場氛圍，短期內可能造成的股價波動程度」，分成 low / medium / high 三級：
- low：總經環境穩定、沒有明顯風險警示，市場氛圍平靜
- medium：有一些不確定性或分歧看法，但沒有立即衝擊性事件
- high：明確提到升息/降息轉向、地緣政治衝突、經濟衰退疑慮、資金緊縮、系統性風險等字眼，暗示短期波動可能加劇

===總體經濟環境===
{ctx['macro'] or '（本集無此段落）'}

===風險提示===
{ctx['risk'] or '（本集無此段落）'}

===看空名單===
{bearish_txt}

===觀察名單===
{watchlist_txt}

===整集立場===
{ctx['stance'] or '（無）'}

reason 撰寫規則：必須引用內容中至少一個具體關鍵字或事件（例如實際提到的人名、機構、數字、政策），
不可只是換句話說重複 low/medium/high 的定義本身。
  ✗ 「市場存在多空因素，有不確定性但無立即衝擊」（沒有引用任何具體內容，等於白講）
  ✓ 「Fed暫緩降息、Lisa Cook去留疑慮未除，記憶體恐長期供過於求」（點名具體事件）

只輸出 JSON（不要 ```json 或任何說明文字），格式：
{{"volatility_level": "low|medium|high", "reason": "一句話說明依據，20字以內，需引用具體關鍵字"}}
"""


class Command(BaseCommand):
    help = "預覽總經/風險摘要打分效果（不寫入資料庫，純測試 prompt）"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=2, help="沒指定 --ids 時，抓最新幾筆（預設 2）")
        parser.add_argument("--ids", nargs="+", type=int, metavar="ID", help="指定 SummaryRecord id")
        parser.add_argument("--model", default="models/gemini-2.5-flash", help="Gemini model 名稱")

    def handle(self, *args, **options):
        api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
        vertex_project = os.getenv("VERTEX_PROJECT_ID", "").strip()
        if not api_key and not vertex_project:
            self.stderr.write("GEMINI_API_KEY 或 VERTEX_PROJECT_ID 未設定，終止。")
            return

        qs = SummaryRecord.objects.using("summariesdb").order_by("-created_at")
        if options["ids"]:
            all_rows = list(qs.filter(id__in=options["ids"]))
        else:
            # 多抓一些 row，因為同一集通常有 2-3 個 mode，分組後才湊得滿 limit 個「集數」
            all_rows = list(qs[: options["limit"] * 3 + 10])

        if not all_rows:
            self.stdout.write("找不到符合條件的 SummaryRecord。")
            return

        # ── 依 source_filename 分組：同一集只留下最適合打分的那個 mode ──────────
        groups: dict[str, list[SummaryRecord]] = {}
        order: list[str] = []
        for row in all_rows:
            key = row.source_filename or f"__no_source_{row.id}"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)

        if not options["ids"]:
            order = order[: options["limit"]]

        client = make_client(api_key)
        model = options["model"]

        for key in order:
            sibling_rows = groups[key]
            source_row = min(sibling_rows, key=lambda r: _MODE_PREFERENCE.get(r.mode, 9))
            sibling_ids = [r.id for r in sibling_rows if r.id != source_row.id]

            ctx = _extract_context(source_row)
            self.stdout.write("=" * 70)
            self.stdout.write(f"episode: {key}")
            self.stdout.write(
                f"打分依據: [id={source_row.id}] mode={source_row.mode} podcaster={source_row.podcaster}"
                + (f"  (同集其他 row 共用此結果: {sibling_ids})" if sibling_ids else "")
            )
            self.stdout.write("-" * 70)
            self.stdout.write(f"總體經濟環境: {ctx['macro'] or '(無)'}")
            self.stdout.write(f"風險提示: {ctx['risk'] or '(無)'}")
            self.stdout.write(f"看空名單: {ctx['bearish']}")
            self.stdout.write(f"觀察名單: {ctx['watchlist']}")
            self.stdout.write(f"整集立場: {ctx['stance']}")

            if not ctx["macro"] and not ctx["risk"] and not ctx["bearish"]:
                self.stdout.write(
                    "⚠ 本集沒有總經/風險/看空資料，略過打分"
                    "（正式上線時這種情況會 fallback 成中性值 medium）"
                )
                continue

            prompt = _build_prompt(ctx)
            try:
                resp = gemini_generate_with_retry(
                    client=client, model=model, prompt_text=prompt,
                    temperature=0.2, max_output_tokens=300, max_tries=3,
                )
                text = getattr(resp, "text", "") or ""
                js = extract_json_object(text)
                result = json.loads(js) if js else {}
            except Exception as e:
                self.stderr.write(f"✗ LLM 呼叫失敗：{e}")
                continue

            self.stdout.write("-" * 70)
            level = result.get("volatility_level", "?")
            reason = result.get("reason", "")
            self.stdout.write(self.style.SUCCESS(f"→ volatility_level={level}  reason={reason}"))

        self.stdout.write("=" * 70)
        self.stdout.write(f"共測試 {len(order)} 集，純預覽，未寫入資料庫。")
