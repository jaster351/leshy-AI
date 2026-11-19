"""Unit coverage for the tray update status renderer (no Task Scheduler needed)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from leshy.update_feedback import TrayUpdateFeedback, render_state


def test_update_statuses_show_real_results():
    for state, needle, terminal in (
        ("checking", "Проверяю", False),
        ("updating", "Выполняется сборка", False),
        ("up_to_date", "Обновлений нет", True),
        ("updated", "обновлён", True),
        ("busy", "Другая сборка", True),
        ("error", "Ошибка проверки", True),
    ):
        message, completed = render_state(
            {"state": state, "version": "1.10.190", "message": "git fetch failed"}
        )
        assert needle in message
        assert completed == terminal


def test_error_is_not_falsely_presented_as_up_to_date():
    status, done = render_state({"state": "error", "message": "No access to GitHub"})
    assert "Ошибка" in status
    assert "Обновлений нет" not in status
    assert done


def test_saved_status_is_visible_after_runtime_restart(tmp_path):
    root = tmp_path / "repo"
    auto = root / ".autobuild"
    auto.mkdir(parents=True)
    state = {
        "request_id": "49c4a6d8-cf16-497b-a91f-f4bc3493cb2f",
        "state": "updated",
        "version": "1.10.190",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    (auto / "update-status.json").write_text(json.dumps(state), encoding="utf-8")
    feedback = TrayUpdateFeedback(root, notify=lambda *_: None, changed=lambda: None)
    assert "1.10.190" in feedback.message
    assert "обновлён" in feedback.message


def test_stale_results_are_not_reported_as_current(tmp_path):
    root = tmp_path / "repo"
    auto = root / ".autobuild"
    auto.mkdir(parents=True)
    status = {
        "request_id": "f5d809b9-b937-4326-8621-e12e0d4b4330",
        "state": "up_to_date",
        "version": "0.0.1",
        "updated_at": "2025-01-01T00:00:00+00:00",
    }
    (auto / "update-status.json").write_text(json.dumps(status), encoding="utf-8")
    feedback = TrayUpdateFeedback(root, notify=lambda *_: None, changed=lambda: None)
    assert "0.0.1" not in feedback.message
