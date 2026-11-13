from __future__ import annotations

from .diagnostics import event
from .inference_scheduler import PRIORITY_BACKGROUND, get_inference_scheduler
from .superagent import SuperAgentService


_INSTALLED = False
_ORIGINAL_CHAT = None


def install_superagent_background_scheduler() -> None:
    """Reserve the local inference slot for EACH LLM call, not whole jobs.

    Read-only tools and web research can run on other CPU threads while the
    model is serving interactive Web/Telegram requests. Background priority,
    process leadership, cancellation and the original model lock are kept.
    """
    global _INSTALLED, _ORIGINAL_CHAT
    if _INSTALLED:
        return
    _ORIGINAL_CHAT = SuperAgentService._chat_inference

    def scheduled_chat(self: SuperAgentService, ticket_id: str, messages):
        scheduler = get_inference_scheduler(self.app)
        with scheduler.slot(
            source=f"superagent:{ticket_id}",
            priority=PRIORITY_BACKGROUND,
            cancel_event=self._stop,
            timeout=None,
        ):
            event("superagent_inference_slot_granted", ticket_id=ticket_id, priority=PRIORITY_BACKGROUND)
            return _ORIGINAL_CHAT(self, ticket_id, messages)

    SuperAgentService._chat_inference = scheduled_chat
    _INSTALLED = True
    event("superagent_scheduler_installed", priority=PRIORITY_BACKGROUND)


__all__ = ["install_superagent_background_scheduler"]
