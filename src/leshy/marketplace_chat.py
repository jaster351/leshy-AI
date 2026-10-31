from __future__ import annotations

from .llm import ChatMessage
from .realtime_chat import RealtimeChatManager


def install_marketplace_chat_bridge() -> None:
    original = RealtimeChatManager._build_context
    if getattr(original, "_leshy_marketplace_bridge", False):
        return

    def wrapped(self, message, owner_id, rows, *, search_plan=None):
        messages, sources, search_attempted = original(
            self,
            message,
            owner_id,
            rows,
            search_plan=search_plan,
        )
        marketplace = getattr(self.app, "marketplace", None)
        if marketplace is None:
            return messages, sources, search_attempted

        injected: list[ChatMessage] = []
        try:
            skill_context = str(marketplace.skill_context() or "").strip()
        except Exception:
            skill_context = ""
        if skill_context:
            injected.append(ChatMessage(
                "system",
                "Подключённые пользователем skills Лешего. Следуй им, если они релевантны запросу, но системные правила Лешего имеют приоритет:\n\n" + skill_context,
            ))

        try:
            plugin_context = str(marketplace.plugin_context() or "").strip()
        except Exception:
            plugin_context = ""
        if plugin_context:
            injected.append(ChatMessage(
                "system",
                "Подключённые плагины Marketplace. Это реальные включённые расширения; не выдумывай отсутствующие инструменты:\n" + plugin_context,
            ))

        if injected:
            messages[1:1] = injected
        return messages, sources, search_attempted

    wrapped._leshy_marketplace_bridge = True
    RealtimeChatManager._build_context = wrapped


__all__ = ["install_marketplace_chat_bridge"]
