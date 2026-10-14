# scripts/reclassify_topics.py
"""
針對資料庫裡「已經存在、topic命名不符合新規則」的舊資料，逐筆重新分類topic名稱
——不重新呼叫整份摘要生成(不動summary/position/key_data/evidence_timestamps等內容)，
只更新topic這個欄位，把成本/風險降到最低。

背景：topic命名規則已經在 prompts.py/chunking.py/enrich.py 修正過(2026-09-30)，
之後新產生的摘要不會再出現「公司分析：台積電」「金融市場」這類不合規範的標題。但修正
只對之後新產生的摘要有效，舊資料(2620/5391筆，48.6%)還是亂命名，需要這支腳本補救。

合法格式(5種，postprocess.py的_is_recognized_topic()同步驗證)：
  「總體經濟環境」「操作策略與建議」「風險提示」(固定三個)
  「個股：[名稱]」「ETF：[名稱]」「產業：[名稱]」(帶名稱的三種，含固定的算6種...不對，共5種)

同一集節目的pro/novice兩筆SummaryRecord，topic字串設計上必須一致(novice被鎖定
要跟pro用同一批標題)，所以用(episode_id, 原始topic字串)當key去重——同一組合只呼叫
一次LLM分類，兩筆記錄套用同一個新topic，避免pro/novice被分類成不同結果、也節省一半的
API呼叫量。

用法：
  python scripts/reclassify_topics.py --mode trial   # 只抽樣25~30筆分類，印出來看，不寫DB
  python scripts/reclassify_topics.py --mode full     # 正式全部跑，會真的寫入DB(尚未實作，先用trial)
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
from apps.summaries.services.postprocess import _is_recognized_topic
from apps.summaries.services.gemini import make_client, gemini_generate_with_retry, sanitize_json_text

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

CLASSIFY_PROMPT = """你要把一個Podcast摘要裡「命名不合規範的主題標題」，重新分類成正確格式。

合法格式只有以下5種，只能選其中一種，不可自創其他格式：
1. 「總體經濟環境」— 整體大盤/指數/台股/美股/港股等非單一公司的市場走勢討論
2. 「操作策略與建議」— 不涉及特定標的的交易心法、資產配置、市場生存法則等策略性內容
3. 「風險提示」— 泛用的風險警示，不特指單一公司/ETF/產業
4. 「個股：[名稱]」— 針對單一公司或指數的討論，例如「個股：台積電」
5. 「ETF：[名稱]」— 針對單一ETF的討論，例如「ETF：00961」
6. 「產業：[名稱]」— 針對某個產業主題的討論，例如「產業：記憶體漲價」「產業：AI伺服器需求」

【原本(不合規範)的標題】
{old_topic}

【這個主題底下的摘要內容】
{summary}

【這個主題底下的結論句】
{position}

請判斷這段內容真正在討論的是上面6種格式的哪一種，並給出正確格式的新標題。
如果是「個股：」「ETF：」「產業：」這三種，[名稱]要根據內容判斷出具體、簡潔的名稱
(例如公司全名或常用簡稱、ETF代號或常用名稱、產業主題的簡短描述)。

只輸出這個JSON格式，不要其他文字：
{{"new_topic": "分類後的新標題"}}
"""


def scan_bad_topics():
    """回傳 [{summary_id, mode, episode_id, arg_index, old_topic, summary, position}, ...]"""
    bad = []
    qs = (
        SummaryRecord.objects.using("summariesdb")
        .only("id", "mode", "episode_id", "arguments")
        .iterator(chunk_size=200)
    )
    for rec in qs:
        for idx, a in enumerate(rec.arguments or []):
            if not isinstance(a, dict):
                continue
            topic = a.get("topic", "")
            if topic and not _is_recognized_topic(topic):
                bad.append({
                    "summary_id": rec.id,
                    "mode": rec.mode,
                    "episode_id": rec.episode_id,
                    "arg_index": idx,
                    "old_topic": topic,
                    "summary": a.get("summary", ""),
                    "position": a.get("position", ""),
                })
    return bad


def bucket_pattern(old_topic: str) -> str:
    if old_topic.startswith("公司分析"):
        return "公司分析：前綴錯誤(應為個股：)"
    if old_topic.startswith("ETF："):
        return "ETF(舊資料，需併入新ETF格式)"
    if old_topic in ("產業分析",):
        return "產業分析(裸詞，無具體產業名稱)"
    if old_topic in ("金融市場", "投資策略", "投資觀點", "科技趨勢", "政策與地緣政治"):
        return "泛用總經/策略類自創標題"
    return "其他自創/細碎標題"


def pick_trial_sample(bad_topics, n_per_bucket=6, seed=42):
    buckets = {}
    for item in bad_topics:
        b = bucket_pattern(item["old_topic"])
        buckets.setdefault(b, []).append(item)
    rng = random.Random(seed)
    sample = []
    for b, items in buckets.items():
        rng.shuffle(items)
        sample.extend(items[:n_per_bucket])
    return sample


def classify_one(client, old_topic, summary, position):
    prompt = CLASSIFY_PROMPT.format(old_topic=old_topic, summary=summary[:500], position=position[:300])
    resp = gemini_generate_with_retry(
        client=client, model=MODEL, prompt_text=prompt,
        temperature=0.0, max_output_tokens=100, max_tries=3,
    )
    obj = json.loads(sanitize_json_text(getattr(resp, "text", "") or ""))
    return str(obj.get("new_topic", "")).strip()


def classify_all_unique(unique_keys: dict, max_workers: int = 8):
    """
    對每個(episode_id, old_topic)組合各呼叫一次LLM分類，平行處理加速。
    回傳 (classification_map, failures)：
      classification_map: {(episode_id, old_topic): new_topic}，只收分類成功且格式合法的
      failures: [((episode_id, old_topic), 原因), ...]，分類失敗或回傳格式不合法的都算失敗，
                失敗的topic維持原樣不會被更新，不會硬套一個可能錯的分類上去。
    """
    client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
    keys = list(unique_keys.keys())
    classification_map = {}
    failures = []
    done = 0
    t0 = time.time()

    def _work(key):
        episode_id, old_topic = key
        sample_item = unique_keys[key][0]  # 同一個key底下的任何一筆內容都一樣(來自同一集同一個topic)
        new_topic = classify_one(client, old_topic, sample_item["summary"], sample_item["position"])
        return key, new_topic

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_work, key): key for key in keys}
        for fut in as_completed(futures):
            key = futures[fut]
            done += 1
            try:
                _, new_topic = fut.result()
                if _is_recognized_topic(new_topic):
                    classification_map[key] = new_topic
                else:
                    failures.append((key, f"分類結果格式不合法: {new_topic!r}"))
            except Exception as e:
                failures.append((key, f"呼叫失敗: {e}"))
            if done % 100 == 0 or done == len(keys):
                elapsed = time.time() - t0
                print(f"  進度 {done}/{len(keys)} ({elapsed:.0f}s，失敗{len(failures)}筆)")

    return classification_map, failures


def apply_updates(bad_topics: list, classification_map: dict):
    """
    依summary_id分組，每筆SummaryRecord只重新讀取+存檔一次(即使裡面有多個topic要改)，
    避免同一筆記錄被反覆讀取/覆寫造成競爭寫入或效能浪費。只更新topic欄位本身，
    summary/position/key_data/evidence_timestamps等其他內容完全不動。
    """
    by_summary = {}
    for item in bad_topics:
        key = (item["episode_id"], item["old_topic"])
        if key not in classification_map:
            continue  # 分類失敗的，維持原樣不更新
        by_summary.setdefault(item["summary_id"], []).append((item["arg_index"], classification_map[key]))

    updated_records = 0
    updated_topics = 0
    for summary_id, changes in by_summary.items():
        rec = SummaryRecord.objects.using("summariesdb").get(id=summary_id)
        args = rec.arguments or []
        changed = False
        for arg_index, new_topic in changes:
            if arg_index < len(args) and isinstance(args[arg_index], dict):
                args[arg_index]["topic"] = new_topic
                changed = True
                updated_topics += 1
        if changed:
            rec.arguments = args
            rec.save(using="summariesdb", update_fields=["arguments"])
            updated_records += 1

    return updated_records, updated_topics


def run_full_once(bad_topics, unique_keys, max_workers):
    """跑一輪分類+寫入，回傳 (updated_records, updated_topics, n_failures)。"""
    classification_map, failures = classify_all_unique(unique_keys, max_workers=max_workers)
    print(f"\n分類完成：成功 {len(classification_map)} 筆，失敗 {len(failures)} 筆")
    if failures:
        print("失敗清單(前20筆，維持原topic不變):")
        for key, reason in failures[:20]:
            print(f"  episode_id={key[0]} old_topic={key[1]!r}  原因: {reason}")

    updated_records, updated_topics = apply_updates(bad_topics, classification_map)
    print(f"寫入完成：更新了 {updated_records} 筆SummaryRecord，共 {updated_topics} 個topic欄位")
    return updated_records, updated_topics, len(failures)


def run_full_until_clean(max_rounds=8):
    """
    重複「掃描→分類→寫入」直到沒有剩餘的不合規範topic，或連續一輪完全沒進展就停止
    (避免對真的卡住、不是流量限制造成的失敗案例無限重跑)。每一輪都重新掃描資料庫，
    所以會自動只處理「上一輪還沒修好」的部分，已經修好的不會重複呼叫LLM。
    流量限制(429)是上一輪失敗的主因，所以並行數逐輪調低、輪與輪之間加上緩衝時間。
    """
    workers_schedule = [8, 5, 3, 2, 1, 1, 1, 1]
    for round_num in range(1, max_rounds + 1):
        print()
        print("#" * 70)
        print(f"第 {round_num} 輪")
        print("#" * 70)

        bad_topics = scan_bad_topics()
        if not bad_topics:
            print("沒有剩餘的不合規範topic了，全部完成。")
            return

        unique_keys = {}
        for item in bad_topics:
            key = (item["episode_id"], item["old_topic"])
            unique_keys.setdefault(key, []).append(item)
        print(f"剩餘 {len(bad_topics)} 筆不合規範topic，去重後 {len(unique_keys)} 個組合需要分類")

        workers = workers_schedule[min(round_num - 1, len(workers_schedule) - 1)]
        print(f"這一輪並行數：{workers}")
        _, updated_topics, n_failures = run_full_once(bad_topics, unique_keys, max_workers=workers)

        if updated_topics == 0:
            print("這一輪完全沒有任何topic被成功更新，可能不是流量限制造成的問題，停止重跑。")
            print(f"還剩 {len(bad_topics)} 筆需要人工檢查。")
            return

        if round_num < max_rounds:
            print("暫停60秒讓API流量額度回復，再進行下一輪...")
            time.sleep(60)

    remaining = len(scan_bad_topics())
    if remaining:
        print(f"\n已達最大重跑輪數({max_rounds}輪)，仍有 {remaining} 筆topic未修好，建議人工檢查。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["trial", "full", "full-until-clean"], default="trial")
    args = parser.parse_args()

    if args.mode == "full-until-clean":
        run_full_until_clean()
        return

    print("=" * 70)
    print("STEP 1：掃描資料庫，找出所有不合規範的topic")
    print("=" * 70)
    bad_topics = scan_bad_topics()
    print(f"總共找到 {len(bad_topics)} 筆不合規範的topic紀錄")

    # 用(episode_id, old_topic)去重——同一集pro/novice共用同一個分類結果
    unique_keys = {}
    for item in bad_topics:
        key = (item["episode_id"], item["old_topic"])
        unique_keys.setdefault(key, []).append(item)
    print(f"去重後(依episode_id+原始topic)：{len(unique_keys)} 個不同的(集數,標題)組合需要分類")

    if args.mode == "trial":
        print()
        print("=" * 70)
        print("STEP 2：抽樣代表性案例，實際呼叫LLM分類(不寫入資料庫)")
        print("=" * 70)
        sample = pick_trial_sample(bad_topics, n_per_bucket=6)
        print(f"抽樣 {len(sample)} 筆進行試跑\n")

        client = make_client(api_key=os.getenv("GEMINI_API_KEY", ""))
        results = []
        for i, item in enumerate(sample):
            try:
                new_topic = classify_one(client, item["old_topic"], item["summary"], item["position"])
            except Exception as e:
                new_topic = f"[分類失敗: {e}]"
            results.append((item, new_topic))
            print(f"[{i+1}/{len(sample)}] ({bucket_pattern(item['old_topic'])})")
            print(f"   舊: {item['old_topic']}")
            print(f"   新: {new_topic}")
            print()

        print("=" * 70)
        print("試跑結果彙整表")
        print("=" * 70)
        print(f"{'原始topic':<30} {'分類後':<20} {'類型'}")
        for item, new_topic in results:
            print(f"{item['old_topic']:<30} {new_topic:<20} {bucket_pattern(item['old_topic'])}")
    else:
        print()
        print("=" * 70)
        print(f"STEP 2：對 {len(unique_keys)} 個(集數,標題)組合平行呼叫LLM分類")
        print("=" * 70)
        classification_map, failures = classify_all_unique(unique_keys, max_workers=8)
        print(f"\n分類完成：成功 {len(classification_map)} 筆，失敗 {len(failures)} 筆")
        if failures:
            print("失敗清單(前20筆，維持原topic不變):")
            for key, reason in failures[:20]:
                print(f"  episode_id={key[0]} old_topic={key[1]!r}  原因: {reason}")

        print()
        print("=" * 70)
        print("STEP 3：把分類結果寫回資料庫(只更新topic欄位)")
        print("=" * 70)
        updated_records, updated_topics = apply_updates(bad_topics, classification_map)
        print(f"完成：更新了 {updated_records} 筆SummaryRecord，共 {updated_topics} 個topic欄位")

        remaining = len(bad_topics) - updated_topics
        if remaining:
            print(f"仍有 {remaining} 筆topic未更新(分類失敗或索引對不上)，topic維持原樣，建議之後再檢查一次")


if __name__ == "__main__":
    main()
