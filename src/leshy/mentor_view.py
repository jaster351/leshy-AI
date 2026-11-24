"""Read-only, bounded local AI-mentor history for the verified creator."""
from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime, timezone

from .mentor_batch import read_bytes, validate_batch

_ID = re.compile(r"mentor-[a-z0-9-]{1,80}")
_MAX_FILE = 128 * 1024
_MAX_ITEMS = 50


def _read(path: Path, limit=_MAX_FILE) -> dict:
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
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
                      "error", "checking", "not_configured", "outcome_unknown", "waiting_delivery"}
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
    # Доставка обновлятором видна до начала инференса, только после проверки хеша.
    delivered = {}
    delivery_at = ""
    batch_id = config.get("batch_id")
    expected = config.get("batch_sha256")
    if (config.get("repository") == "jaster351/AI-mentor"
            and isinstance(batch_id, str) and re.fullmatch(r"mentor-batch-[a-z0-9-]{1,55}", batch_id)
            and isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected)):
        delivery = root / (batch_id + ".delivery.json")
        try:
            pairs = validate_batch(read_bytes(delivery), batch_id, expected)
            delivered = {batch_id + "-" + qid: question for qid, question in pairs}
            delivery_at = datetime.fromtimestamp(delivery.stat().st_mtime, timezone.utc).isoformat()
        except (OSError, ValueError, TypeError):
            delivered = {}
        ids.update(delivered)
    items = []
    ordered = sorted(delivered) + sorted(ids - delivered.keys(), reverse=True)
    for rid in ordered[:_MAX_ITEMS]:
        q = _read(root / (rid + ".question.json"))
        a = _read(root / (rid + ".json"))
        published = _read(root / (rid + ".published.json"))
        question = _text(q.get("question"), 4000) if q.get("request_id") == rid else ""
        from_delivery = not question and rid in delivered
        if from_delivery:
            question = delivered[rid]
        valid_answer = a.get("request_id") == rid and a.get("status") == "answered" and a.get("source") in {"leshy_api", "leshy_model"}
        answer = _text(a.get("answer"), 12000) if valid_answer else ""
        from hashlib import sha256
        sent = bool(answer and published.get("request_id") == rid
                    and published.get("answer_sha256") == sha256(answer.encode("utf-8")).hexdigest())
        phase = ("published" if sent else "answered" if answer else
                 "outcome_unknown" if a.get("request_id") == rid and a.get("status") == "sent"
                 else "error" if a.get("status") == "error"
                 else "delivered" if from_delivery else "received" if question else "not_received")
        items.append({
            "request_id": rid, "status": phase, "question": question, "answer": answer,
            "received_at": _text(q.get("timestamp")),
            "delivered_at": delivery_at if rid in delivered else "",
            "answered_at": _text(a.get("timestamp")) if answer else "",
            "published_at": _text(published.get("timestamp")) if sent else "",
            "version": _text(a.get("revision")) if answer else "",
            "model": _text(a.get("model")) if answer else "",
            "source": _text(a.get("source")) if answer else "",
        })
    items.sort(key=lambda row: row["answered_at"] or row["received_at"], reverse=True)
    export_hash = ""
    export_path = root / "export.json"
    if export_path.is_file() and not export_path.is_symlink():
        from hashlib import sha256
        with export_path.open("rb") as handle:
            raw = handle.read(256 * 1024 + 1)
        if len(raw) <= 256 * 1024:
            export_hash = sha256(raw).hexdigest()
    load = _read(root / "load-policy.json", 1024 * 1024)
    last = load.get("last_review") or {}
    if not isinstance(last, dict):
        last = {}
    investigations = load.get("investigation") or []
    history = load.get("quality_history", [])
    points = []
    if isinstance(history, list):
        for point in history[-168:]:
            if not isinstance(point, dict):
                continue
            value = point.get("leshy_percent")
            if type(value) not in (int, float) or not 0 <= value <= 100:
                continue
            if type(point.get("questions")) is not int or not 1 <= point["questions"] <= 256:
                continue
            row = {**{k: _text(point.get(k)) for k in ("batch_id", "timestamp", "benchmark_id", "rubric_id", "model", "revision")},
                   "leshy_percent": value, "questions": point["questions"]}
            base, relative = point.get("reference_percent"), point.get("relative_percent")
            if (type(base) in (int, float) and 0 < base <= 100
                    and type(relative) in (int, float) and 0 <= relative <= 153600):
                row.update(reference_model=_text(point.get("reference_model")), reference_percent=base, relative_percent=relative)
            points.append(row)
    return {"configured": bool(config), "enabled": config.get("enabled") is True,
            "training_quota": load.get("quota", 30),
            "load_decision": _text(load.get("decision")),
            "review_quality": last.get("quality"),
            "quality_history": points,
            "reviewed_questions": last.get("reviewed"),
            "open_investigations": len(investigations) if isinstance(investigations, list) else 0,
            "export_sha256": export_hash,
            "status": current, "last_check_at": _text(status.get("timestamp")),
            "items": items, "truncated": len(ids) > _MAX_ITEMS,
            "delivered_count": len(delivered),
            "evaluation_status": "review_received" if last else "not_reviewed"}
