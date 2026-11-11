from __future__ import annotations

import threading

from .diagnostics import event
from .inference_scheduler import PRIORITY_BACKGROUND, get_inference_scheduler
from .superagent import SuperAgentService


_INSTALL_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_EXECUTE = None


def install_superagent_background_scheduler() -> None:
    """Не дать фоновому SuperAgent конкурировать с Web/Telegram за локальную LLM.

    Локальный backend Лешего имеет один inference-slot. SuperAgent получает
    минимальный приоритет и начинает assignment только когда scheduler отдаёт
    ему слот. Уже начатая генерация не прерывается — это тот же контракт, что
    у остальных runtime-задач.
    """
    global _INSTALLED, _ORIGINAL_EXECUTE
    with _INSTALL_LOCK:
        if _INSTALLED:
            return
        original_execute = SuperAgentService._execute

        def scheduled_execute(self: SuperAgentService, ticket):
            ticket_id = str((ticket or {}).get("id") or "unknown")
            scheduler = get_inference_scheduler(self.app)
            with scheduler.slot(
                source=f"superagent:{ticket_id}",
                priority=PRIORITY_BACKGROUND,
                cancel_event=self._stop,
                timeout=None,
            ):
                event("superagent_inference_slot_granted", ticket_id=ticket_id, priority=PRIORITY_BACKGROUND)
                return original_execute(self, ticket)

        SuperAgentService._execute = scheduled_execute
        _ORIGINAL_EXECUTE = original_execute
        _INSTALLED = True
        event("superagent_scheduler_installed", priority=PRIORITY_BACKGROUND)


__all__ = ["install_superagent_background_scheduler"]
