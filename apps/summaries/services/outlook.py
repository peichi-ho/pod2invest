import json
from pathlib import Path
from typing import Optional

from google import genai

from .gemini import gemini_generate_with_retry, sanitize_json_text, repair_to_valid_json
from .postprocess import strict_filter_outlook_calls


def extract_outlook_calls(
    client: genai.Client,
    model: str,
    inline_text: str,
    raw_save_path: Optional[Path] = None,
) -> list[dict]:
    def append_raw(title: str, content: str):
        if not raw_save_path:
            return
        with open(raw_save_path, "a", encoding="utf-8") as f:
            f.write(f"{title}\n{content}\n\n")

    prompt = (
        "你是一個專門抽取投資節目中『股票未來方向性判斷』的資訊抽取器。\n"
        "你現在只做一件事：抽取 outlook_calls。\n\n"
        "硬性規則：\n"
        "1) 只能輸出合法 JSON，不要 ```json，不要任何解釋\n"
        "2) 請輸出格式：\n"
        "{\n"
        '  "outlook_calls": [\n'
        '    {\n'
        '      "asset": "台積電",\n'
        '      "direction": "bullish",\n'
        '      "timeframe": "2026(短中期)",\n'
        '      "evidence_timestamps": ["5:53", "7:38"],\n'
        '      "evidence_quote": "2026年目標價上看"\n'
        "    }\n"
        "  ]\n"
        "}\n\n"
        "3) 只收『未來』語氣：\n"
        "   例如：未來、明年、下一季、接下來、預估、有機會、會來到、將、目標價、上看、下看\n"
        "   過去回顧或已經發生的事一律不要放\n"
        "4) asset 必須是『可交易股票』：\n"
        "   只接受個股名稱、股票代號、美股 ticker\n"
        "   不得使用產業泛稱，如 AI、半導體、科技股、台股、美股七雄\n"
        "5) direction 只能是 bullish 或 bearish\n"
        "6) timeframe 要透過前後文去判斷，有就填，沒有可為 null\n"
        "7) evidence_quote 最多 25 字，避免雙引號\n"
        "8) 如果沒有符合條件，輸出 {\"outlook_calls\": []}\n"
        "9) timeframe 規則：必須包含括號標註週期：(短期) 指一季內、(短中期) 指一年內、(中期) 指 1-3 年、(長期) 指 3 年以上。例如：明年(短中期)、2026(中期)、未來十年(長期)。若無提到具體時間，則標註為 null。\n\n"
        "===逐字稿===\n"
        f"{inline_text}\n"
    )

    resp = gemini_generate_with_retry(
        client=client,
        model=model,
        prompt_text=prompt,
        temperature=0.0,
        max_output_tokens=2500,
        max_tries=6,
    )

    text = (getattr(resp, "text", "") or "")
    append_raw("===OUTLOOK RAW===", text)

    clean = sanitize_json_text(text)
    if not clean.strip():
        return []

    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        obj = repair_to_valid_json(client, model, clean)

    temp_summary = {"outlook_calls": obj.get("outlook_calls", [])}
    temp_summary = strict_filter_outlook_calls(temp_summary, inline_text)
    return temp_summary.get("outlook_calls", [])