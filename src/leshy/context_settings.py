"""Глобальная настройка использования контекста всеми клиентами Лешего.

Режим «Безлимит» распространяется на доступную сохранённую историю; он не
отключает физический предел n_ctx модели и не влияет на фоновое самообучение.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock

MIN_CONTEXT_TOKENS = 1024
DEFAULT_HISTORY_LIMIT = 24


class ContextSettingsStore:
    def __init__(self, brain_dir: Path | str):
        self.path = Path(brain_dir) / "settings" / "context.json"
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                return {"mode": "auto", "level": 50}
            if not isinstance(raw, dict):
                return {"mode": "auto", "level": 50}
            mode = str(raw.get("mode") or "auto").lower()
            try:
                level = int(raw.get("level", 50))
            except (ValueError, TypeError):
                level = 50
            if mode not in {"auto", "manual"} or not 0 <= level <= 100:
                return {"mode": "auto", "level": 50}
            return {"mode": mode, "level": level}

    def save(self, *, mode: str, level: int) -> dict:
        if mode not in {"auto", "manual"} or type(level) is not int or not 0 <= level <= 100:
            raise ValueError("Режим: авто/вручную; положение ползунка: от 0 до 100")
        record = {"mode": mode, "level": level}
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            try:
                tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, self.path)
            finally:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
        return record

    def context_size(self, physical_n_ctx: int) -> int:
        """Рассчитать выбранное окно, не превышая возможности backend."""
        physical = max(512, int(physical_n_ctx))
        choice = self.load()
        if choice["mode"] == "auto" or choice["level"] == 100:
            return physical
        minimum = min(physical, MIN_CONTEXT_TOKENS)
        result = minimum + (physical - minimum) * choice["level"] // 100
        return max(512, min(physical, (result // 64) * 64))

    def history_limit(self) -> int:
        """Количество соседних реплик, извлекаемых при подготовке диалога."""
        choice = self.load()
        if choice["mode"] == "auto":
            return DEFAULT_HISTORY_LIMIT
        if choice["level"] == 100:
            return 96
        return max(4, 4 + choice["level"] * 92 // 100)

    def unlimited_history(self) -> bool:
        choice = self.load()
        return choice["mode"] == "manual" and choice["level"] == 100

    def status(self, llm=None) -> dict:
        choice = self.load()
        backends = []
        for item in tuple(getattr(llm, "_states", ()) or ()):
            provider = getattr(item, "provider", None)
            if provider is None:
                continue
            limit = int(getattr(provider, "n_ctx", 4096) or 4096)
            backends.append({
                "backend": str(getattr(item, "name", "model")),
                "physical_tokens": limit,
                "selected_tokens": self.context_size(limit),
            })
        return {
            **choice,
            "unlimited_history": self.unlimited_history(),
            "history_window": self.history_limit(),
            "backend_contexts": backends,
            "meaning": (
                "Безлимит: сохранённая история без срока удаления с поиском по прежним "
                "сообщениям. За один запрос модель видит только объём, допускаемый её n_ctx."
            ),
            "affects": ["web", "telegram", "superagent", "shared_llm"],
            "learning_unchanged": True,
        }


__all__ = ["ContextSettingsStore", "MIN_CONTEXT_TOKENS"]
