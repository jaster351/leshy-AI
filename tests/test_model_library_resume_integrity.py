"""Offline regression fixtures for verified resumed model downloads.

These tests are intentionally not executed in this change.
"""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from leshy.model_library import ModelLibraryManager, RECOMMENDED_MODELS


class _Response(BytesIO):
    def __init__(self, payload: bytes, *, status: int, headers: dict[str, str]):
        super().__init__(payload)
        self.status = status
        self.headers = headers

    def geturl(self):
        return "https://cdn-lfs.huggingface.co/test/model.gguf"


def test_pinned_model_has_published_sha256():
    model = next(x for x in RECOMMENDED_MODELS if x.id == "qwen35-9b-original-q4km")
    assert model.sha256 == "dcdc2a0977b42d9494b99671e74834027d0eaf9e925f8e03cc1880e617be1d39"


def test_resume_uses_range_and_verifies_whole_file():
    payload = b"GGUF" + b"a" * 32
    with TemporaryDirectory() as folder:
        manager = ModelLibraryManager(Path(folder))
        partial = Path(folder) / "test.gguf.download"
        partial.write_bytes(payload[:11])
        def reply(req, timeout):
            assert req.get_header("Range") == "bytes=11-"
            return _Response(
                payload[11:],
                status=206,
                headers={"Content-Range": f"bytes 11-{len(payload)-1}/{len(payload)}",
                         "Content-Length": str(len(payload)-11)},
            )
        with patch("leshy.model_library.request.urlopen", side_effect=reply):
            count = manager._download_one(
                "https://huggingface.co/test/test/resolve/commit/test.gguf",
                "test.gguf", stage="downloading_model",
                expected_sha256=sha256(payload).hexdigest(),
            )
        assert count == len(payload)
        assert (Path(folder) / "test.gguf").read_bytes() == payload
        assert not partial.exists()


def test_ignored_range_restarts_without_duplicate_bytes():
    payload = b"GGUF" + b"z" * 27
    with TemporaryDirectory() as folder:
        manager = ModelLibraryManager(Path(folder))
        (Path(folder) / "test.gguf.download").write_bytes(payload[:12])
        def reply(req, timeout):
            assert req.get_header("Range") == "bytes=12-"
            return _Response(payload, status=200, headers={"Content-Length": str(len(payload))})
        with patch("leshy.model_library.request.urlopen", side_effect=reply):
            manager._download_one(
                "https://huggingface.co/test/test/resolve/commit/test.gguf",
                "test.gguf", stage="downloading_model",
                expected_sha256=sha256(payload).hexdigest(),
            )
        assert (Path(folder) / "test.gguf").read_bytes() == payload


def test_incomplete_download_keeps_resumable_partial():
    payload = b"GGUF" + b"y" * 22
    with TemporaryDirectory() as folder:
        manager = ModelLibraryManager(Path(folder))
        with patch("leshy.model_library.request.urlopen", return_value=_Response(
            payload[:10], status=200, headers={"Content-Length": str(len(payload))},
        )):
            try:
                manager._download_one(
                    "https://huggingface.co/test/test/resolve/commit/test.gguf",
                    "test.gguf", stage="downloading_model",
                )
            except OSError:
                pass
            else:
                raise AssertionError("truncated download was accepted")
        assert (Path(folder) / "test.gguf.download").read_bytes() == payload[:10]
        assert not (Path(folder) / "test.gguf").exists()


def test_sha256_mismatch_cannot_replace_existing_model():
    good = b"GGUF" + b"original"
    bad = b"GGUF" + b"incorrect"
    with TemporaryDirectory() as folder:
        manager = ModelLibraryManager(Path(folder))
        target = Path(folder) / "test.gguf"
        target.write_bytes(good)
        with patch("leshy.model_library.request.urlopen", return_value=_Response(
            bad, status=200, headers={"Content-Length": str(len(bad))},
        )):
            try:
                manager._download_one(
                    "https://huggingface.co/test/test/resolve/commit/test.gguf",
                    "test.gguf", stage="downloading_model",
                    expected_sha256=sha256(b"different").hexdigest(),
                )
            except ValueError as exc:
                assert "SHA-256" in str(exc)
            else:
                raise AssertionError("corrupt download was accepted")
        assert target.read_bytes() == good
        assert not (Path(folder) / "test.gguf.download").exists()
