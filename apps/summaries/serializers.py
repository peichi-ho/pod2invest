from rest_framework import serializers

class GenerateSummarySerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=["novice", "pro", "both"])
    srt_text = serializers.CharField()
    model = serializers.CharField(required=False, default="models/gemini-2.5-flash-lite")
    chunk_threshold_chars = serializers.IntegerField(required=False, default=30000)