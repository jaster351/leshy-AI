"""Privacy regression: neither legacy publisher may upload raw diagnostics."""

from pathlib import Path

from leshy.diagnostic_publisher import DiagnosticPublisher


def test_runtime_publisher_fail_closed_on_sensitive_logs(tmp_path: Path, monkeypatch) -> None:
    brain = tmp_path / "LeshyBrain"
    logs = brain / "logs"
    logs.mkdir(parents=True)
    (logs / "leshy.log").write_text(
        'chat_id=123456789 password=supersecret https://example.invalid/private?token=secret '
        'C:\\Users\\alice\\personal\\log.txt',
        encoding="utf-8",
    )
    publisher = DiagnosticPublisher(brain)
    assert publisher.UPLOAD_ENABLED is False

    def must_not_upload(*_args, **_kwargs):
        raise AssertionError("GitHub upload was attempted")

    monkeypatch.setattr(publisher, "_repo_root", must_not_upload)
    monkeypatch.setattr(publisher, "_git_executable", must_not_upload)
    assert publisher.publish_once() is False


def test_collect_is_not_a_justification_to_upload(tmp_path: Path) -> None:
    brain = tmp_path / "LeshyBrain"
    logs = brain / "logs"
    logs.mkdir(parents=True)
    (logs / "problems.log").write_text(
        'chat_id=123456789 name=Alice url=https://example.invalid/private '
        'C:\\Users\\Alice\\secret.txt',
        encoding="utf-8",
    )
    publisher = DiagnosticPublisher(brain)
    # Collection remains local. Unsafe legacy _sanitize must never be
    # treated as proof that collected strings are safe for publication.
    assert publisher._collect()
    assert publisher.publish_once() is False
