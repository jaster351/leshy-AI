from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from .storage import utcnow


@dataclass(frozen=True, slots=True)
class ModuleSettingDefinition:
    name: str
    title: str
    description: str
    default_enabled: bool = True
    dependencies: tuple[str, ...] = ()


MODULE_SETTINGS: tuple[ModuleSettingDefinition, ...] = (
    ModuleSettingDefinition(
        name="human-browser",
        title="Human Browser",
        description="Системный Edge/Chrome/Chromium с отдельным профилем Лешего, cookies и представлением страницы как для человека; если браузера нет, Windows-версия попробует установить его автоматически.",
    ),
    ModuleSettingDefinition(
        name="browser-agent",
        title="Browser Agent",
        description="Интеллектуальное исследование сайтов через Browser Use и детерминированные действия через Playwright. Покупки, бронирования, отправка форм и финансовые операции проходят Human Approval Gate.",
        dependencies=("human-browser",),
    ),
    ModuleSettingDefinition(
        name="document-intelligence",
        title="Document Intelligence",
        description="Локальный разбор PDF, DOCX, XLSX, PPTX и текстовых документов в Markdown для анализа, памяти и RAG через MarkItDown с резервным PDF-парсером.",
    ),
    ModuleSettingDefinition(
        name="computer-use",
        title="Computer Use",
        description="Локальное управление desktop UI по циклу увидеть → выполнить → повторно увидеть → проверить. Опасные действия требуют подтверждения пользователя.",
    ),
    ModuleSettingDefinition(
        name="tools-marketplace",
        title="Skills / MCP Marketplace",
        description="Безопасный реестр MCP/Skills, просмотр разрешений, изолированная установка расширений, health-check, отключение и удаление.",
    ),
    ModuleSettingDefinition(
        name="parcel-tracking",
        title="Отслеживание посылок",
        description="Проверка трек-номеров через бесплатный веб-сервис без регистрации и фоновое уведомление об изменениях.",
        dependencies=("human-browser",),
    ),
    ModuleSettingDefinition(
        name="network-blocking",
        title="Проверка IP и блокировок ТСПУ",
        description="TCP 22/3389 из сети Лешего + Cheburcheck + географические TCP-проверки Check-Host с оценкой вероятности блокировки.",
    ),
    ModuleSettingDefinition(
        name="support-consultant",
        title="Консультант техподдержки",
        description="Диагностика серверов и подготовка ответов по заявкам; профиль сервера определяется по фактической доступности SSH/RDP.",
    ),
    ModuleSettingDefinition(
        name="lenta",
        title="Лента",
        description="Поиск товаров и актуальных цен, планирование корзины и оформление заказа через личный кабинет. Авторизация и cookies изолированы по пользователям; перед заказом и оплатой требуется подтверждение владельца.",
        dependencies=("human-browser", "browser-agent"),
    ),
    ModuleSettingDefinition(
        name="superagent",
        title="SuperAgent",
        description="Автономные фоновые задания по схеме Trigger → Mandate → Ticket → Assignment → Report: состояние переживает перезапуск, временные ошибки повторяются, а к человеку возвращаются только результат или один блокирующий вопрос.",
    ),
)


_PREFIX = "module.enabled."


def _definition(name: str) -> ModuleSettingDefinition:
    normalized = str(name or "").strip()
    for item in MODULE_SETTINGS:
        if item.name == normalized:
            return item
    raise KeyError(f"Неизвестный модуль: {normalized}")


def _stored_value(storage, name: str) -> bool | None:
    with storage.connection() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (_PREFIX + name,)).fetchone()
    if row is None:
        return None
    raw = str(row["value"] or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    try:
        return bool(json.loads(raw))
    except Exception:
        return None


def configured_enabled(storage, name: str) -> bool:
    definition = _definition(name)
    stored = _stored_value(storage, definition.name)
    return definition.default_enabled if stored is None else bool(stored)


def module_enabled(storage, name: str) -> bool:
    definition = _definition(name)
    if not configured_enabled(storage, definition.name):
        return False
    return all(module_enabled(storage, dependency) for dependency in definition.dependencies)


def set_module_enabled(storage, name: str, enabled: bool) -> dict[str, Any]:
    definition = _definition(name)
    value = "1" if bool(enabled) else "0"
    with storage.connection() as conn:
        conn.execute(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_PREFIX + definition.name, value),
        )
    storage.audit(
        "module.setting.changed",
        {"module": definition.name, "enabled": bool(enabled), "updated_at": utcnow()},
    )
    return module_state(storage, definition.name)


def module_state(storage, name: str) -> dict[str, Any]:
    definition = _definition(name)
    configured = configured_enabled(storage, definition.name)
    dependencies = [
        {
            "name": dependency,
            "enabled": module_enabled(storage, dependency),
        }
        for dependency in definition.dependencies
    ]
    effective = configured and all(item["enabled"] for item in dependencies)
    return {
        "name": definition.name,
        "title": definition.title,
        "description": definition.description,
        "configured_enabled": configured,
        "enabled": effective,
        "dependencies": dependencies,
    }


def list_module_states(storage) -> list[dict[str, Any]]:
    return [module_state(storage, item.name) for item in MODULE_SETTINGS]


__all__ = [
    "MODULE_SETTINGS",
    "ModuleSettingDefinition",
    "configured_enabled",
    "list_module_states",
    "module_enabled",
    "module_state",
    "set_module_enabled",
]
