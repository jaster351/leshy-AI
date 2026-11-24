"""Read-only, bounded local AI-mentor history for the verified creator."""
from __future__ import annotations

import json
import re
from pathlib import Path

_ID = re.compile(r"mentor-[a-z0-9-]{1,80}")
_MAX_FILE = 128 * 1024
_MAX_ITEMS = 50


def _read(path: Path) -> dict:
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        with path.open("rb") as handle:
            raw = handle.read(_MAX_FILE + 1)
        if len(raw) > _MAX_FILE:
            return {}
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _text(value, limit=200):
    return value[:limit] if isinstance(value, str) else ""


def mentor_snapshot(brain_dir: Path) -> dict:
    root = Path(brain_dir) / "ai-mentor"
    if root.is_symlink() or not root.is_dir():
        return {"configured": False, "enabled": False, "items": [],
                "status": "not_configured", "last_check_at": "", "truncated": False}
    config = _read(root / "config.json")
    status = _read(root / "status.json")
    valid_statuses = {"waiting", "awaiting_review", "published", "disabled",
                      "error", "checking", "not_configured"}
    current = status.get("status")
    if current not in valid_statuses:
        current = "not_confirmed"
    if config.get("enabled") is not True:
        current = "disabled" if config else "not_configured"
    ids = set()
    rid = config.get("request_id")
    if isinstance(rid, str) and _ID.fullmatch(rid):
        ids.add(rid)
    for path in root.iterdir():
        name = path.name
        if name.endswith(".question.json"):
            candidate = name[:-len(".question.json")]
        elif name.endswith(".json"):
            candidate = name[:-5]
        else:
            continue
        if _ID.fullmatch(candidate):
            ids.add(candidate)
        if len(ids) > 1000:
            break
    items = []
    for rid in sorted(ids, reverse=True)[:_MAX_ITEMS]:
        q = _read(root / (rid + ".question.json"))
        a = _read(root / (rid + ".json"))
        published = _read(root / (rid + ".published.json"))
        question = _text(q.get("question"), 4000) if q.get("request_id") == rid else ""
        valid_answer = a.get("request_id") == rid and a.get("status") == "answered" and a.get("source") == "leshy_api"
        answer = _text(a.get("answer"), 12000) if valid_answer else ""
        from hashlib import sha256
        sent = bool(answer and published.get("request_id") == rid
                    and published.get("answer_sha256") == sha256(answer.encode("utf-8")).hexdigest())
        phase = ("published" if sent else "answered" if answer else
                 "outcome_unknown" if a.get("request_id") == rid and a.get("status") == "sent"
                 else "received" if question else "not_received")
        items.append({
            "request_id": rid, "status": phase, "question": question, "answer": answer,
            "received_at": _text(q.get("timestamp")),
            "answered_at": _text(a.get("timestamp")) if answer else "",
            "published_at": _text(published.get("timestamp")) if sent else "",
            "version": _text(a.get("revision")) if answer else "",
            "model": _text(a.get("model")) if answer else "",
            "source": "leshy_api" if answer else "",
        })
    items.sort(key=lambda row: row["answered_at"] or row["received_at"], reverse=True)
    return {"configured": bool(config), "enabled": config.get("enabled") is True,
            "status": current, "last_check_at": _text(status.get("timestamp")),
            "items": items, "truncated": len(ids) > _MAX_ITEMS,
            "evaluation_status": "not_implemented"}
