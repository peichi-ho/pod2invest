# DRF serializer (query+response)

from rest_framework import serializers

class ETFCompareQuerySerializer(serializers.Serializer):
    # 例如：?symbols=0050,00878,00919
    symbols = serializers.CharField()

    def validate_symbols(self, v: str):
        parts = [x.strip().upper() for x in v.split(",") if x.strip()]
        if not parts:
            raise serializers.ValidationError("symbols is required")
        if len(parts) > 20:
            raise serializers.ValidationError("too many symbols (max 20)")
        return parts