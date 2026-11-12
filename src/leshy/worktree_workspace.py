"""Dedicated Git worktrees for parallel coding agents.

No shell invocation, remote fetch, implicit commit, or destructive force-removal.
This module is infrastructure; callers must still apply the approval policy.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess


_SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,127}$")


class WorkspaceError(RuntimeError):
    pass


class WorktreeWorkspace:
    def __init__(self, repo: str | Path, *, managed_root: str | Path | None = None):
        self.repo = Path(repo).resolve(strict=True)
        self.root = Path(managed_root).resolve() if managed_root else self.repo.parent / (self.repo.name + '-leshy-worktrees')
        self.root = self.root.resolve()
        if self.root == self.repo or self.repo in self.root.parents or self.root in self.repo.parents:
            raise WorkspaceError('Worktrees must live outside the source repository')
        top = self._git('-C', str(self.repo), 'rev-parse', '--show-toplevel').strip()
        if Path(top).resolve() != self.repo:
            raise WorkspaceError('Repository root required')

    @staticmethod
    def _git(*args: str, timeout: int = 60) -> str:
        try:
            result = subprocess.run(('git', *args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding='utf-8', errors='replace', timeout=timeout,
                                    shell=False, check=False)
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise WorkspaceError('Git command unavailable or timed out') from exc
        if result.returncode:
            raise WorkspaceError((result.stderr or result.stdout).strip()[:600])
        return result.stdout

    def _destination(self, name: str) -> Path:
        if not isinstance(name, str) or not _SAFE.fullmatch(name) or name in {'.', '..'}:
            raise WorkspaceError('Invalid agent workspace name')
        dest = self.root / name
        if dest.resolve() != dest or dest.parent.resolve() != self.root:
            raise WorkspaceError('Workspace path escapes managed directory')
        return dest

    def create(self, name: str, *, base_ref: str = 'HEAD') -> dict[str, str]:
        dest = self._destination(name)
        if dest.exists() or dest.is_symlink():
            raise WorkspaceError('Workspace already exists')
        if not isinstance(base_ref, str) or not _REF.fullmatch(base_ref) or '..' in base_ref or '//' in base_ref:
            raise WorkspaceError('Invalid ref')
        # Resolve to one immutable commit, avoiding branch checkout races.
        sha = self._git('-C', str(self.repo), 'rev-parse', '--verify', f'{base_ref}^{{commit}}').strip()
        self.root.mkdir(parents=True, exist_ok=True)
        self._git('-C', str(self.repo), 'worktree', 'add', '--detach', str(dest), sha)
        return {'name': name, 'path': str(dest), 'base_commit': sha}

    def status(self, name: str) -> dict[str, str | bool]:
        dest = self._destination(name)
        if not dest.is_dir() or dest.is_symlink():
            raise WorkspaceError('Workspace missing or replaced')
        top = Path(self._git('-C', str(dest), 'rev-parse', '--show-toplevel').strip()).resolve()
        if top != dest:
            raise WorkspaceError('Workspace root mismatch')
        head = self._git('-C', str(dest), 'rev-parse', 'HEAD').strip()
        changes = self._git('-C', str(dest), 'status', '--porcelain', '-uno', timeout=30)
        return {'name': name, 'path': str(dest), 'commit': head, 'dirty': bool(changes.strip())}

    def remove_clean(self, name: str) -> None:
        dest = self._destination(name)
        if self.status(name)['dirty']:
            raise WorkspaceError('Workspace has modifications; refusing to remove')
        # Git refuses to remove untracked changes by default; never use --force.
        self._git('-C', str(self.repo), 'worktree', 'remove', str(dest))
