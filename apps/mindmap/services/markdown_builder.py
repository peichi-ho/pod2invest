def md_escape(text):
    """
    避免 markdown 特殊字元影響 markmap 結構。
    """
    if text is None:
        return ""
    text = str(text)
    return (text.replace("\\", "\\\\").replace("\n", " ").replace("\r", " "))


def bullets(items, level=0):
    """
    把字串列表轉成 markdown 清單。
    只接受 list；如果是單一字串，會自動包成一個項目，避免被逐字拆開。
    """
    if items is None:
        return []

    if isinstance(items, str):
        items = [items]
    elif not isinstance(items, list):
        items = [str(items)]

    lines = []
    indent = "  " * level

    for item in items:
        if item is None:
            continue

        item = str(item).strip()
        if item:
            lines.append(f"{indent}- {md_escape(item)}")

    return lines


def mindmap_json_to_markdown(data, fallback_title="Mindmap"):
    """
    將結構化的心智圖 JSON 轉成 markmap 可用的 markdown。
    """
    title = data.get("title") or fallback_title
    one_sentence_summary = data.get("one_sentence_summary") or ""
    investment_takeaways = data.get("investment_takeaways") or {}
    arguments = data.get("arguments") or []

    lines = [f"# {md_escape(title)}"]

    if one_sentence_summary:
        lines.append("## 一句話總結")
        lines.append(f"- {md_escape(one_sentence_summary)}")

    if investment_takeaways:
        lines.append("## 投資重點")

        takeaway_order = ["bullish", "bearish", "watchlist", "podcaster_stance"]

        for category in takeaway_order:
            value = investment_takeaways.get(category)
            if not value:
                continue

            lines.append(f"### {md_escape(category)}")
            lines.extend(bullets(value, level=0))

        for category, value in investment_takeaways.items():
            if category in takeaway_order:
                continue
            if not value:
                continue

            lines.append(f"### {md_escape(category)}")
            lines.extend(bullets(value, level=0))

    if arguments:
        lines.append("## 論點整理")

        for idx, arg in enumerate(arguments, start=1):
            if not isinstance(arg, dict):
                continue

            arg_title = arg.get("topic") or f"論點 {idx}"
            lines.append(f"### {md_escape(arg_title)}")

            position = arg.get("position") or ""
            if position:
                lines.append("#### 立場")
                lines.append(f"- {md_escape(position)}")

            summaries = arg.get("summary") or []
            if summaries:
                lines.append("#### 重點")
                lines.extend(bullets(summaries, level=0))

            key_data = arg.get("key_data") or []
            if key_data:
                lines.append("#### 關鍵數據")
                for row in key_data:
                    if not isinstance(row, dict):
                        continue

                    label = str(row.get("label") or "").strip()
                    value = str(row.get("value") or "").strip()
                    context = str(row.get("context") or "").strip()

                    parts = []
                    if label:
                        parts.append(label)
                    if value:
                        parts.append(value)
                    if context:
                        parts.append(context)

                    if parts:
                        lines.append(f"- {md_escape('｜'.join(parts))}")

    return "\n".join(lines)
