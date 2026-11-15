"""Download the complete public knowledge tree as data into LeshyBrain.

Immutable snapshots and an atomic pointer keep an interrupted update from
replacing the last complete copy. Nothing downloaded here is executed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha1
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import threading
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


REPOSITORY = "jaster351/leshy-AI"
BRANCH = "main"
_SHA = re.compile(r"[0-9a-f]{40}")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relative_path(value: object) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("invalid repository path")
    path = PurePosixPath(value)
    if path.is_absolute() or str(path) != value:
        raise ValueError("noncanonical repository path")
    for part in path.parts:
        if part in {".", ".."} or part.endswith((".", " ")) or any(ord(c) < 32 or c in '<>:"|?*' for c in part):
            raise ValueError("unsafe repository path")
        if part.split(".", 1)[0].lower() in _RESERVED:
            raise ValueError("repository path is not portable to Windows")
    return Path(*path.parts)


def _blob_sha(data: bytes) -> str:
    return sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


class _Cancelled(Exception):
    pass


class KnowledgeRepositorySync:
    """Owner-requested catalogue delivery, independent of autonomous research."""

    INTERVAL_SECONDS = 3600.0
    RETRY_SECONDS = 90.0
    MAX_FILE_BYTES = 32 * 1024 * 1024
    MAX_TOTAL_BYTES = 512 * 1024 * 1024
    MAX_METADATA_BYTES = 16 * 1024 * 1024

    def __init__(self, cache_root: Path, bundled_root: Path):
        self.cache_root = Path(cache_root).expanduser().resolve()
        self.bundled_root = Path(bundled_root).expanduser().resolve()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._guard = threading.RLock()
        self._sync_guard = threading.Lock()
        self._active_root: Path | None = None
        self._manifest: dict = {}
        self._status: dict = {"state": "not_checked", "repository": REPOSITORY, "branch": BRANCH, "folder": "knowledge"}
        self._restore()

    def _check_stop(self) -> None:
        if self._stop.is_set():
            raise _Cancelled()

    def _read(self, url: str, limit: int) -> bytes:
        self._check_stop()
        req = Request(url, headers={"User-Agent": "Leshy-Knowledge-Sync", "Accept": "application/vnd.github+json"})
        with urlopen(req, timeout=4) as response:
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname not in {"api.github.com", "raw.githubusercontent.com"}:
                raise ValueError("unexpected catalogue download redirect")
            chunks = []
            size = 0
            while True:
                self._check_stop()
                chunk = response.read(min(65536, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise ValueError("catalogue response exceeds the size limit")
        return b"".join(chunks)

    def _api(self, path: str) -> dict:
        data = json.loads(self._read(f"https://api.github.com/repos/{REPOSITORY}/{path}", self.MAX_METADATA_BYTES))
        if not isinstance(data, dict):
            raise ValueError("invalid GitHub metadata")
        return data

    @staticmethod
    def _sha(value: object) -> str:
        if not isinstance(value, str) or not _SHA.fullmatch(value):
            raise ValueError("invalid Git object identifier")
        return value

    def _files(self, entries: object) -> list[dict]:
        if not isinstance(entries, list) or not entries:
            raise ValueError("empty or invalid knowledge manifest")
        files = []
        names = set()
        for entry in entries:
            name = entry["path"]
            _relative_path(name)
            if name.casefold() in names:
                raise ValueError("duplicate or case-colliding knowledge paths")
            names.add(name.casefold())
            if entry.get("type") == "tree":
                continue
            if entry.get("type") != "blob" or entry.get("mode") not in {"100644", "100755"}:
                raise ValueError("knowledge symlinks and submodules are not supported")
            size = entry.get("size")
            if type(size) is not int or not 0 <= size <= self.MAX_FILE_BYTES:
                raise ValueError("knowledge file exceeds the size limit")
            files.append({"path": name, "sha": self._sha(entry.get("sha")), "size": size, "type": "blob", "mode": "100644"})
        if not files or sum(item["size"] for item in files) > self.MAX_TOTAL_BYTES:
            raise ValueError("knowledge snapshot is empty or exceeds the size limit")
        return sorted(files, key=lambda item: item["path"])

    @staticmethod
    def _matches(root: Path, item: dict) -> bool:
        path = root / _relative_path(item["path"])
        try:
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                return False
            return path.is_file() and path.stat().st_size == item["size"] and _blob_sha(path.read_bytes()) == item["sha"]
        except OSError:
            return False

    def _restore(self) -> None:
        try:
            pointer = self.cache_root / "current.json"
            if pointer.is_symlink() or pointer.stat().st_size > 4096:
                return
            tree_sha = self._sha(json.loads(pointer.read_text(encoding="utf-8"))["tree_sha"])
            snapshot = self.cache_root / "snapshots" / tree_sha
            manifest_path = snapshot / "manifest.json"
            if snapshot.is_symlink() or manifest_path.is_symlink() or manifest_path.stat().st_size > self.MAX_METADATA_BYTES:
                return
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = self._files(manifest["files"])
            root = snapshot / "knowledge"
            if manifest["tree_sha"] != tree_sha or not all(self._matches(root, item) for item in files):
                return
            self._active_root, self._manifest = root, manifest
            self._status.update(state="cached", tree_sha=tree_sha, files=len(files), bytes=sum(item["size"] for item in files))
        except (OSError, ValueError, TypeError, KeyError):
            return

    def directory(self) -> Path:
        with self._guard:
            return self._active_root or self.bundled_root

    def status(self) -> dict:
        with self._guard:
            return {**self._status, "directory": str(self.directory()), "running": bool(self._thread and self._thread.is_alive()),
                    "interval_seconds": int(self.INTERVAL_SECONDS), "last_error": self._status.get("last_error", "")}

    def _publish_pointer(self, tree_sha: str, commit: str) -> None:
        self._check_stop()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.cache_root, prefix="current-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"tree_sha": tree_sha, "commit": commit, "checked_at": _now()}, stream)
        try:
            self._check_stop()
            os.replace(temporary, self.cache_root / "current.json")
        finally:
            temporary.unlink(missing_ok=True)

    def _sync(self) -> dict:
        branch = self._api(f"branches/{BRANCH}")
        commit = self._sha(branch["commit"]["sha"])
        source_tree = self._sha(branch["commit"]["commit"]["tree"]["sha"])
        root_tree = self._api(f"git/trees/{source_tree}")
        if root_tree.get("truncated") or root_tree.get("sha") != source_tree:
            raise ValueError("incomplete repository tree")
        folders = [item for item in root_tree["tree"] if item.get("path") == "knowledge" and item.get("type") == "tree"]
        if len(folders) != 1:
            raise ValueError("repository knowledge folder is missing")
        tree_sha = self._sha(folders[0]["sha"])
        active = self.directory()
        if self._manifest.get("tree_sha") == tree_sha and all(self._matches(active, item) for item in self._manifest["files"]):
            self._publish_pointer(tree_sha, commit)
            return {"state": "unchanged", "tree_sha": tree_sha, "commit": commit, "downloaded_files": 0,
                    "files": len(self._manifest["files"]), "bytes": sum(item["size"] for item in self._manifest["files"])}
        remote = self._api(f"git/trees/{tree_sha}?recursive=1")
        if remote.get("truncated") or remote.get("sha") != tree_sha:
            raise ValueError("incomplete knowledge tree; previous snapshot retained")
        files = self._files(remote.get("tree"))
        previous = {item["path"]: item for item in self._manifest.get("files", [])}
        snapshots = self.cache_root / "snapshots"
        snapshots.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        with tempfile.TemporaryDirectory(prefix="pending-", dir=snapshots) as temporary:
            staged = Path(temporary) / "snapshot"
            root = staged / "knowledge"
            root.mkdir(parents=True)
            for item in files:
                self._check_stop()
                relative = _relative_path(item["path"])
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if previous.get(item["path"]) == item and self._matches(active, item):
                    shutil.copyfile(active / relative, target)
                else:
                    url = f"https://raw.githubusercontent.com/{REPOSITORY}/{commit}/knowledge/" + quote(item["path"], safe="/")
                    payload = self._read(url, item["size"])
                    if len(payload) != item["size"] or _blob_sha(payload) != item["sha"]:
                        raise ValueError(f"knowledge checksum mismatch: {item['path']}")
                    target.write_bytes(payload)
                    downloaded += 1
            manifest = {"tree_sha": tree_sha, "commit": commit, "downloaded_at": _now(), "files": files}
            (staged / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            self._check_stop()
            final = snapshots / tree_sha
            if final.exists() or final.is_symlink():
                # Keep the replacement reversible until its directory rename succeeds.
                quarantine = Path(temporary) / "old-cache"
                final.rename(quarantine)
                try:
                    staged.rename(final)
                except OSError:
                    quarantine.rename(final)
                    raise
            else:
                staged.rename(final)
            self._publish_pointer(tree_sha, commit)
            with self._guard:
                self._active_root, self._manifest = final / "knowledge", manifest
        return {"state": "updated", "tree_sha": tree_sha, "commit": commit, "downloaded_files": downloaded,
                "files": len(files), "bytes": sum(item["size"] for item in files)}

    def sync_once(self) -> dict:
        with self._sync_guard:
            with self._guard:
                self._status.update(state="checking", checked_at=_now())
            try:
                self._check_stop()
                result = self._sync()
                result.update(last_success_at=_now(), last_error="")
            except _Cancelled:
                result = {"state": "stopped"}
            except Exception as exc:
                result = {"state": "error", "last_error": str(exc)[:500]}
            with self._guard:
                self._status.update(result)
                return dict(self._status)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="leshy-knowledge-sync", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            result = self.sync_once()
            self._stop.wait(self.RETRY_SECONDS if result["state"] == "error" else self.INTERVAL_SECONDS)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
