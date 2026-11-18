"""Structure-preserving fallback for long, explicitly sectioned user instructions.

Never confuse model-context compression with a truncated network/user message.
The original is stored in the owner's chat history. This is a lossy
view for a small local model, not a replacement for the source of truth.
"""
from __future__ import annotations

import re

_SECTION = re.compile(r"(?m)^\s*#{1,3}\s+([^\n]{3,160})\s*$")
_URL = re.compile(r"https?://[^\s<>()]+")
_SPACE = re.compile(r"\s+")


def structured_task_outline(text: str, *, max_chars: int = 2600) -> str | None:
    """Cover major sections of a long Markdown assignment within a cap.

    Returns None for arbitrary logs, code dumps, or ordinary user messages.
    This is an extractive outline, not a pretend full reading.
    """
    original = str(text or "")
    matches = list(_SECTION.finditer(original))
    if len(original) < 4200 or len(matches) < 4:
        return None
    cap = max(700, int(max_chars))
    main = [m for m in matches if original[m.start():m.end()].lstrip().startswith(("## ", "# "))]
    sections = main if len(main) >= 4 else matches
    if len(sections) > 30:
        sections = sections[:29] + sections[-1:]
    header = (
        "[СЖАТО ПРИ ФОРМИРОВАНИИ КОНТЕКСТА МОДЕЛИ, а НЕ при приёме сообщения. "
        f"Оригинал: {len(original)} символов, хранится в истории этого чата. "
        "Ниже НЕ полный текст задания; нельзя заявлять, что исходное сообщение оборвалось.]\n"
    )
    ending = (
        "\n[Не продолжай и не дописывай текст задания. Отвечай на его цель. "
        "Не заявляй о выполнении действий без настоящих инструментов. "
        "При недостатке сведений называй ограничение контекста модели, "
        "а не выдуманный обрыв исходного сообщения.]"
    )
    intro_text = _SPACE.sub(" ", original[:matches[0].start()]).strip()[:240]
    final_text = _SPACE.sub(" ", original[-520:]).strip()
    intro = ("Исходная постановка: " + intro_text + "\n") if intro_text else ""
    tail = "\nПоследнее распоряжение: " + final_text
    available = cap - len(header) - len(intro) - len(tail) - len(ending) - 50
    if available < 80 * len(sections):
        intro = ""
        final_text = _SPACE.sub(" ", original[-260:]).strip()
        tail = "\nПоследнее распоряжение: " + final_text
        available = cap - len(header) - len(tail) - len(ending) - 30

    per_section = max(25, available // max(1, len(sections)))
    extracts: list[str] = []
    for index, match in enumerate(sections):
        next_start = sections[index + 1].start() if index + 1 < len(sections) else len(original)
        title = _SPACE.sub(" ", match.group(1)).strip()
        body = original[match.end():next_start]
        meaningful = [
            _SPACE.sub(" ", line).strip(" -\t")
            for line in body.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        statement = next((line for line in meaningful if not _URL.fullmatch(line)), "")
        if not statement and meaningful:
            statement = meaningful[0]
        room = max(0, per_section - len(title) - 5)
        line = "- " + title
        if room:
            line += ": " + statement[:room]
        extracts.append(line[:per_section + 5])
    result = header + intro + "РАЗДЕЛЫ ИСХОДНОГО ЗАДАНИЯ:\n" + "\n".join(extracts) + tail + ending
    if len(result) > cap:
        protected = tail + ending
        head_cap = max(0, cap - len(protected))
        result = result[:head_cap] + protected
    return result


__all__ = ["structured_task_outline"]
