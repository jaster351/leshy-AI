"""Проверки владения окном браузера без запуска Edge или Chrome."""
from __future__ import annotations

from pathlib import Path

from leshy import managed_browser


class _Job:
    created = []
    def __init__(self):
        self.closed = False
        self.window_visible = True
        self.__class__.created.append(self)

    def focus_existing_window(self):
        return self.window_visible and not self.closed

    def close(self):
        self.closed = True


class _Process:
    created = []
    def __init__(self, exe, args, job):
        self.exe = exe
        self.args = args
        self.job = job
        self.closed = False
        self.__class__.created.append(self)

    def poll(self):
        return 0 if self.job.closed else None

    def wait(self, timeout=3):
        return 0

    def terminate(self):
        self.job.close()

    def close(self):
        self.closed = True


def _prepare(monkeypatch):
    _Job.created = []
    _Process.created = []
    monkeypatch.setattr(managed_browser, "_BrowserJob", _Job)
    monkeypatch.setattr(managed_browser, "_WindowsBrowserProcess", _Process)
    return managed_browser.ManagedBrowser(
        "http://127.0.0.1:8765/", executable_finder=lambda: Path("msedge.exe")
    )


def test_reopening_tray_reuses_one_window(monkeypatch):
    browser = _prepare(monkeypatch)
    try:
        assert browser.open()
        assert browser.open()
        assert len(_Process.created) == 1
        assert "--app=http://127.0.0.1:8765/" in _Process.created[0].args
        assert any(value.startswith("--user-data-dir=") for value in _Process.created[0].args)
    finally:
        browser.close()
    assert _Job.created[0].closed


def test_exit_closes_only_owned_job_and_blocks_new_tabs(monkeypatch):
    browser = _prepare(monkeypatch)
    assert browser.open()
    original = _Job.created[0]
    browser.close()
    assert original.closed
    assert _Process.created[0].closed
    assert not browser.open()
    assert len(_Process.created) == 1


def test_closed_window_can_reopen_without_duplicates(monkeypatch):
    browser = _prepare(monkeypatch)
    try:
        assert browser.open()
        first_job = _Job.created[0]
        browser._started_at -= 10
        first_job.window_visible = False
        assert browser.open()
        assert first_job.closed
        assert len(_Process.created) == 2
    finally:
        browser.close()


def test_unavailable_browser_never_uses_default_unmanaged_tab(monkeypatch):
    browser = managed_browser.ManagedBrowser(
        "http://127.0.0.1:8765/", executable_finder=lambda: None
    )
    assert not browser.open()
    browser.close()
