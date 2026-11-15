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


def test_special_command_cancelled_before_execution_does_not_call_chat():
    from unittest.mock import Mock
    app=SimpleNamespace(_image_prompt=Mock(return_value=None),chat=Mock())
    manager=RealtimeChatManager(app)
    rid="web:20261107155644000:001:123abcde"
    assert manager.stop(rid)
    events=list(manager.stream("/memories",request_id=rid))
    assert events[-1].type=="done"
    assert events[-1].data["stopped"] is True
    app.chat.assert_not_called()


def test_stop_registration_competes_under_same_lock():
    import threading
    manager=RealtimeChatManager(SimpleNamespace())
    rid="web:20261107155644000:002:123abcde"
    barrier=threading.Barrier(3)
    output=[]
    def register():
        barrier.wait()
        _id,cancel=manager._register(rid)
        output.append(cancel)
    def stop():
        barrier.wait()
        assert manager.stop(rid)
    a=threading.Thread(target=register)
    b=threading.Thread(target=stop)
    a.start();b.start();barrier.wait()
    a.join(timeout=2);b.join(timeout=2)
    assert output and output[0].is_set()
