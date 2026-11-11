"""Регрессии: исключения backend не оставляют занятого inference-слота.

Тесты намеренно не запускаются автоматизацией: запуск требует разрешения владельца.
"""
import threading

import pytest

from leshy.inference_scheduler import (
    InferenceScheduler,
    PRIORITY_BACKGROUND,
    PRIORITY_WEB,
)


def test_backend_exception_releases_slot_for_interactive_waiter():
    scheduler = InferenceScheduler()
    with pytest.raises(RuntimeError, match="backend crashed"):
        with scheduler.slot(source="superagent:failure", priority=PRIORITY_BACKGROUND):
            assert scheduler.status()["active"]["source"] == "superagent:failure"
            raise RuntimeError("backend crashed")

    assert scheduler.status()["active"] is None
    with scheduler.slot(source="web:after-crash", priority=PRIORITY_WEB, timeout=0.2):
        assert scheduler.status()["active"]["source"] == "web:after-crash"
    assert scheduler.status()["queue_depth"] == 0


def test_cancelled_waiter_does_not_mask_backend_exception():
    scheduler = InferenceScheduler()
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(RuntimeError, match="backend crashed"):
        with scheduler.slot(source="superagent:failure", priority=PRIORITY_BACKGROUND):
            with pytest.raises(InterruptedError):
                scheduler.acquire(
                    source="web:cancelled",
                    priority=PRIORITY_WEB,
                    cancel_event=cancelled,
                    timeout=0.2,
                )
            raise RuntimeError("backend crashed")

    assert scheduler.status()["active"] is None
    assert scheduler.status()["queue_depth"] == 0
