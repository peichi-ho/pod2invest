"""
把小表 TickerMap 的每一筆別名，用外鍵 classification 連到大表 IndustryClassification。

只用 ticker 字串完全相等來對，不猜：對不到的留 null 並列成清單。清單旁邊附上
「可能是」的候選(大小寫不同、.TW/.TWO 後綴不同)，只供人工判斷，不會自動套用。

預設是 dry run(只讀資料庫、只印統計)，加 --commit 才寫入 summariesdb。

用法：
  python3 manage.py link_tickermap_classification
  python3 manage.py link_tickermap_classification --commit
"""
from django.core.management.base import BaseCommand

from apps.summaries.models import IndustryClassification, TickerMap

DB = "summariesdb"


def _candidates(ticker: str, ic_upper: dict[str, str]) -> list[str]:
    upper = ticker.upper()
    variants = {upper}
    if upper.endswith(".TWO"):
        variants.add(upper[:-4] + ".TW")
    elif upper.endswith(".TW"):
        variants.add(upper[:-3] + ".TWO")
    return sorted(ic_upper[v] for v in variants if v in ic_upper and ic_upper[v] != ticker)


class Command(BaseCommand):
    help = "TickerMap.classification 回填：用 ticker 完全比對連到 IndustryClassification(預設 dry run)"

    def add_arguments(self, parser):
        parser.add_argument("--commit", action="store_true", help="實際寫入 summariesdb")

    def handle(self, *args, commit=False, **opts):
        ic_id = dict(IndustryClassification.objects.using(DB).values_list("ticker", "id"))
        ic_upper = {t.upper(): t for t in ic_id}
        # 只取既有欄位：dry run 要能在 migration 還沒套用前執行
        aliases = list(TickerMap.objects.using(DB).values_list("id", "asset_name", "ticker").order_by("ticker"))

        matched = {pk: ic_id[ticker] for pk, _, ticker in aliases if ticker in ic_id}
        unmatched = [(pk, name, ticker) for pk, name, ticker in aliases if ticker not in ic_id]

        self.stdout.write(f"TickerMap 共 {len(aliases)} 筆別名，IndustryClassification 共 {len(ic_id)} 筆")
        self.stdout.write(f"成功對上：{len(matched)} 筆(對到 {len(set(matched.values()))} 檔不同標的)")
        self.stdout.write(f"對不到：{len(unmatched)} 筆")
        for pk, name, ticker in unmatched:
            hint = _candidates(ticker, ic_upper)
            hint_txt = f"  可能是：{', '.join(hint)}" if hint else ""
            self.stdout.write(f"  id={pk}  {name} → {ticker}{hint_txt}")

        # 對上了但目標可疑：大表裡同一個代號同時有 .TW 跟 .TWO 兩列，通常是上櫃轉上市後
        # 舊的 .TWO 列沒清掉。照樣用完全比對連上(不猜)，但列出來給人工確認。
        stale = []
        for pk, name, ticker in aliases:
            if pk in matched and ticker.upper().endswith(".TWO") and ticker[:-4] + ".TW" in ic_id:
                stale.append((pk, name, ticker, ticker[:-4] + ".TW"))
        if stale:
            self.stdout.write(f"對上了但要人工確認(大表同代號另有 .TW 列，可能已轉上市)：{len(stale)} 筆")
            for pk, name, ticker, tw in stale:
                self.stdout.write(f"  id={pk}  {name} → {ticker}(大表另有 {tw})")

        if not commit:
            self.stdout.write(self.style.WARNING("dry run，沒有寫入資料庫。確認後加 --commit。"))
            return

        objs = list(TickerMap.objects.using(DB).filter(id__in=matched))
        changed = [o for o in objs if o.classification_id != matched[o.id]]
        for o in changed:
            o.classification_id = matched[o.id]
        TickerMap.objects.using(DB).bulk_update(changed, ["classification"], batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"已寫入 {len(changed)} 筆(其餘 {len(objs) - len(changed)} 筆原本就對好了)"))
