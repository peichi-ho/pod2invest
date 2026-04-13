# apps/summaries/serializers.py
from rest_framework import serializers


class SummarizeRequestSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=["novice", "pro", "both"], default="novice")
    model = serializers.CharField(default="models/gemini-2.5-flash-lite", required=False)
    chunk_threshold_chars = serializers.IntegerField(default=20000, required=False)
    debug_chars = serializers.IntegerField(default=0, required=False)

    srt_text = serializers.CharField(required=False, allow_blank=False)
    srt_file = serializers.FileField(required=False)

    def validate(self, attrs):
        srt_text = attrs.get("srt_text")
        srt_file = attrs.get("srt_file")

        if not srt_text and not srt_file:
            raise serializers.ValidationError("請提供 srt_text 或 srt_file。")

        if srt_file:
            name = srt_file.name.lower()
            if not name.endswith(".srt"):
                raise serializers.ValidationError("上傳檔案必須是 .srt。")

        return attrs


class GenerateFromPodcastSerializer(serializers.Serializer):
    podcast_id = serializers.IntegerField()
    mode = serializers.ChoiceField(choices=["novice", "pro", "both"], default="novice")
    model = serializers.CharField(default="models/gemini-2.5-flash-lite", required=False)
    chunk_threshold_chars = serializers.IntegerField(default=30000, required=False)
