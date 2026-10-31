from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib import error, parse, request

from .diagnostics import event, problem
from .secret_store import load_secret, save_secret


_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PATH_RE = re.compile(
    r"(?P<path>(?:src|web|windows|tests|docs|\.github|models)/[A-Za-z0-9_./()\-]+|[A-Za-z0-9_.-]+\.(?:py|js|css|html|md|json|ps1|yml|yaml|toml|txt))",
    re.IGNORECASE,
)
_MAX_FILE_CHARS = 70000
_MAX_RESULT_CHARS = 180000
_DEFAULT_REPOSITORY = "jaster351/leshy-AI"


class GitHubPluginService:
    """Безопасный GitHub-коннектор для официального Marketplace-плагина.

    Чатовый инструмент намеренно работает только на чтение. Репозитории должны
    быть явно разрешены в локальной конфигурации. GitHub token хранится отдельно
    от репозитория через secret_store (DPAPI в Windows).
    """

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.settings_dir = self.brain_dir / "settings"
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.secrets_dir = self.brain_dir / "secrets"
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.settings_dir / "github-plugin.json"
        self.token_path = self.secrets_dir / "github_token.dat"

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
        except Exception:
            return default

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)

    def _config(self) -> dict[str, Any]:
        value = self._read_json(
            self.config_path,
            {"repositories": [_DEFAULT_REPOSITORY], "default_repository": _DEFAULT_REPOSITORY, "write_enabled": False},
        )
        if not isinstance(value, dict):
            value = {}
        repos = []
        for item in value.get("repositories") or [_DEFAULT_REPOSITORY]:
            repo = str(item or "").strip()
            if _REPO_RE.fullmatch(repo) and repo not in repos:
                repos.append(repo)
        if not repos:
            repos = [_DEFAULT_REPOSITORY]
        default = str(value.get("default_repository") or repos[0]).strip()
        if default not in repos:
            default = repos[0]
        return {
            "repositories": repos[:50],
            "default_repository": default,
            "write_enabled": bool(value.get("write_enabled", False)),
        }

    def status(self) -> dict[str, Any]:
        config = self._config()
        token_present = False
        try:
            token_present = bool(load_secret(self.token_path))
        except Exception as exc:
            problem("github_plugin_secret_load_failed", str(exc))
        return {
            "installed": True,
            "enabled": True,
            "token_present": token_present,
            "repositories": list(config["repositories"]),
            "default_repository": config["default_repository"],
            "write_enabled": bool(config["write_enabled"]),
            "chat_mode": "read_only",
            "public_without_token": True,
            "secret_storage": "DPAPI" if os.name == "nt" else "local-secret-file",
        }

    def configure(
        self,
        *,
        repositories: list[str] | tuple[str, ...] | None = None,
        default_repository: str = "",
        token: str = "",
        clear_token: bool = False,
        write_enabled: bool | None = None,
    ) -> dict[str, Any]:
        current = self._config()
        if repositories is None:
            repos = list(current["repositories"])
        else:
            repos = []
            for item in repositories:
                repo = str(item or "").strip()
                if not repo:
                    continue
                if not _REPO_RE.fullmatch(repo):
                    raise ValueError(f"Некорректное имя репозитория: {repo}")
                if repo not in repos:
                    repos.append(repo)
            if not repos:
                raise ValueError("Нужно разрешить хотя бы один репозиторий")
            if len(repos) > 50:
                raise ValueError("Можно разрешить не более 50 репозиториев")
        default = str(default_repository or current.get("default_repository") or repos[0]).strip()
        if default not in repos:
            raise ValueError("Репозиторий по умолчанию должен быть в списке разрешённых")
        config = {
            "repositories": repos,
            "default_repository": default,
            "write_enabled": bool(current["write_enabled"] if write_enabled is None else write_enabled),
        }
        self._write_json(self.config_path, config)
        if clear_token:
            try:
                self.token_path.unlink(missing_ok=True)
            except OSError as exc:
                raise RuntimeError(f"Не удалось удалить GitHub token: {exc}") from exc
        elif str(token or "").strip():
            save_secret(self.token_path, str(token).strip())
        event(
            "github_plugin_configured",
            repositories=len(repos),
            default_repository=default,
            token_present=bool(str(token or "").strip()) or self.token_path.is_file(),
            write_enabled=config["write_enabled"],
        )
        return self.status()

    def _token(self) -> str:
        try:
            return load_secret(self.token_path)
        except Exception as exc:
            problem("github_plugin_secret_load_failed", str(exc))
            return ""

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": "Leshy-AI-GitHub-Plugin",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        token = self._token()
        if token:
            headers["Authorization"] = "Bearer " + token
        return headers

    def _api(self, path: str, *, params: dict[str, Any] | None = None, timeout: int = 20) -> Any:
        suffix = parse.urlencode(params or {}, doseq=True)
        url = "https://api.github.com" + path + (("?" + suffix) if suffix else "")
        req = request.Request(url, headers=self._headers(), method="GET")
        try:
            with request.urlopen(req, timeout=timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
        except error.HTTPError as exc:
            detail = ""
            try:
                body = json.loads(exc.read(65536).decode("utf-8", errors="replace"))
                detail = str(body.get("message") or "") if isinstance(body, dict) else ""
            except Exception:
                pass
            if exc.code == 401:
                raise RuntimeError("GitHub отклонил token. Проверьте token в настройках плагина.") from exc
            if exc.code == 403:
                raise RuntimeError("GitHub API запретил запрос или исчерпан лимит. Для повышенного лимита подключите token.") from exc
            if exc.code == 404:
                raise FileNotFoundError("GitHub не нашёл запрошенный объект или у token нет доступа") from exc
            raise RuntimeError(f"GitHub HTTP {exc.code}: {detail or exc.reason}") from exc
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError("Ответ GitHub слишком большой")
        return json.loads(raw.decode("utf-8")) if raw else None

    def _allowed_repo(self, message: str) -> str:
        config = self._config()
        allowed = list(config["repositories"])
        candidates = re.findall(r"(?<![\w.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w./-])", str(message or ""))
        for candidate in candidates:
            if candidate in allowed:
                return candidate
            if _REPO_RE.fullmatch(candidate):
                raise PermissionError(f"Репозиторий {candidate} не разрешён в настройках GitHub-плагина")
        return str(config["default_repository"])

    @staticmethod
    def _path_from_message(message: str) -> str:
        match = _PATH_RE.search(str(message or ""))
        return str(match.group("path") if match else "").strip("`'\".,:; ")

    def repository_info(self, repo: str) -> dict[str, Any]:
        data = self._api(f"/repos/{repo}")
        return {
            "full_name": data.get("full_name"),
            "description": data.get("description"),
            "default_branch": data.get("default_branch"),
            "private": bool(data.get("private")),
            "language": data.get("language"),
            "updated_at": data.get("updated_at"),
            "open_issues_count": data.get("open_issues_count"),
        }

    def read_file(self, repo: str, path: str, *, ref: str = "") -> dict[str, Any]:
        params = {"ref": ref} if ref else None
        data = self._api(f"/repos/{repo}/contents/{parse.quote(path, safe='/')}", params=params)
        if not isinstance(data, dict) or str(data.get("type")) != "file":
            raise FileNotFoundError(f"{path} не является файлом")
        content = str(data.get("content") or "").replace("\n", "")
        encoding = str(data.get("encoding") or "")
        if encoding == "base64":
            decoded = base64.b64decode(content).decode("utf-8", errors="replace")
        else:
            decoded = content
        truncated = len(decoded) > _MAX_FILE_CHARS
        return {
            "repository": repo,
            "path": path,
            "sha": str(data.get("sha") or ""),
            "html_url": str(data.get("html_url") or ""),
            "content": decoded[:_MAX_FILE_CHARS],
            "truncated": truncated,
        }

    def list_commits(self, repo: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._api(f"/repos/{repo}/commits", params={"per_page": max(1, min(limit, 20))})
        result = []
        for row in rows if isinstance(rows, list) else []:
            commit = row.get("commit") if isinstance(row, dict) else {}
            result.append({
                "sha": str(row.get("sha") or "")[:12],
                "message": str((commit or {}).get("message") or "").splitlines()[0],
                "date": str((((commit or {}).get("committer") or {}).get("date")) or ""),
                "url": str(row.get("html_url") or ""),
            })
        return result

    def list_issues(self, repo: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._api(f"/repos/{repo}/issues", params={"state": "open", "per_page": max(1, min(limit, 20))})
        result = []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("pull_request"):
                continue
            result.append({"number": row.get("number"), "title": row.get("title"), "state": row.get("state"), "url": row.get("html_url")})
        return result

    def list_pulls(self, repo: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self._api(f"/repos/{repo}/pulls", params={"state": "open", "per_page": max(1, min(limit, 20))})
        result = []
        for row in rows if isinstance(rows, list) else []:
            result.append({
                "number": row.get("number"), "title": row.get("title"), "state": row.get("state"),
                "head": str(((row.get("head") or {}).get("ref")) or ""),
                "base": str(((row.get("base") or {}).get("ref")) or ""), "url": row.get("html_url"),
            })
        return result

    @staticmethod
    def _search_hints(message: str) -> list[str]:
        text = str(message or "").lower()
        mapping = {
            "телеграм": ["telegram"], "telegram": ["telegram"],
            "голос": ["voice"], "микрофон": ["voice"], "озвуч": ["voice", "tts"],
            "маркет": ["marketplace"], "плагин": ["plugin", "marketplace"],
            "памят": ["memory"], "поиск": ["search", "research"], "интернет": ["research"],
            "веб": ["web", "chat"], "чат": ["chat", "realtime"],
            "модел": ["model", "llm"], "gpu": ["accelerator", "runtime"], "npu": ["accelerator", "openvino"],
            "github": ["github"], "гитхаб": ["github"], "автобил": ["autobuilder"],
        }
        hints: list[str] = []
        for needle, values in mapping.items():
            if needle in text:
                for value in values:
                    if value not in hints:
                        hints.append(value)
        words = re.findall(r"[A-Za-z_][A-Za-z0-9_-]{3,}", text)
        for word in words:
            if word not in {"github", "file", "code", "commit", "issue", "pull", "request"} and word not in hints:
                hints.append(word)
        return hints[:8]

    def _tree_matches(self, repo: str, message: str, limit: int = 6) -> list[str]:
        info = self.repository_info(repo)
        branch = str(info.get("default_branch") or "main")
        data = self._api(f"/repos/{repo}/git/trees/{parse.quote(branch, safe='')}", params={"recursive": "1"})
        rows = data.get("tree", []) if isinstance(data, dict) else []
        hints = self._search_hints(message)
        scored: list[tuple[int, str]] = []
        for row in rows:
            if not isinstance(row, dict) or row.get("type") != "blob":
                continue
            path = str(row.get("path") or "")
            low = path.lower()
            score = sum(8 if hint in low.rsplit("/", 1)[-1] else 3 for hint in hints if hint in low)
            if score:
                if low.startswith(("src/", "web/", "windows/")):
                    score += 2
                scored.append((score, path))
        scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
        return [path for _score, path in scored[:limit]]

    def search_code(self, repo: str, message: str, limit: int = 5) -> list[dict[str, Any]]:
        token = self._token()
        hints = self._search_hints(message)
        if token and hints:
            try:
                query = " ".join(hints[:4]) + f" repo:{repo}"
                data = self._api("/search/code", params={"q": query, "per_page": max(1, min(limit, 10))})
                rows = data.get("items", []) if isinstance(data, dict) else []
                if rows:
                    return [{"path": row.get("path"), "url": row.get("html_url")} for row in rows[:limit]]
            except Exception as exc:
                problem("github_plugin_code_search_failed", str(exc), repository=repo)
        return [{"path": path, "url": f"https://github.com/{repo}/blob/HEAD/{path}"} for path in self._tree_matches(repo, message, limit=limit)]

    def execute(self, message: str) -> dict[str, Any]:
        repo = self._allowed_repo(message)
        text = str(message or "")
        lower = text.lower()
        event("github_plugin_query_started", repository=repo)

        path = self._path_from_message(text)
        if path:
            result = {"kind": "file", "file": self.read_file(repo, path)}
        elif any(token in lower for token in ("pull request", "pull-request", "пулл-реквест", " pr ", " pr?", "pr #")):
            result = {"kind": "pull_requests", "repository": repo, "items": self.list_pulls(repo)}
        elif any(token in lower for token in ("issue", "issues", "ишью", "задач github", "задачи github")):
            result = {"kind": "issues", "repository": repo, "items": self.list_issues(repo)}
        elif any(token in lower for token in ("commit", "commits", "коммит", "история изменений")):
            result = {"kind": "commits", "repository": repo, "items": self.list_commits(repo)}
        elif any(token in lower for token in ("репозитор", "repository")) and not self._search_hints(text):
            result = {"kind": "repository", "repository": self.repository_info(repo)}
        else:
            matches = self.search_code(repo, text, limit=5)
            files = []
            for item in matches[:2]:
                path = str(item.get("path") or "")
                if not path:
                    continue
                try:
                    files.append(self.read_file(repo, path))
                except Exception as exc:
                    files.append({"path": path, "error": str(exc)})
            result = {"kind": "code_search", "repository": repo, "matches": matches, "files": files}

        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if len(rendered) > _MAX_RESULT_CHARS:
            rendered = rendered[:_MAX_RESULT_CHARS] + "\n…[результат сокращён]"
        event("github_plugin_query_finished", repository=repo, result_chars=len(rendered))
        return {"ok": True, "text": rendered, "repository": repo, "kind": result.get("kind")}


__all__ = ["GitHubPluginService"]
