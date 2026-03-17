import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
SUMMARY_DIR = BASE_DIR


def load_summary_from_file(filename: str):
    path = SUMMARY_DIR / filename

    if not path.exists():
        raise ValueError(f"找不到摘要檔案: {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "title" not in data:
        data["title"] = filename.replace(".json", "")

    return data
