# DRF API endpoints
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import AssetRankingQuerySerializer, AssetBasicInfoQuerySerializer
from ..services import tw_stock_rankings, tw_etf_rankings, basic_info

# category → provider function 的 dispatch table，之後加 us_stock 只要多註冊一行，
# 不用改 view 裡的分支邏輯。
RANKING_PROVIDERS = {
    "tw_stock": tw_stock_rankings.get_tw_stock_rankings,
    "tw_etf": tw_etf_rankings.get_tw_etf_rankings,
}

BASIC_INFO_PROVIDERS = {
    "tw_stock": basic_info.get_tw_stock_basic_info,
    "tw_etf": basic_info.get_tw_etf_basic_info,
}


class AssetRankingAPIView(APIView):
    """
    GET /api/assets/rankings/?category=tw_stock|tw_etf&sort=volume|price|change&direction=desc|asc&limit=50&offset=0

    漲幅／跌幅不開兩個獨立 sort key：都是 sort=change，
    direction=desc（漲最多排前面）＝漲幅榜，direction=asc（跌最多排前面）＝跌幅榜。
    """
    def get(self, request):
        ser = AssetRankingQuerySerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        params = ser.validated_data

        provider = RANKING_PROVIDERS[params["category"]]
        try:
            result = provider(
                sort=params["sort"],
                direction=params["direction"],
                limit=params["limit"],
                offset=params["offset"],
                symbols=params["symbols"] or None,
                q=params["q"] or None,
            )
        except Exception as e:
            return Response({"error": f"取得排名資料失敗：{e}"}, status=status.HTTP_502_BAD_GATEWAY)

        return Response({
            "category": params["category"],
            "sort": params["sort"],
            "direction": params["direction"],
            **result,
        })


class AssetBasicInfoAPIView(APIView):
    """GET /api/assets/basic-info/?category=tw_stock|tw_etf&symbol=2330"""
    def get(self, request):
        ser = AssetBasicInfoQuerySerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        params = ser.validated_data

        provider = BASIC_INFO_PROVIDERS[params["category"]]
        try:
            result = provider(params["symbol"])
        except Exception as e:
            return Response({"error": f"取得基本資料失敗：{e}"}, status=status.HTTP_502_BAD_GATEWAY)

        if result is None:
            return Response({"error": f"找不到 {params['symbol']} 的資料"}, status=status.HTTP_404_NOT_FOUND)

        return Response(result)
