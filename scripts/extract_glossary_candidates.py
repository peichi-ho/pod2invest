# scripts/extract_glossary_candidates.py
"""
從全部pro摘要批次萃取財經名詞候選清單，只寫入一個JSON檔案給人工審核，
不直接寫進GlossaryTerm資料庫——跟原本的 `python manage.py extract_glossary_terms`
(有 --dry-run，但只是印出來、不去重、不比對既有資料庫)不同，這支腳本會：
  1. 跑完全部批次，把每批萃取到的詞彙全部收集起來
  2. 同一個術語如果在不同批次都被抽到，合併成一筆(不同批次的定義可能用字不同，
     取字數最長、看起來最完整的那個版本)
  3. 對照現有GlossaryTerm資料庫，標記每筆候選是「新增」還是「更新既有定義」，
     「更新」的話如果新舊定義文字其實一樣就不列入候選(沒有意義要求人工確認)
  4. 全部結果存成一個JSON檔，供審核後再用另一支腳本(apply_glossary_candidates.py)
     實際寫入資料庫，這支腳本完全不碰資料庫寫入

用法：
  python scripts/extract_glossary_candidates.py --out /path/to/candidates.json
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import SummaryRecord
from apps.glossary.models import GlossaryTerm, GlossaryAlias
from apps.summaries.services.gemini import make_client, gemini_generate_with_retry
from apps.glossary.management.commands.extract_glossary_terms import (
    _collect_content, SYSTEM_PROMPT, EXTRACT_MODEL,
)

# 原本沿用 extract_glossary_terms.py 的完整prompt(每個術語都要求long_definition長定義)，
# 30集一批送出去，實測連batch=12+max_output_tokens=32000都還是被截斷——問題不是
# 批次大小不夠小，是「每個術語都要生成完整長定義」本身就讓單次回應太長。這裡只是要
# 先掃出候選清單給人工審核，不需要在這個階段就把長定義寫好，改成只要term/
# short_definition/category/aliases，完整長定義留到候選確認要收錄之後才生成，
# 輸出量小很多，batch也可以回到接近原本的大小。
CANDIDATE_PROMPT_TEMPLATE = """以下是多集 Podcast 投資節目的摘要內容（pro 模式），請從中萃取所有財經投資專有名詞。

【萃取規則】
- 只收「詞典等級」的通用術語：拿掉上下文、單獨看這個詞，也有一個穩定、不隨集數改變的定義，
  值得被收進財經辭典查詢。判斷方法：如果只要知道組成這個詞的幾個常用字/詞各自的意思，
  就能猜出整個詞在說什麼，這種「描述性詞組」不算術語，不要收。
- 不收：公司名稱（台積電、輝達）、人名（鮑爾）、國家地名
- 不收：一般用語（行情、股價、買賣），只收有特定含義的術語
- 不收：描述性詞組/情境化的動詞+名詞組合，這種詞組每集都可能用不同字眼講同一件事，
  不是穩定的專有名詞。例如「拋售現象」「獲利部位」「議價空間」「美債避險功能」
  「盤中跌幅收斂」「經濟增長預估」「現金彈性」「多元化配置」「基金出清」都不該收
  （這些都只是把常用字組合描述一個情境，不是需要專門查閱定義的術語）
- 收：有公認名稱、需要專門解釋才懂的具體概念/比率/指標/技術/商品/法規/事件，例如
  「本益比」「EPS」「HBM」「法說會」「縮表」「摩爾定律」「Dot-com泡沫」「量化寬鬆」
- 若同一術語有多個常見說法（如「本益比」=「P/E」），都列為 aliases

【輸出格式】只輸出 JSON 陣列（不要 ```json），每筆格式：
[
  {{
    "term": "本益比",
    "short_definition": "股價除以每股盈餘，用來評估股票是否貴或便宜（50字以內）",
    "category": "股票分析",
    "aliases": ["P/E", "P/E ratio"]
  }}
]

category 只能從以下選擇：
  總體經濟 / 股票分析 / 技術分析 / 產業分析 / 衍生品 / 基本面分析 / 政策法規 / 其他

不要輸出long_definition，只要上面4個欄位，保持輸出精簡。

【摘要內容】
{content}
"""

BATCH_SIZE = 10


def _extract_one_batch(client, batch, batch_no, n_batches):
    """處理單一批次，回傳(batch_no, terms或None, 錯誤訊息或None)。給ThreadPoolExecutor平行呼叫用。"""
    content = _collect_content(batch)
    if not content.strip():
        return batch_no, [], None
    prompt = f"{SYSTEM_PROMPT}\n\n{CANDIDATE_PROMPT_TEMPLATE.format(content=content)}"
    try:
        resp = gemini_generate_with_retry(
            client=client, model=EXTRACT_MODEL, prompt_text=prompt,
            temperature=0.1, max_output_tokens=32000, max_tries=3,
        )
        raw = (getattr(resp, "text", "") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        terms = json.loads(raw.strip())
        if not isinstance(terms, list):
            raise ValueError("回傳不是陣列")
        return batch_no, terms, None
    except Exception as e:
        return batch_no, None, str(e)


def run_extraction(max_workers: int = 4):
    """
    跑完全部批次，回傳所有批次萃取到的原始term dict列表(還沒去重)。

    原本是單執行緒逐批呼叫+批次間睡5秒，270批跑下來要4-5小時。這裡只是單純的
    候選詞彙掃描(不寫資料庫、批次間也沒有互相依賴)，改成跟filter_keydata.py/
    reclassify_topics.py一樣用ThreadPoolExecutor平行送出多個API請求，把時間壓
    到1-1.5小時內，涵蓋範圍不變(還是全部2693筆pro摘要)。
    """
    client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
    records = list(
        SummaryRecord.objects.using("summariesdb")
        .filter(mode="pro")
        .exclude(arguments=[])
        .order_by("id")
    )
    total = len(records)
    batches = [records[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    n_batches = len(batches)
    print(f"共 {total} 筆pro摘要，每批{BATCH_SIZE}筆，共{n_batches}批，平行數{max_workers}")

    all_terms = []
    n_failed = 0
    done = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_extract_one_batch, client, batch, i + 1, n_batches): i + 1
            for i, batch in enumerate(batches)
        }
        for fut in as_completed(futures):
            batch_no = futures[fut]
            done += 1
            batch_no_ret, terms, err = fut.result()
            elapsed = time.time() - t0
            if err is not None:
                n_failed += 1
                print(f"  [批次{batch_no_ret}/{n_batches}] 失敗: {err} ({done}/{n_batches}完成，{elapsed:.0f}s)")
                continue
            all_terms.extend(terms)
            print(f"  [批次{batch_no_ret}/{n_batches}] 萃取到{len(terms)}個詞彙 "
                  f"(累計{len(all_terms)}筆原始資料，{done}/{n_batches}完成，失敗{n_failed}批，{elapsed:.0f}s)")

    return all_terms


def dedupe_terms(raw_terms: list) -> dict:
    """
    同一個term字串(去除前後空白)在多個批次都出現時合併成一筆：
    取short_definition字數最長的那個版本當代表，aliases取聯集。
    回傳 {term_str: {short_definition, category, aliases, seen_count}}

    這個階段不含long_definition(完整長定義留到候選確認要收錄後才生成，
    見CANDIDATE_PROMPT_TEMPLATE的設計理由)。
    """
    merged = {}
    for t in raw_terms:
        term_str = (t.get("term") or "").strip()
        if not term_str:
            continue
        short_def = (t.get("short_definition") or "").strip()
        category = (t.get("category") or "其他").strip()
        aliases = set(a.strip() for a in (t.get("aliases") or []) if a.strip())

        if term_str not in merged:
            merged[term_str] = {
                "term": term_str, "short_definition": short_def,
                "category": category, "aliases": aliases, "seen_count": 1,
            }
        else:
            existing = merged[term_str]
            existing["seen_count"] += 1
            existing["aliases"] |= aliases
            if len(short_def) > len(existing["short_definition"]):
                existing["short_definition"] = short_def
                existing["category"] = category

    return merged


def classify_new_vs_update(merged: dict) -> tuple[list, list, list]:
    """
    對照現有GlossaryTerm，分成三類：
      new_candidates: 資料庫裡完全沒有這個term(也不是任何既有term的alias)
      update_candidates: 資料庫已有這個term，但這次萃取到的short_definition
        文字跟既有的short_definition不同，可能代表既有定義該補充或修正
      unchanged: 資料庫已有且short_definition文字一樣(或這個term是既有term的alias)，
        不需要人工看
    """
    existing_terms = {t.term: t for t in GlossaryTerm.objects.all()}
    existing_aliases = {a.alias: a.term.term for a in GlossaryAlias.objects.select_related("term").all()}

    new_candidates, update_candidates, unchanged = [], [], []
    for term_str, data in merged.items():
        data["aliases"] = sorted(data["aliases"])
        if term_str in existing_terms:
            existing = existing_terms[term_str]
            if (existing.short_definition or "").strip() == data["short_definition"]:
                unchanged.append(data)
            else:
                data["existing_short_definition"] = existing.short_definition
                data["existing_category"] = existing.category
                update_candidates.append(data)
        elif term_str in existing_aliases:
            data["matched_as_alias_of"] = existing_aliases[term_str]
            unchanged.append(data)
        else:
            new_candidates.append(data)

    return new_candidates, update_candidates, unchanged


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="輸出候選清單的JSON檔路徑")
    args = parser.parse_args()

    print("=" * 70)
    print("STEP 1：跑全部批次萃取(不寫入資料庫)")
    print("=" * 70)
    raw_terms = run_extraction()
    print(f"\n全部批次完成，原始萃取{len(raw_terms)}筆(含重複)")

    print()
    print("=" * 70)
    print("STEP 2：去重、合併同一個術語跨批次的多次萃取結果")
    print("=" * 70)
    merged = dedupe_terms(raw_terms)
    print(f"去重後：{len(merged)} 個不重複的術語")

    print()
    print("=" * 70)
    print("STEP 3：對照現有GlossaryTerm資料庫，分類新增/更新/不變")
    print("=" * 70)
    new_candidates, update_candidates, unchanged = classify_new_vs_update(merged)
    print(f"全新術語(資料庫裡沒有)：{len(new_candidates)} 筆")
    print(f"既有術語但定義文字不同(可能要更新)：{len(update_candidates)} 筆")
    print(f"既有術語且定義文字相同(不需要看)：{len(unchanged)} 筆")

    # 第二層篩選：全新術語裡只在1批(10集)裡出現過的，比較可能是偶發/情境化的
    # 詞組(不是真正穩定的通用術語)，跟seen_count>=2的分開放，審核時可以先看
    # 信心度較高的那組，數量少很多，seen_count==1的留著備查但不當成優先審核項目。
    new_high_confidence = [c for c in new_candidates if c["seen_count"] >= 2]
    new_low_confidence = [c for c in new_candidates if c["seen_count"] == 1]
    print(f"  其中只出現過1次(seen_count==1，優先度較低)：{len(new_low_confidence)} 筆")
    print(f"  出現2次以上(建議優先審核)：{len(new_high_confidence)} 筆")

    out_data = {
        "new_candidates_high_confidence": new_high_confidence,
        "new_candidates_low_confidence": new_low_confidence,
        "update_candidates": update_candidates,
        "unchanged_count": len(unchanged),
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"\n候選清單已存到: {args.out}")


if __name__ == "__main__":
    main()
