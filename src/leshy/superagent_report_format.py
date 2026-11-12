"""Extract one final JSON report without treating text as an executed action.

This is only syntax extraction. Owner checks and mutation receipts remain the
responsibility of the existing SuperAgent completion assessment.
"""
from __future__ import annotations

import json
import re
from typing import Any


_MAX_REPORT_INPUT = 32000
_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def decode_report_json(text: str) -> Any | None:
    """Accept exact JSON or one JSON object wrapped in commentary/fences.

    Reject multiple embedded objects or malformed JSON. Never interpret
    ``action=tool`` as permission to execute a tool: this is final-report only.
    """
    raw = str(text or "").strip()
    if not raw or len(raw) > _MAX_REPORT_INPUT:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    fences = _FENCE.findall(raw)
    if len(fences) == 1:
        try:
            return json.loads(fences[0].strip())
        except (ValueError, TypeError):
            return None
    if fences:
        return None
    start = raw.find("{")
    if start < 0 or "{" in raw[:start] or "}" in raw[:start]:
        return None
    try:
        payload, count = json.JSONDecoder().raw_decode(raw[start:])
    except (ValueError, TypeError):
        return None
    tail = raw[start + count:]
    if "{" in tail or "}" in tail:
        return None
    return payload
