"""Web cancellation: stop works before first server SSE registration."""
from __future__ import annotations

from types import SimpleNamespace
from leshy.realtime_chat import RealtimeChatManager


def test_stop_before_register_cancels_future_web_request():
    manager=RealtimeChatManager(SimpleNamespace())
    rid="web:20261107155644000:000:123abcde"
    assert manager.stop(rid)
    actual,cancel=manager._register(rid)
    assert actual==rid
    assert cancel.is_set()
    manager._unregister(rid)


def test_early_stop_is_one_shot():
    manager=RealtimeChatManager(SimpleNamespace())
    rid="web:20261107155644000:000:123abcde"
    assert manager.stop(rid)
    _,first=manager._register(rid)
    assert first.is_set()
    manager._unregister(rid)
    _,second=manager._register(rid)
    assert not second.is_set()
    manager._unregister(rid)


def test_unknown_nonweb_stop_is_not_queued():
    manager=RealtimeChatManager(SimpleNamespace())
    assert not manager.stop("unknown-request")
    _,cancel=manager._register("unknown-request")
    assert not cancel.is_set()


def test_current_registered_cancel_always_wins():
    manager=RealtimeChatManager(SimpleNamespace())
    rid="web:20261107155644000:000:123abcde"
    _,cancel=manager._register(rid)
    assert manager.stop(rid)
    assert cancel.is_set()
