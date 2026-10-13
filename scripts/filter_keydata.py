# scripts/filter_keydata.py
"""
針對資料庫裡已存在的key_data(每個論點下面的數字卡片)，重新審查每一筆是否
「對投資判斷有意義」，把純技術規格數字(晶圓尺寸、封裝尺寸、規格比例等，跟投資
判斷無關的數字)過濾掉——這是回應「產業：半導體封裝」那個論點下面出現「12吋晶圓
尺寸」「500x500mm面板尺寸」這種數字被誤收的問題。

背景：key_data抽取規則已經在 prompts.py/chunking.py/enrich.py 修正過
(2026-09-30)，之後新產生的摘要不會再收這種純技術規格數字。但修正只對之後新產生
的摘要有效，舊資料(28683個論點、70557筆key_data)還是可能混著這種數字，需要這支
腳本補救。

做法：不是逐筆key_data各自呼叫一次LLM(太貴太慢，70557筆)，而是把同一個論點底下
的整組key_data(平均每個論點2.5筆)一起送，並且把好幾個論點打包成一批(預設25個
論點一批)一次呼叫，讓LLM在同一次呼叫裡對所有論點的所有key_data做去留判斷，
回傳「要刪除的項目」清單(預設留下，只講要刪的，輸出精簡)。只刪除不合規範的
key_data項目本身，summary/position/topic/evidence_timestamps等其他內容完全不動。

用法：
  python scripts/filter_keydata.py --mode trial              # 抽樣試跑，不寫入
  python scripts/filter_keydata.py --mode full-until-clean    # 正式全部跑，會寫入DB
"""
import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django
django.setup()

from apps.summaries.models import SummaryRecord
from apps.summaries.services.gemini import make_client, gemini_generate_with_retry, sanitize_json_text

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
# 原本batch=25一次塞太多論點，實測發現同樣內容在不同批次判斷結果會不一致
# (甚至同一集pro/novice幾乎一樣的內容，一次幾乎全留、一次幾乎全刪)，
# 改成每次只審查1個論點，用API呼叫量換一致性，這是刪除資料的操作，值得。
BATCH_ARGS_PER_CALL = 1

FILTER_PROMPT = """你要審查一批Podcast投資摘要裡的「key_data數字卡片」，判斷哪些應該被刪除。

【重要原則】不確定的話一律保留，不要刪除。你只能刪除明確屬於下面「一定要刪除」
清單的項目，除此之外的所有數字都要保留，包括你覺得「不太重要」但不屬於刪除清單的。

【特別強調：整個論點都是「歷史類比／回顧過去某個年代」也不能整組刪除】
如果一個論點的內容是在回顧過去的總經週期(例如「1990年代升息週期回顧」)，裡面的
GDP/CPI/利率/指數歷史數字**依然要保留**，不可以因為「主題是歷史回顧、不是即時
行情」就把整組數字都判定為不重要而刪除——歷史類比正是podcaster拿來佐證現在判斷
的依據，跟即時數據一樣重要，這條規則優先於你自己對「這是背景知識」的直覺判斷。

【一定要保留(絕對不可以刪)，即使看起來只是背景資訊或歷史數據】
1. 總經指標：GDP、CPI/PCE(通膨)、失業率、利率(含各國央行利率、貸款利率、聯邦基金利率)、
   關稅稅率、貿易/進出口數據、匯率——不論是哪一國、哪個年份，包含歷史對照數據
   (例如「1994年聯邦基金利率」「1996年納斯達克指數」這種歷史類比也要保留，
   這是總經脈絡的一部分，不是可有可無的背景資訊)
2. 公司財務數字：營收、獲利、毛利率、營業利益率、EPS、現金流、資本支出、研發支出、
   訂單量、積壓訂單、財測guidance
3. 估值與股價：股價、目標價、本益比、營收倍數(P/S)、市值、殖利率、IPO估值
4. 市場資金流向與部位：外資/投信/自營商買賣超金額、期貨未平倉部位、大盤漲跌點數/幅度、
   個股或指數的漲跌幅、成交量
5. 產業/商業數字：市占率、出貨量、產能、良率、成長率——只要是「生意做得好不好」
   的數字都算，不是只有嚴格的財報數字才算

【只能刪除以下這幾種，範圍很窄】
A. 純物理/技術規格：產品尺寸、晶圓/封裝尺寸、面積、重量、顆數等工程規格，
   跟營收/成本/獲利沒有直接數字連結
B. 產品效能/技術benchmark分數：ELO分數、跑分測試、模型準確率、演算法效能指標，
   這些是技術指標不是財務指標
C. 節目來賓的個人買賣軼事價格：「我當年用XX元買的」這種個人交易回憶，
   不是市場公開報價，也不是分析依據
D. 跟市場/投資完全無關的社會統計：例如行政命令簽署數量、民調支持度百分比

範例：
  ✓ 保留：{{"label":"美國第一季GDP成長率","value":"-0.3%","context":"2022年以來首次陷入負成長"}}(總經指標)
  ✓ 保留：{{"label":"對中國關稅提升","value":"145%","context":"川普政府的關稅措施"}}(關稅屬於總經/貿易數據)
  ✓ 保留：{{"label":"SpaceX 營收對估值倍數","value":"77倍","context":"以過去12個月營收計算的估值"}}(估值指標)
  ✓ 保留：{{"label":"Vertiv 營業利潤率","value":"22.3%","context":""}}(公司財務數字)
  ✓ 保留：{{"label":"外資期貨賣超金額","value":"413億元","context":"今日"}}(市場資金流向)
  ✓ 保留：{{"label":"預估EPS","value":"95元","context":"代表台積電今年每股預計賺95元，比去年大幅成長"}}
  ✗ 刪除：{{"label":"晶圓尺寸","value":"12吋","context":"業界標準晶圓尺寸"}}(純技術規格，沒有財務連結)
  ✗ 刪除：{{"label":"GPT-4o ELO 分數","value":"1667","context":"LLM Arena評分"}}(技術benchmark，非財務指標)
  ✗ 刪除：{{"label":"股價","value":"35塊","context":"曾買過遠傳50張在35塊"}}(個人買賣軼事)
  ✗ 刪除：{{"label":"川普簽署行政命令數量","value":"139條","context":"顯示積極運用行政權力"}}(跟投資無關的政治統計)

以下是{n}個論點，每個論點有一個編號(arg_id)跟底下的key_data清單(每筆前面有編號):

{batch_content}

請針對每個論點，判斷哪些編號的key_data項目屬於上面「一定要刪除」的A/B/C/D其中一類。
只輸出這個JSON格式，不要其他文字，沒有要刪除的論點可以省略：
{{"removals": [{{"arg_id": 0, "remove_indices": [0, 2]}}, {{"arg_id": 3, "remove_indices": [1]}}]}}
"""


def scan_key_data():
    """回傳 [{summary_id, arg_index, topic, summary, key_data}, ...]，只收有key_data的論點。"""
    items = []
    qs = (
        SummaryRecord.objects.using("summariesdb")
        .only("id", "arguments")
        .iterator(chunk_size=200)
    )
    for rec in qs:
        for idx, a in enumerate(rec.arguments or []):
            if not isinstance(a, dict):
                continue
            kd = a.get("key_data") or []
            if kd:
                items.append({
                    "summary_id": rec.id,
                    "arg_index": idx,
                    "topic": a.get("topic", ""),
                    "summary": a.get("summary", ""),
                    "key_data": kd,
                })
    return items


def build_batch_content(batch: list) -> str:
    parts = []
    for i, item in enumerate(batch):
        parts.append(f"[論點 arg_id={i}] topic: {item['topic']}")
        parts.append(f"摘要: {item['summary'][:200]}")
        for j, kd in enumerate(item["key_data"]):
            parts.append(f"  [{j}] {json.dumps(kd, ensure_ascii=False)}")
        parts.append("")
    return "\n".join(parts)


def filter_one_batch(client, batch: list) -> dict:
    """回傳 {batch_local_index: [remove_indices]}"""
    content = build_batch_content(batch)
    prompt = FILTER_PROMPT.format(n=len(batch), batch_content=content)
    resp = gemini_generate_with_retry(
        client=client, model=MODEL, prompt_text=prompt,
        temperature=0.0, max_output_tokens=2000, max_tries=3,
    )
    obj = json.loads(sanitize_json_text(getattr(resp, "text", "") or ""))
    result = {}
    for r in obj.get("removals", []):
        arg_id = r.get("arg_id")
        indices = r.get("remove_indices", [])
        if isinstance(arg_id, int) and isinstance(indices, list):
            result[arg_id] = [i for i in indices if isinstance(i, int)]
    return result


def make_batches(items: list, batch_size: int):
    for i in range(0, len(items), batch_size):
        yield items[i:i + batch_size]


def filter_all(items: list, max_workers: int = 6, flush_every: int = 200):
    """
    平行處理所有批次，回傳 (removals_by_item, failed_items)：
      removals_by_item: {(summary_id, arg_index): [要刪除的key_data原始index]}
        (最後一批還沒flush的部分，數量 < flush_every)
      failed_items: 呼叫失敗的批次，攤平回原始item列表(不是batch)，方便下一輪只重跑

    每累積 flush_every 筆結果就呼叫一次 apply_removals() 立刻寫入資料庫，不是
    整輪(可能上萬筆)跑完才一次寫入——實測發現長時間跑批次偶爾會遇到某次API呼叫
    卡住沒有回應也沒有報錯(不會觸發retry，因為根本沒有拋出例外)，如果只在整輪
    結束後才寫入，前面幾小時已經分類好的結果會因為卡住而完全流失，改成邊跑邊
    定期寫入，把可能流失的進度上限控制在 flush_every 筆以內。
    """
    client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
    batches = list(make_batches(items, BATCH_ARGS_PER_CALL))
    removals_by_item = {}
    failed_items = []
    n_failed_batches = 0
    done = 0
    total_updated_records = 0
    total_removed_items = 0
    t0 = time.time()

    def _work(batch):
        return batch, filter_one_batch(client, batch)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, batch): batch for batch in batches}
        for fut in as_completed(futures):
            batch = futures[fut]
            done += 1
            try:
                _, result = fut.result()
                for local_idx, remove_indices in result.items():
                    if 0 <= local_idx < len(batch) and remove_indices:
                        item = batch[local_idx]
                        key = (item["summary_id"], item["arg_index"])
                        removals_by_item[key] = remove_indices
            except Exception as e:
                n_failed_batches += 1
                failed_items.extend(batch)

            if len(removals_by_item) >= flush_every:
                ur, ri = apply_removals(removals_by_item)
                total_updated_records += ur
                total_removed_items += ri
                removals_by_item = {}

            if done % 20 == 0 or done == len(batches):
                elapsed = time.time() - t0
                print(f"  進度 {done}/{len(batches)} 批 ({elapsed:.0f}s，失敗{n_failed_batches}批，"
                      f"已寫入{total_updated_records}筆記錄/{total_removed_items}筆key_data)")

    # 收尾：把最後一批不足flush_every的殘餘結果也寫入
    if removals_by_item:
        ur, ri = apply_removals(removals_by_item)
        total_updated_records += ur
        total_removed_items += ri
        removals_by_item = {}

    print(f"  這一輪總計寫入：{total_updated_records}筆記錄，{total_removed_items}筆key_data")
    return removals_by_item, failed_items


def apply_removals(removals_by_item: dict):
    """依summary_id分組，每筆記錄只讀取+存檔一次。只刪除key_data陣列裡指定index的
    項目，summary/position/topic/evidence_timestamps等其他內容完全不動。"""
    by_summary = {}
    for (summary_id, arg_index), remove_indices in removals_by_item.items():
        by_summary.setdefault(summary_id, []).append((arg_index, remove_indices))

    updated_records = 0
    removed_items = 0
    for summary_id, changes in by_summary.items():
        rec = SummaryRecord.objects.using("summariesdb").get(id=summary_id)
        args = rec.arguments or []
        changed = False
        for arg_index, remove_indices in changes:
            if arg_index >= len(args) or not isinstance(args[arg_index], dict):
                continue
            kd = args[arg_index].get("key_data") or []
            remove_set = set(remove_indices)
            new_kd = [item for i, item in enumerate(kd) if i not in remove_set]
            if len(new_kd) != len(kd):
                removed_items += len(kd) - len(new_kd)
                args[arg_index]["key_data"] = new_kd
                changed = True
        if changed:
            rec.arguments = args
            rec.save(using="summariesdb", update_fields=["arguments"])
            updated_records += 1

    return updated_records, removed_items


def pick_trial_sample(items: list, n: int = 20, seed: int = 99):
    # 優先挑key_data筆數多的論點(比較可能混雜技術規格數字，試跑更有代表性)
    sorted_items = sorted(items, key=lambda x: -len(x["key_data"]))
    top_pool = sorted_items[: max(n * 3, 60)]
    rng = random.Random(seed)
    rng.shuffle(top_pool)
    return top_pool[:n]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["trial", "full-until-clean"], default="trial")
    parser.add_argument("--max-rounds", type=int, default=6)
    args = parser.parse_args()

    print("=" * 70)
    print("STEP 1：掃描資料庫，收集所有有key_data的論點")
    print("=" * 70)
    items = scan_key_data()
    total_kd = sum(len(i["key_data"]) for i in items)
    print(f"共 {len(items)} 個論點有key_data，總計 {total_kd} 筆key_data項目")

    if args.mode == "trial":
        sample = pick_trial_sample(items, n=20)
        print(f"\n抽樣 {len(sample)} 個論點(優先挑key_data較多的)進行試跑，共{sum(len(s['key_data']) for s in sample)}筆key_data\n")

        client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
        batches = list(make_batches(sample, BATCH_ARGS_PER_CALL))
        all_removals = {}
        for batch in batches:
            result = filter_one_batch(client, batch)
            for local_idx, remove_indices in result.items():
                if 0 <= local_idx < len(batch):
                    item = batch[local_idx]
                    all_removals[(item["summary_id"], item["arg_index"])] = remove_indices

        for item in sample:
            key = (item["summary_id"], item["arg_index"])
            remove_indices = all_removals.get(key, [])
            print(f"[summary_id={item['summary_id']} topic={item['topic']!r}]")
            for i, kd in enumerate(item["key_data"]):
                mark = "❌ 刪除" if i in remove_indices else "✅ 保留"
                print(f"   {mark}  {kd.get('label','')}: {kd.get('value','')}  ({kd.get('context','')[:40]})")
            print()
    else:
        workers_schedule = [6, 4, 3, 2, 1, 1]
        round_items = items
        for round_num in range(1, args.max_rounds + 1):
            print()
            print("#" * 70)
            print(f"第 {round_num} 輪")
            print("#" * 70)
            if not round_items:
                print("沒有剩餘項目了。")
                break
            total_kd = sum(len(i["key_data"]) for i in round_items)
            print(f"這一輪：{len(round_items)} 個論點，{total_kd} 筆key_data")

            workers = workers_schedule[min(round_num - 1, len(workers_schedule) - 1)]
            # filter_all內部已經邊跑邊每200筆flush一次寫入資料庫了，這裡拿到的
            # removals_by_item只是最後不足200筆的殘餘(已經在filter_all內flush過)，
            # 不用再呼叫apply_removals
            removals_by_item, failed_items = filter_all(round_items, max_workers=workers)
            print(f"這一輪分類完成，失敗 {len(failed_items)} 個論點(整批算失敗，下一輪重跑)")

            if not failed_items:
                print("這一輪沒有任何批次失敗，完成。")
                break

            round_items = failed_items  # 下一輪只重跑失敗的部分，不重新掃描全部
            if round_num < args.max_rounds:
                print(f"還有 {len(failed_items)} 個論點因批次失敗需要重跑(通常是API流量限制)，暫停60秒後重跑...")
                time.sleep(60)
            else:
                print(f"已達最大重跑輪數，仍有 {len(failed_items)} 個論點未處理，建議之後再檢查。")


if __name__ == "__main__":
    main()
