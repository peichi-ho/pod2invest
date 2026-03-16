from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json

from .services.markdown_builder import mindmap_json_to_markdown
from .services.gemini_mindmap import generate_mindmap_json
from .services.load_summary import load_summary_from_file


def health_check(request):
    return JsonResponse({"message": "mindmap api is working"})


@csrf_exempt
def generate_mindmap(request):

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    summary_data = body.get("summary_data")

    if not summary_data:
        return JsonResponse({"error": "summary_data is required"}, status=400)

    try:
        mindmap_json = generate_mindmap_json(summary_data)
        markdown = mindmap_json_to_markdown(mindmap_json)

        return JsonResponse({
            "mindmap_json": mindmap_json,
            "markdown": markdown
        },
                            json_dumps_params={"ensure_ascii": False})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@csrf_exempt
def generate_from_file(request):

    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    filename = body.get("filename")

    if not filename:
        return JsonResponse({"error": "filename required"}, status=400)

    try:
        summary_data = load_summary_from_file(filename)

        mindmap_json = generate_mindmap_json(summary_data)
        markdown = mindmap_json_to_markdown(mindmap_json)

        return JsonResponse({
            "mindmap_json": mindmap_json,
            "markdown": markdown
        },
                            json_dumps_params={"ensure_ascii": False})

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
