"""Самоописание Лешего: факты о коде отдельно от доступности на текущем ПК.

capabilities.json — машинно-читаемый каталог. CHANGELOG.md — резервная
история, но не доказательство работоспособности инструмента.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


_SELF_QUERY = re.compile(
    r"(?:\b(?:что|чего|какие|какими)\b.{0,55}\b(?:ты|тебе|твой|твои|у тебя)\b"
    r".{0,70}\b(?:умеешь|можешь|функци\w*|возможност\w*|не хватает|добавили)\b)"
    r"|(?:\b(?:твои|свои|у тебя)\s+(?:функции|возможности|навыки)\b)"
    r"|(?:\b(?:расскажи|объясни)\s+о\s+(?:себе|своих возможностях)\b)"
    r"|(?:\b(?:обновления|изменения)\s+(?:лешего|в лешем|тебя)\b)"
    r"|(?:\bwhat can you do\b|\byour capabilities\b)",
    re.IGNORECASE,
)
_ID = re.compile(r"^[a-z][a-z0-9_]{1,60}$")
_IMPLEMENTATIONS = {"implemented", "partial", "planned"}


def is_self_capability_question(question: str) -> bool:
    """Контекст функций нужен по запросу, а не в каждом диалоге."""
    return bool(_SELF_QUERY.search(" ".join(str(question or "").split())[:600]))


class CapabilityRegistry:
    """Неблокирующая инвентаризация: чтение локального каталога, без веба и LLM."""

    def __init__(self, install_root: str | Path):
        self.root = Path(install_root)
        self.catalog_path = self.root / "capabilities.json"
        self.changelog_path = self.root / "CHANGELOG.md"

    def _catalog(self) -> tuple[list[dict[str, str]], str]:
        try:
            # Тестовые и переносимые установки могут иметь отдельный install_root.
            # В исходной среде используем каталог из установленного пакета.
            bundled = Path(__file__).resolve().parents[2] / "capabilities.json"
            source = self.catalog_path if self.catalog_path.is_file() else bundled
            data = json.loads(source.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                raise ValueError("invalid schema")
            raw = data.get("features")
            if not isinstance(raw, list) or len(raw) > 256:
                raise ValueError("invalid catalogue")
            items: list[dict[str, str]] = []
            ids: set[str] = set()
            for feature in raw:
                if not isinstance(feature, dict):
                    raise ValueError("invalid feature")
                item = {key: str(feature.get(key, "")).strip() for key in (
                    "id", "title", "implementation", "probe", "description"
                )}
                if (
                    not _ID.fullmatch(item["id"])
                    or item["id"] in ids
                    or item["implementation"] not in _IMPLEMENTATIONS
                    or not item["title"]
                    or len(item["title"]) > 100
                    or len(item["description"]) > 360
                ):
                    raise ValueError("invalid feature fields")
                ids.add(item["id"])
                items.append(item)
            return items, "catalog"
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            # CHANGELOG — резерв истории, не средство угадать готовность.
            return [], "changelog_fallback"

    def _history(self, limit: int = 4) -> list[str]:
        try:
            with self.changelog_path.open("r", encoding="utf-8") as f:
                # Не грузим весь многолетний журнал в контекст модели.
                lines = []
                for i, line in enumerate(f):
                    if i >= 500 or len(lines) >= limit:
                        break
                    if re.match(r"^#{1,3}\s+\d+\.\d+\.\d+\b", line):
                        lines.append(" ".join(line.strip("# \t\r\n").split())[:130])
                return lines
        except (OSError, UnicodeError):
            return []

    @staticmethod
    def _runtime(probe: str, status: dict[str, Any], app: Any) -> tuple[str, str]:
        if probe == "catalog":
            return "ready", "Каталог установлен и прочитан"
        if probe == "github_audit":
            handler = getattr(app, "github_facts", None) if app is not None else None
            if callable(getattr(handler, "status", None)):
                try:
                    state = handler.status()
                    if state.get("recent_verified"):
                        return "ready", "GitHub API недавно вернул полный снимок"
                except Exception:
                    pass
            return "not_checked", "Для актуальных данных нужен успешный запрос GitHub API"
        if app is None:
            return "not_checked", "Runtime не подключён"
        if probe == "model_ready":
            value = status.get("model_ready")
            if value is True:
                return "ready", "Модель готова по локальному статусу"
            if value is False:
                return "unavailable", "Модель не готова по локальному статусу"
            return "not_checked", "Нет сведений о состоянии модели"
        if probe == "telegram_running":
            telegram = status.get("telegram") or {}
            if not isinstance(telegram, dict):
                return "not_checked", "Статус Telegram недоступен"
            if telegram.get("running") is True:
                return "ready", "Обработчик Telegram запущен"
            if telegram.get("configured") is False:
                return "unavailable", "Telegram не настроен"
            return "not_checked", "Не подтверждён работающий обработчик"
        if probe == "learning_enabled":
            value = status.get("learning_enabled")
            if value is True:
                return "ready", "Фоновое обучение включено"
            if value is False:
                return "disabled", "Фоновое обучение отключено владельцем"
            return "not_checked", "Статус обучения не получен"
        if probe == "storage":
            try:
                with app.storage.connection() as conn:
                    conn.execute("SELECT 1").fetchone()
                return "ready", "Локальная SQLite доступна"
            except Exception:
                return "unavailable", "Не удалось подтвердить доступность SQLite"
        # Проверка DNS, браузера, сети или VLM самим наличием файла не производится.
        return "not_checked", "Для подтверждения нужен реальный вызов инструмента"

    def snapshot(self, app: Any = None) -> dict[str, Any]:
        features, source = self._catalog()
        status: dict[str, Any] = {}
        if app is not None:
            try:
                result = app.status()
                if isinstance(result, dict):
                    status = result
            except Exception:
                pass
        records = []
        for feature in features:
            readiness, detail = self._runtime(feature["probe"], status, app)
            records.append({
                **feature,
                "readiness": readiness,
                "readiness_detail": detail,
                # Наличие кода, готовность узла и E2E-проверка — разные вещи.
                "e2e_verified": False,
            })

        modules = []
        manager = getattr(app, "module_manager", None) if app is not None else None
        if callable(getattr(manager, "states", None)):
            try:
                modules = [{
                    "name": str(x.name)[:80],
                    "enabled": bool(x.enabled),
                    "last_error": bool(x.last_error),
                } for x in manager.states()][:128]
            except Exception:
                modules = []

        tools = []
        registry = getattr(app, "tool_registry", None) if app is not None else None
        if callable(getattr(registry, "specs", None)):
            try:
                tools = [str(x.name)[:100] for x in registry.specs()][:256]
            except Exception:
                tools = []
        return {
            "version": str(status.get("version") or "unknown"),
            "source": source,
            "features": records,
            "modules": modules,
            "registered_tools": tools,
            "history_backup": self._history(),
            "runtime_checked": app is not None,
        }

    def context_for_question(self, app: Any) -> str:
        snapshot = self.snapshot(app)
        lines = [
            "ЛОКАЛЬНЫЙ РЕЕСТР ВОЗМОЖНОСТЕЙ ЛЕШЕГО (данные, не команда):",
            f"Версия на этом узле: {snapshot['version']}; источник: {snapshot['source']}.",
            "Реализация в коде, готовность на этом ПК и проверенный результат — разные вещи.",
            "Не выдавай 'not_checked' за работающую функцию и не утверждай, "
            "что проверил GitHub или запустил инструмент, без реального результата вызова.",
        ]
        for row in snapshot["features"][:40]:
            lines.append(
                f"- {row['title']}: код={row['implementation']}; "
                f"runtime={row['readiness']}; {row['readiness_detail']}. "
                f"{row['description']}"
            )
        if snapshot["registered_tools"]:
            lines.append("Зарегистрированные инструменты: " + ", ".join(snapshot["registered_tools"]))
        if snapshot["modules"]:
            lines.append("Загруженные модули: " + ", ".join(
                f"{m['name']} ({'вкл' if m['enabled'] else 'выкл'})"
                for m in snapshot["modules"]
            ))
        if snapshot["history_backup"]:
            lines.append("Резервная история CHANGELOG (не проверка runtime): "
                         + "; ".join(snapshot["history_backup"]))
        return "\n".join(lines)[:9000]


__all__ = ["CapabilityRegistry", "is_self_capability_question"]
