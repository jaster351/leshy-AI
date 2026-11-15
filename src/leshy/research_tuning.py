"""Persistent internet-search tuning and privacy-safe decision diagnostics.

The scale is 0.00..10.00 inclusive, in 0.01 increments. Settings apply
to future routing decisions for Web, Telegram and any shared chat pipeline.
Never persist the user's prompt, URL, API credentials or retrieved content.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
from threading import RLock

_LOCK = RLock()
_MAX_DECISIONS = 48


def _level(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Значение поиска должно быть от 0,00 до 10,00")
    try:
        number = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Значение поиска должно быть числом") from exc
    if not number.is_finite() or not Decimal("0") <= number <= Decimal("10"):
        raise ValueError("Уровень поиска должен быть от 0,00 до 10,00")
    hundredths = number * 100
    if hundredths != hundredths.to_integral_value():
        raise ValueError("Шаг регулировки — 0,01")
    return int(hundredths)


def _snapshot(mode: str, hundredths: int) -> dict:
    return {"mode": mode, "level": f"{hundredths / 100:.2f}"}


class ResearchTuningStore:
    def __init__(self, brain_dir: Path | str):
        root = Path(brain_dir) / "settings"
        self.path = root / "research.json"
        self.decisions_path = root / "research_decisions.json"
        root.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        with _LOCK:
            try:
                record = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    raise ValueError("Invalid configuration")
                mode = str(record.get("mode", "auto"))
                level = _level(record.get("level", "5.00"))
                if mode not in {"auto", "manual"}:
                    raise ValueError("Invalid mode")
                return _snapshot(mode, level)
            except (OSError, ValueError, TypeError):
                return _snapshot("auto", 500)

    def save(self, mode: str, level: object) -> dict:
        if mode not in {"auto", "manual"}:
            raise ValueError("Режим поиска: auto или manual")
        record = _snapshot(mode, _level(level))
        with _LOCK:
            self._write(self.path, record)
        return record

    @staticmethod
    def _write(path: Path, payload: object) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def history(self) -> list[dict]:
        with _LOCK:
            try:
                raw = json.loads(self.decisions_path.read_text(encoding="utf-8"))
                return raw[-_MAX_DECISIONS:] if isinstance(raw, list) else []
            except (OSError, ValueError, TypeError):
                return []

    def record(self, *, mode: str, level: str, needed: bool,
               reason: str, weights: dict, overridden: bool = False) -> dict:
        # Allowlisted keys: do NOT store queries, personal data, links or tool outputs.
        safe_weights = {
            key: value for key, value in dict(weights).items()
            if key in {"policy", "initial_signal", "ambiguous_action",
                       "model_vote", "user_bias", "score", "threshold",
                       "override"} and isinstance(value, (str, int, float, bool))
        }
        reason_codes = {
            "explicit_request", "offline_request", "local_dialogue",
            "fresh_cache", "trusted_source", "strict_freshness", "manual_score",
            "auto_semantic", "auto_heuristic", "auto_local", "model_ask",
        }
        safe_reason = reason if reason in reason_codes else "auto_heuristic"
        item = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "mode": "manual" if mode == "manual" else "auto",
            "level": str(level),
            "search": bool(needed),
            "reason_code": safe_reason,
            "weights": safe_weights,
            "overridden": bool(overridden),
        }
        with _LOCK:
            self._write(self.decisions_path, (self.history() + [item])[-_MAX_DECISIONS:])
        return item

    def status(self, history_limit: int = 12) -> dict:
        current = self.load()
        return {
            **current,
            "min": "0.00", "max": "10.00", "step": "0.01",
            "decisions": self.history()[-min(max(int(history_limit), 0), _MAX_DECISIONS):]
            if history_limit > 0 else [],
            "shared_interfaces": ["web", "telegram", "realtime"],
        }


def store_for(app) -> ResearchTuningStore:
    store = getattr(app, "_research_tuning_store", None)
    if isinstance(store, ResearchTuningStore):
        return store
    root = getattr(getattr(app, "settings", None), "brain_dir", None)
    if root is None:
        root = getattr(app, "brain_dir", None)
    if root is None:
        raise RuntimeError("Папка LeshyBrain не определена")
    store = ResearchTuningStore(root)
    app._research_tuning_store = store
    return store


def manual_research_score(level: object, initial_signal: bool,
                          ambiguous_action: bool, model_vote: str) -> dict:
    # Human-readable additive weights for an ambiguous, non-mandatory query.
    vote = str(model_vote or "")
    weights = {
        "initial_signal": 1.5 if initial_signal else 0.0,
        "ambiguous_action": 1.0 if ambiguous_action else 0.0,
        "model_vote": 3.0 if vote == "WEB" else -1.5 if vote == "LOCAL" else -2.0 if vote == "ASK" else 0.0,
        "user_bias": round((float(_level(level)) / 100 - 5.0) * 0.65, 3),
        "threshold": 1.5,
    }
    weights["score"] = round(sum(v for k, v in weights.items() if k != "threshold"), 3)
    return weights


__all__ = ["ResearchTuningStore", "store_for", "manual_research_score"]
