from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from .diagnostics import event, problem
from .version import VERSION


class DiagnosticPublisher:
    """Периодически публикует санитизированные runtime-логи в diagnostics-ветку Git.

    Publisher никогда не пишет логи в main: используется отдельная ветка
    ``logs/windows-local`` и отдельная служебная копия репозитория в .autobuild.
    """

    BRANCH = "logs/windows-local"
    DEST = Path("log/windows-local/current")
    ALLOWED_SUFFIXES = {".log", ".txt", ".json"}
    MAX_FILE_CHARS = 2_000_000
    MAX_TOTAL_CHARS = 12_000_000

    def __init__(self, brain_dir: Path, *, interval_seconds: int | None = None) -> None:
        self.brain_dir = Path(brain_dir)
        self.log_dir = self.brain_dir / "logs"
        configured = interval_seconds
        if configured is None:
            try:
                configured = int(os.environ.get("LESHY_LOG_PUBLISH_SECONDS", "900"))
            except ValueError:
                configured = 900
        self.interval_seconds = max(60, int(configured))
        self.initial_delay_seconds = min(90, max(15, self.interval_seconds // 10))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="leshy-log-publisher", daemon=True)
        self._thread.start()
        event(
            "diagnostic_publisher_started",
            branch=self.BRANCH,
            interval_seconds=self.interval_seconds,
            source="LeshyBrain/logs",
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3)
        self._thread = None
        event("diagnostic_publisher_stopped")

    def _run(self) -> None:
        if self._stop.wait(self.initial_delay_seconds):
            return
        while not self._stop.is_set():
            try:
                changed = self.publish_once()
                if changed:
                    event("diagnostic_logs_published", branch=self.BRANCH)
            except Exception as exc:  # publisher must never break Leshy itself
                problem("diagnostic_publish_failed", str(exc), branch=self.BRANCH)
            if self._stop.wait(self.interval_seconds):
                break

    @staticmethod
    def _run_git(git: str, cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            [git, "-C", str(cwd), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stdout.strip()[:2000]}")
        return proc

    @staticmethod
    def _repo_root() -> Path | None:
        candidates: list[Path] = []
        try:
            exe = Path(sys.executable).resolve()
            candidates.extend(exe.parents)
        except Exception:
            pass
        try:
            here = Path(__file__).resolve()
            candidates.extend(here.parents)
        except Exception:
            pass
        for candidate in candidates:
            if (candidate / ".git").exists() and (candidate / "windows").exists():
                return candidate
        return None

    @staticmethod
    def _git_executable() -> str | None:
        found = shutil.which("git") or shutil.which("git.exe")
        if found:
            return found
        if os.name == "nt":
            candidates = [
                Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/cmd/git.exe",
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/cmd/git.exe",
            ]
            for path in candidates:
                if path.is_file():
                    return str(path)
        return None

    @staticmethod
    def _sanitize(text: str) -> str:
        value = text
        home = os.path.expanduser("~")
        if home and home != "~":
            value = re.sub(re.escape(home), "<home>", value, flags=re.IGNORECASE)
        userprofile = os.environ.get("USERPROFILE", "")
        if userprofile:
            value = re.sub(re.escape(userprofile), "<home>", value, flags=re.IGNORECASE)
        value = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", value)
        value = re.sub(r"(?i)(/bot)(\d+:[A-Za-z0-9_-]+)", r"\1<redacted>", value)
        value = re.sub(
            r"(?i)((?:token|secret|password|api[_-]?key|authorization|cookie|signature)=)([^&\s\"']+)",
            r"\1<redacted>",
            value,
        )
        value = re.sub(
            r'(?i)("(?:token|secret|password|api[_-]?key|authorization|cookie|signature)"\s*:\s*")[^"]*(")',
            r"\1<redacted>\2",
            value,
        )
        return value

    def _collect(self) -> list[tuple[Path, str]]:
        if not self.log_dir.is_dir():
            return []
        files = sorted(
            (
                path
                for path in self.log_dir.rglob("*")
                if path.is_file() and path.suffix.lower() in self.ALLOWED_SUFFIXES
            ),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        result: list[tuple[Path, str]] = []
        total = 0
        for path in files:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if len(text) > self.MAX_FILE_CHARS:
                text = "[truncated to last 2000000 characters]\n" + text[-self.MAX_FILE_CHARS :]
            if total + len(text) > self.MAX_TOTAL_CHARS:
                remaining = self.MAX_TOTAL_CHARS - total
                if remaining <= 0:
                    break
                text = "[truncated by total diagnostics limit]\n" + text[-remaining:]
            total += len(text)
            result.append((path.relative_to(self.log_dir), self._sanitize(text)))
            if total >= self.MAX_TOTAL_CHARS:
                break
        return result

    def publish_once(self) -> bool:
        repo = self._repo_root()
        git = self._git_executable()
        if repo is None or git is None:
            return False
        logs = self._collect()
        if not logs:
            return False

        origin = self._run_git(git, repo, "remote", "get-url", "origin").stdout.strip()
        if not origin:
            return False

        runtime = repo / ".autobuild"
        sync_repo = runtime / "log-sync-repo"
        dest = sync_repo / self.DEST
        runtime.mkdir(parents=True, exist_ok=True)

        if not (sync_repo / ".git").exists():
            if sync_repo.exists():
                shutil.rmtree(sync_repo, ignore_errors=True)
            clone = subprocess.run(
                [git, "clone", "--quiet", "--single-branch", "--branch", self.BRANCH, origin, str(sync_repo)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
            if clone.returncode != 0:
                raise RuntimeError(f"Could not clone diagnostics branch {self.BRANCH}: {clone.stdout.strip()[:2000]}")
        else:
            self._run_git(git, sync_repo, "fetch", "origin", self.BRANCH, "--quiet")
            self._run_git(git, sync_repo, "checkout", "-B", self.BRANCH, f"origin/{self.BRANCH}", "--quiet")
            self._run_git(git, sync_repo, "reset", "--hard", f"origin/{self.BRANCH}", "--quiet")

        # Несколько Windows-узлов публикуют в одну ветку. Раньше каждый
        # новый push удалял предыдущий current, и логи другого компьютера
        # безвозвратно пропадали между проверками. Перед заменой сохраняем
        # прежний санитизированный снимок в history.
        if dest.exists():
            old_meta = dest / "upload-metadata.json"
            if old_meta.is_file():
                try:
                    metadata_text = old_meta.read_text(encoding="utf-8")
                    old_info = json.loads(metadata_text)
                    timestamp = datetime.fromisoformat(str(old_info["uploaded_at"]).replace("Z", "+00:00"))
                    if timestamp.tzinfo is None:
                        raise ValueError("metadata timestamp has no timezone")
                    stamp = timestamp.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                    digest = hashlib.sha256(metadata_text.encode("utf-8")).hexdigest()[:16]
                    archive = sync_repo / "log/windows-local/history" / f"{stamp}-{digest}"
                    if not archive.exists():
                        archive.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copytree(dest, archive)
                except (ValueError, KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
                    # Не удаляем единственный экземпляр диагностики при
                    # повреждённых метаданных: следующий запуск повторит.
                    raise RuntimeError("Cannot archive previous sanitized logs before replacement") from exc
            else:
                raise RuntimeError("Previous diagnostics snapshot has no metadata; refusing to overwrite")
            shutil.rmtree(dest)
        dest.mkdir(parents=True, exist_ok=True)

        for relative, text in logs:
            target = dest / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")

        commit = self._run_git(git, repo, "rev-parse", "HEAD").stdout.strip()
        metadata = {
            "version": VERSION,
            "commit": commit,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "source": "dist/Leshy/LeshyBrain/logs",
            "sanitized": True,
            "publisher": "leshy-runtime",
            "interval_seconds": self.interval_seconds,
            "files": [str(relative).replace("\\", "/") for relative, _ in logs],
        }
        (dest / "upload-metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )

        self._run_git(git, sync_repo, "config", "user.name", "Leshy Runtime")
        self._run_git(git, sync_repo, "config", "user.email", "leshy-runtime@localhost")
        self._run_git(git, sync_repo, "add", "-A", "--", str(self.DEST).replace("\\", "/"))
        diff = self._run_git(git, sync_repo, "diff", "--cached", "--quiet", check=False)
        if diff.returncode == 0:
            return False

        short = commit[:8] if commit else "runtime"
        self._run_git(git, sync_repo, "commit", "-m", f"logs: Windows runtime diagnostics {short}", "--quiet")
        self._run_git(git, sync_repo, "push", "origin", f"HEAD:{self.BRANCH}", "--quiet")
        return True
