"""Статический контракт конкурентной установки обёртки SuperAgent."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from leshy import superagent_scheduler as hook
from leshy.superagent import SuperAgentService


def test_concurrent_install_wraps_execute_once(monkeypatch):
    original = SuperAgentService._execute
    previous_installed = hook._INSTALLED
    previous_original = hook._ORIGINAL_EXECUTE
    try:
        monkeypatch.setattr(hook, "_INSTALLED", False)
        monkeypatch.setattr(hook, "_ORIGINAL_EXECUTE", None)
        monkeypatch.setattr(SuperAgentService, "_execute", original)
        barrier = threading.Barrier(12)

        def install(_):
            barrier.wait(timeout=5)
            hook.install_superagent_background_scheduler()
            return SuperAgentService._execute

        with ThreadPoolExecutor(max_workers=12) as pool:
            installed = list(pool.map(install, range(12)))
        assert all(method is installed[0] for method in installed)
        assert installed[0] is not original
        assert hook._ORIGINAL_EXECUTE is original
        assert hook._INSTALLED is True
    finally:
        monkeypatch.setattr(SuperAgentService, "_execute", original)
        monkeypatch.setattr(hook, "_ORIGINAL_EXECUTE", previous_original)
        monkeypatch.setattr(hook, "_INSTALLED", previous_installed)
