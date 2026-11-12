"""Explicitly approved, compare-and-swap Python edits in isolated Git worktrees.

This module does not accept unverified LLM authorizations, run source code,
commit, merge, or touch the main checkout. Approval must be enforced by caller.
"""
from __future__ import annotations

import ast
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import threading
from typing import Any

from .code_change_review import review_python_file
from .worktree_workspace import WorktreeWorkspace, WorkspaceError


_MAX_SOURCE = 256 * 1024


class PatchError(ValueError):
    pass


class WorktreePythonEditor:
    def __init__(self, workspaces: WorktreeWorkspace):
        self.workspaces = workspaces
        self._lock = threading.RLock()

    def _path(self, workspace: str, relative_path: str) -> tuple[Path, Path]:
        status = self.workspaces.status(workspace)
        root = Path(str(status["path"])).resolve(strict=True)
        if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
            raise PatchError("Valid relative Python path required")
        relative = Path(relative_path)
        if relative.is_absolute() or ".." in relative.parts or relative.suffix.lower() != ".py":
            raise PatchError("Only existing relative .py files may be edited")
        target = root / relative
        if target.is_symlink() or any(parent.is_symlink() for parent in target.parents if parent != root):
            raise PatchError("Symlinks are not allowed in edit path")
        if not target.resolve().is_relative_to(root) or not target.is_file():
            raise PatchError("Target is missing or outside isolated worktree")
        metadata = target.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_SOURCE:
            raise PatchError("Only bounded regular files are supported")
        return root, target

    @staticmethod
    def _digest(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def preview(self, workspace: str, relative_path: str) -> dict[str, Any]:
        with self._lock:
            root, target = self._path(workspace, relative_path)
            original = target.read_bytes()
            if len(original) > _MAX_SOURCE:
                raise PatchError("File size exceeded")
            try:
                original.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PatchError("Source must be UTF-8") from exc
            return {"workspace": workspace, "path": str(target.relative_to(root)),
                    "sha256": self._digest(original), "bytes": len(original)}

    def apply(self, workspace: str, relative_path: str, *, expected_sha256: str,
              replacement: str, approval_verified: bool = False) -> dict[str, Any]:
        if approval_verified is not True:
            raise PermissionError("Explicit server-validated approval required")
        if not isinstance(replacement, str):
            raise PatchError("Replacement must be text")
        encoded = replacement.encode("utf-8")
        if not encoded or len(encoded) > _MAX_SOURCE:
            raise PatchError("Replacement must be nonempty and at most 256 KiB")
        if not isinstance(expected_sha256, str) or len(expected_sha256) != 64:
            raise PatchError("Valid expected SHA-256 is required")
        # Static Python syntax check, not execution.
        try:
            ast.parse(replacement, filename=relative_path)
        except SyntaxError as exc:
            raise PatchError(f"Python syntax error at line {exc.lineno}: {exc.msg}") from exc
        with self._lock:
            root, target = self._path(workspace, relative_path)
            previous = target.read_bytes()
            if self._digest(previous) != expected_sha256:
                raise PatchError("File changed since preview; refusing stale update")
            if previous == encoded:
                raise PatchError("No changes to apply")
            previous_mode = stat.S_IMODE(target.stat().st_mode)
            temporary: str | None = None
            try:
                with tempfile.NamedTemporaryFile(mode="wb", prefix=".leshy-patch-",
                                                 dir=target.parent, delete=False) as stream:
                    temporary = stream.name
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(temporary, previous_mode)
                # One last stale-write check before publication.
                if self._digest(target.read_bytes()) != expected_sha256:
                    raise PatchError("File changed while preparing patch")
                os.replace(temporary, target)
                temporary = None
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except FileNotFoundError:
                        pass
            findings = review_python_file(root, str(target.relative_to(root)))
            return {"workspace": workspace, "path": str(target.relative_to(root)),
                    "before_sha256": expected_sha256, "after_sha256": self._digest(encoded),
                    "bytes": len(encoded), "findings": [entry.as_dict() for entry in findings]}
