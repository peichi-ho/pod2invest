"""
補打舊資料的總經/風險分數：episode_macro/episode_risk（SummaryRecord）跟
call_risk（BacktestingRecord）。只讀已經存在的欄位（arguments、investment_takeaways、
thesis、evidence_quote）去打分，不重新產生摘要、不重跑 outlook 抽取，不用逐字稿。

同一集（同 source_filename）只打一次 episode_macro/episode_risk，比照試算頁的
去重邏輯套用到所有 mode 的 row；call_risk 則是逐筆 BacktestingRecord 各自打分。

用法：
  python manage.py backfill_macro_risk_scores                    # 全部補
  python manage.py backfill_macro_risk_scores --limit 20         # 只補最新 20 集/20 筆
  python manage.py backfill_macro_risk_scores --episodes-only    # 只補 episode_macro/episode_risk
  python manage.py backfill_macro_risk_scores --calls-only       # 只補 call_risk
  python manage.py backfill_macro_risk_scores --dry-run          # 只印不寫入
  python manage.py backfill_macro_risk_scores --model models/gemini-2.5-flash-lite
"""
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.summaries.models import SummaryRecord, BacktestingRecord
from apps.summaries.services.gemini import (
    make_client,
    gemini_generate_with_retry,
    extract_json_object,
)
from apps.summaries.services.postprocess import _normalize_episode_macro, _normalize_episode_risk
from apps.summaries.services.outlook import _normalize_call_risk

_MACRO_TOPIC = "總體經濟環境"
_RISK_TOPIC = "風險提示"
_MODE_PREFERENCE = {"pro": 0, "both": 1, "novice": 2}


# ── Episode 層：episode_macro + episode_risk ──────────────────────────────

def _extract_episode_context(row: SummaryRecord) -> dict:
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


def _build_episode_prompt(ctx: dict) -> str:
    bearish_txt = "\n".join(f"  - {b}" for b in ctx["bearish"]) or "  （無）"
    watchlist_txt = "\n".join(f"  - {w}" for w in ctx["watchlist"]) or "  （無）"
    return f"""你是「總經與風險敏感度」評分助手。
以下是一集投資理財 Podcast 摘要中，已經萃取出來的「總體經濟環境」與「風險提示」段落，以及該集的看空/觀察名單。

請分別判斷：

1. episode_macro：podcaster 對總體經濟的樂觀/悲觀傾向，五選一：
   大幅樂觀 / 樂觀 / 中性 / 悲觀 / 大幅悲觀

2. episode_risk：這一集反映出的市場氛圍，短期內可能造成的股價波動程度，只能二選一：中 / 高
   （不要選低，沒有風險提示內容的情況由程式另外處理，不需要你判斷）
   中：有一些不確定性或分歧看法，但沒有立即衝擊性事件
   高：明確提到升息/降息轉向、地緣政治衝突、經濟衰退疑慮、資金緊縮、系統性風險等字眼

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

reason 必須引用內容中至少一個具體關鍵字或事件，不可只是複述等級定義本身。

只輸出 JSON（不要 ```json 或任何說明文字）：
{{"episode_macro": {{"level": "...", "reason": "..."}}, "episode_risk": {{"level": "中|高", "reason": "..."}}}}
"""


def _score_episode(client, model: str, ctx: dict) -> dict:
    prompt = _build_episode_prompt(ctx)
    resp = gemini_generate_with_retry(
        client=client, model=model, prompt_text=prompt,
        temperature=0.2, max_output_tokens=400, max_tries=3,
    )
    text = getattr(resp, "text", "") or ""
    js = extract_json_object(text)
    result = json.loads(js) if js else {}
    return {
        "episode_macro": result.get("episode_macro"),
        "episode_risk": result.get("episode_risk"),
    }


# ── 個股層：call_risk ──────────────────────────────────────────────────────

def _build_call_prompt(call: BacktestingRecord, named_in_risk: str) -> str:
    named_block = (
        f"\n===本集風險提示裡也點名了這支標的===\n{named_in_risk}\n"
        if named_in_risk else ""
    )
    return f"""你是「個股風險敏感度」評分助手。
只根據以下這一筆看法自己的內容，判斷風險/不確定性高低，不要用其他無關內容去評估。

===標的===
{call.asset}

===方向===
{call.direction}

===論述===
{call.thesis}

===原話引用===
{call.evidence_quote}
{named_block}
請判斷這筆看法本身的風險/不確定性程度，只能二選一：中 / 高
（不要選低，沒有明顯風險的情況由程式另外處理）
中：這筆看法本身有附帶條件、但書，或說話者語氣保留
高：這筆看法本身明確提到針對這檔標的的具體風險（例如法律訴訟、競爭加劇、財務壓力、股權稀釋）

reason 必須引用具體內容，不可只是複述等級定義本身。

只輸出 JSON：{{"level": "中|高", "reason": "..."}}
"""


def _score_call(client, model: str, call: BacktestingRecord, named_in_risk: str) -> dict:
    prompt = _build_call_prompt(call, named_in_risk)
    resp = gemini_generate_with_retry(
        client=client, model=model, prompt_text=prompt,
        temperature=0.2, max_output_tokens=300, max_tries=3,
    )
    text = getattr(resp, "text", "") or ""
    js = extract_json_object(text)
    return json.loads(js) if js else {}


class Command(BaseCommand):
    help = "補打舊資料的 episode_macro/episode_risk/call_risk（不重跑摘要/outlook 抽取）"

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None, help="限制處理筆數（不分類型各自套用）")
        parser.add_argument("--episodes-only", action="store_true", help="只補 episode_macro/episode_risk")
        parser.add_argument("--calls-only", action="store_true", help="只補 call_risk")
        parser.add_argument("--dry-run", action="store_true", help="只印不寫入")
        parser.add_argument("--model", default="models/gemini-2.5-flash", help="Gemini model 名稱")

    def handle(self, *args, **options):
        api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
        vertex_project = os.getenv("VERTEX_PROJECT_ID", "").strip()
        if not api_key and not vertex_project:
            self.stderr.write("GEMINI_API_KEY 或 VERTEX_PROJECT_ID 未設定，終止。")
            return

        dry_run = options["dry_run"]
        limit = options["limit"]
        model = options["model"]
        client = make_client(api_key)

        if not options["calls_only"]:
            self._backfill_episodes(client, model, limit, dry_run)
        if not options["episodes_only"]:
            self._backfill_calls(client, model, limit, dry_run)

    # ── episodes ────────────────────────────────────────────────────────
    def _backfill_episodes(self, client, model, limit, dry_run):
        self.stdout.write("=" * 70)
        self.stdout.write("補 episode_macro / episode_risk")
        self.stdout.write("=" * 70)

        qs = (
            SummaryRecord.objects.using("summariesdb")
            .filter(Q(episode_macro={}) | Q(episode_risk={}))
            .order_by("-created_at")
        )
        rows = list(qs)

        groups: dict[str, list[SummaryRecord]] = {}
        order: list[str] = []
        for row in rows:
            key = row.source_filename or f"__no_source_{row.id}"
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(row)

        if limit:
            order = order[:limit]

        done = 0
        for key in order:
            sibling_rows = groups[key]
            source_row = min(sibling_rows, key=lambda r: _MODE_PREFERENCE.get(r.mode, 9))
            all_ids = [r.id for r in sibling_rows]

            ctx = _extract_episode_context(source_row)
            self.stdout.write(f"[{key}] rows={all_ids}")

            if not ctx["macro"] and not ctx["risk"] and not ctx["bearish"]:
                episode_macro = {"level": "中性", "reason": "無總體經濟環境/風險提示/看空資料"}
                episode_risk = {"level": "低", "reason": "本集無風險提示段落"}
            else:
                try:
                    result = _score_episode(client, model, ctx)
                except Exception as e:
                    self.stderr.write(f"  ✗ LLM 呼叫失敗：{e}")
                    continue
                episode_macro = _normalize_episode_macro(result.get("episode_macro"))
                # 沒有風險提示段落一律覆寫成低，不管 LLM 說什麼
                episode_risk = _normalize_episode_risk(result.get("episode_risk"), source_row.arguments or [])

            self.stdout.write(
                self.style.SUCCESS(f"  → macro={episode_macro} risk={episode_risk}")
            )

            if not dry_run:
                SummaryRecord.objects.using("summariesdb").filter(id__in=all_ids).update(
                    episode_macro=episode_macro, episode_risk=episode_risk,
                )
            done += 1

        self.stdout.write(f"episodes 完成：{done} 集" + ("（dry-run，未寫入）" if dry_run else ""))

    # ── calls ───────────────────────────────────────────────────────────
    def _backfill_calls(self, client, model, limit, dry_run):
        self.stdout.write("=" * 70)
        self.stdout.write("補 call_risk")
        self.stdout.write("=" * 70)

        qs = (
            BacktestingRecord.objects.using("summariesdb")
            .filter(call_risk={})
            .exclude(thesis="")
            .select_related("summary")
            .order_by("-created_at")
        )
        if limit:
            qs = qs[:limit]

        done = 0
        for call in qs:
            risk_arg_text = ""
            if call.summary:
                risk_arg_text = next(
                    (a.get("summary", "") for a in (call.summary.arguments or [])
                     if a.get("topic") == _RISK_TOPIC),
                    "",
                )
            named_in_risk = risk_arg_text if (call.asset and call.asset in risk_arg_text) else ""

            self.stdout.write(f"[id={call.id}] {call.asset} ({call.direction}): {call.thesis[:40]}")

            if not risk_arg_text and not call.thesis:
                call_risk = {"level": "低", "reason": ""}
            else:
                try:
                    result = _score_call(client, model, call, named_in_risk)
                except Exception as e:
                    self.stderr.write(f"  ✗ LLM 呼叫失敗：{e}")
                    continue
                call_risk = _normalize_call_risk(result)
                # 沒有明顯風險提示可用時，不硬要 LLM 判定為中/高
                if not call.thesis and not call.evidence_quote:
                    call_risk = {"level": "低", "reason": ""}

            self.stdout.write(self.style.SUCCESS(f"  → call_risk={call_risk}"))

            if not dry_run:
                call.call_risk = call_risk
                call.save(using="summariesdb", update_fields=["call_risk"])
            done += 1

        self.stdout.write(f"calls 完成：{done} 筆" + ("（dry-run，未寫入）" if dry_run else ""))
