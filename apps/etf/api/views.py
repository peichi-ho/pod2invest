#DRF API endpoint
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .serializers import ETFCompareQuerySerializer
from ..services.compare import compare_etfs


class ETFCompareAPIView(APIView):
    """
    GET /api/etf/compare?symbols=0050,00878,00919
    """
    def get(self, request):
        ser = ETFCompareQuerySerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)

        symbols = ser.validated_data["symbols"]
        result = compare_etfs(symbols)

        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)