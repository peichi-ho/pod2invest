# DRF serializer（query 驗證）

from rest_framework import serializers

# 用小型清單而不是寫死 if/else 分支，之後要加 us_stock 只要在這裡跟
# api/views.py 的 dispatch dict 各加一行即可。
CATEGORY_CHOICES = ["tw_stock", "tw_etf"]
SORT_CHOICES = ["volume", "price", "change"]
DIRECTION_CHOICES = ["desc", "asc"]


class AssetRankingQuerySerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=CATEGORY_CHOICES)
    sort = serializers.ChoiceField(choices=SORT_CHOICES, default="volume")
    direction = serializers.ChoiceField(choices=DIRECTION_CHOICES, default="desc")
    limit = serializers.IntegerField(default=50, min_value=1, max_value=200)
    offset = serializers.IntegerField(default=0, min_value=0)
    # 只保留這幾檔（逗號分隔，如 "2330,2317"）不排序全市場，「我的最愛」區塊用這個
    # 拿即時價格/成交量，重用跟主列表一樣的排序/分頁邏輯，不用另開一條資料路徑。
    symbols = serializers.CharField(required=False, allow_blank=True, default="")
    # 搜尋欄位用：代碼或名稱的子字串比對（不分大小寫），在目前分類的完整清單裡找，
    # 不是只在畫面上已經看到的前50筆裡找。
    q = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_symbols(self, v: str):
        return [s.strip().upper() for s in v.split(",") if s.strip()]

    def validate_q(self, v: str):
        return v.strip()


class AssetBasicInfoQuerySerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=CATEGORY_CHOICES)
    symbol = serializers.CharField()

    def validate_symbol(self, v: str):
        return v.strip().upper()
