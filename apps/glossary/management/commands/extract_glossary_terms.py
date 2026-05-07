"""
management command：從所有摘要內容用 LLM 批次萃取財經專有名詞，upsert 進 GlossaryTerm。

用法：
  python manage.py extract_glossary_terms              # 跑全部 pro 摘要
  python manage.py extract_glossary_terms --dry-run    # 只印不寫入
  python manage.py extract_glossary_terms --batch 20   # 每批幾集（預設 30）
"""
import json
import time

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.summaries.models import SummaryRecord
from apps.glossary.models import GlossaryTerm, GlossaryAlias
from apps.summaries.services.gemini import make_client, gemini_generate_with_retry

EXTRACT_MODEL = "models/gemini-2.5-flash"

SYSTEM_PROMPT = """你是一個台灣金融投資術語專家，熟悉股票市場、總體經濟、產業分析等領域的專有名詞。
你的任務是從 Podcast 投資節目的摘要中，萃取出所有財經投資專有名詞。"""

EXTRACT_PROMPT_TEMPLATE = """以下是多集 Podcast 投資節目的摘要內容（pro 模式），請從中萃取所有財經投資專有名詞。

【萃取規則】
- 只收真正的財經/投資/技術/產業專有名詞
- 不收：公司名稱（台積電、輝達）、人名（鮑爾）、國家地名
- 不收：一般用語（行情、股價、買賣），只收有特定含義的術語
- 若同一術語有多個常見說法（如「本益比」=「P/E」），都列為 aliases
- 已知術語也要重新確認定義是否完整

【輸出格式】
只輸出 JSON 陣列（不要 ```json），每筆格式：
[
  {{
    "term": "本益比",
    "short_definition": "股價除以每股盈餘，用來評估股票是否貴或便宜（50字以內）",
    "long_definition": "本益比（Price-to-Earnings Ratio）是衡量股票估值的基本指標，計算方式為股價÷每股盈餘（EPS）。數值越低代表股票相對便宜，越高代表市場對未來成長的預期越樂觀或股票越貴。一般成熟產業本益比約10-15倍，成長型科技股可達30倍以上。",
    "category": "股票分析",
    "aliases": ["P/E", "P/E ratio", "Price-to-Earnings"]
  }}
]

category 只能從以下選擇：
  總體經濟 / 股票分析 / 技術分析 / 產業分析 / 衍生品 / 基本面分析 / 政策法規 / 其他

【摘要內容】
{content}
"""


def _collect_content(batch: list[SummaryRecord]) -> str:
    """把一批 SummaryRecord 的 arguments 內容組成字串"""
    parts = []
    for record in batch:
        arguments = record.arguments or []
        for arg in arguments:
            topic = arg.get("topic", "")
            summary = arg.get("summary", "")
            key_data = arg.get("key_data", [])

            if summary:
                parts.append(f"[{topic}] {summary}")

            for kd in key_data:
                label = kd.get("label", "").strip()
                if label:
                    parts.append(label)

    return "\n".join(parts)


def _upsert_terms(terms: list[dict], stdout, dry_run: bool) -> tuple[int, int]:
    """upsert 萃取出的術語，回傳 (新增數, 更新數)"""
    created = 0
    updated = 0

    for t in terms:
        term_str = (t.get("term") or "").strip()
        if not term_str:
            continue

        short_def = (t.get("short_definition") or "").strip()
        long_def = (t.get("long_definition") or "").strip()
        category = (t.get("category") or "其他").strip()
        aliases = [a.strip() for a in (t.get("aliases") or []) if a.strip()]

        stdout.write(f"  {term_str}（{category}）")

        if dry_run:
            continue

        obj, is_created = GlossaryTerm.objects.update_or_create(
            term=term_str,
            defaults={
                "short_definition": short_def,
                "long_definition": long_def,
                "category": category,
                "lang": "zh-TW",
                "is_active": True,
            },
        )

        for alias in aliases:
            GlossaryAlias.objects.get_or_create(term=obj, alias=alias)

        if is_created:
            created += 1
        else:
            updated += 1

    return created, updated


class Command(BaseCommand):
    help = "從所有摘要內容批次萃取財經專有名詞，upsert 進 GlossaryTerm"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="只印出會萃取的術語，不寫入資料庫",
        )
        parser.add_argument(
            "--batch", type=int, default=30,
            help="每次送給 LLM 的集數（預設 30）",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch"]

        import os
        api_key = getattr(settings, "GEMINI_API_KEY", "").strip()
        vertex_project = os.getenv("VERTEX_PROJECT_ID", "").strip()
        if not api_key and not vertex_project:
            self.stderr.write("GEMINI_API_KEY 或 VERTEX_PROJECT_ID 未設定")
            return

        client = make_client(api_key)

        # 只取 pro 模式（內容最完整）
        records = list(
            SummaryRecord.objects.using("summariesdb")
            .filter(mode="pro")
            .exclude(arguments=[])
            .order_by("id")
        )

        total_records = len(records)
        self.stdout.write(f"共 {total_records} 筆 pro 摘要，每批 {batch_size} 筆")
        self.stdout.write("=" * 60)

        total_created = 0
        total_updated = 0
        total_terms = 0

        for batch_start in range(0, total_records, batch_size):
            batch = records[batch_start: batch_start + batch_size]
            batch_end = min(batch_start + batch_size, total_records)
            self.stdout.write(
                f"\n[批次 {batch_start + 1}–{batch_end} / {total_records}] 萃取中..."
            )

            content = _collect_content(batch)
            if not content.strip():
                self.stdout.write("  （無內容，跳過）")
                continue

            prompt = EXTRACT_PROMPT_TEMPLATE.format(content=content)

            full_prompt = f"{SYSTEM_PROMPT}\n\n{prompt}"

            try:
                resp = gemini_generate_with_retry(
                    client=client,
                    model=EXTRACT_MODEL,
                    prompt_text=full_prompt,
                    temperature=0.1,
                    max_output_tokens=8000,
                    max_tries=3,
                )
                raw = getattr(resp, "text", "") or ""
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  LLM 呼叫失敗：{e}"))
                continue

            # 解析 JSON
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            try:
                terms = json.loads(raw)
                if not isinstance(terms, list):
                    raise ValueError("回傳不是陣列")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  JSON 解析失敗：{e}"))
                self.stdout.write(f"  原始回應（前 300 字）：{raw[:300]}")
                continue

            self.stdout.write(f"  萃取到 {len(terms)} 個術語：")
            created, updated = _upsert_terms(terms, self.stdout, dry_run)
            total_terms += len(terms)
            total_created += created
            total_updated += updated

            # 批次之間稍作停頓，避免 API rate limit
            if batch_end < total_records:
                time.sleep(5)

        self.stdout.write("\n" + "=" * 60)
        mode_str = "[DRY RUN] " if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode_str}完成：共處理 {total_records} 筆摘要，"
                f"萃取 {total_terms} 個術語，"
                f"新增 {total_created} 筆，更新 {total_updated} 筆"
            )
        )
