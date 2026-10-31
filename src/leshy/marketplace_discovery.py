from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
from urllib import parse, request

from .diagnostics import event, problem

_GITHUB_API = "https://api.github.com"
_USER_AGENT = "Leshy-AI/Marketplace-Discovery"
_MAX_REPOS_PER_QUERY = 6
_DEFAULT_QUERIES = (
    "leshy plugin ai assistant",
    "AI agent skill SKILL.md",
    "LLM agent skills SKILL.md",
)


def _github_json(url: str) -> Any:
    req = request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with request.urlopen(req, timeout=18) as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError("Ответ GitHub для Marketplace слишком большой")
    return json.loads(data.decode("utf-8"))


def _github_file(full_name: str, path: str, branch: str) -> tuple[str, str] | None:
    encoded_path = "/".join(parse.quote(part, safe="") for part in path.split("/"))
    url = f"{_GITHUB_API}/repos/{full_name}/contents/{encoded_path}?ref={parse.quote(branch, safe='')}"
    try:
        payload = _github_json(url)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "file":
        return None
    encoded = str(payload.get("content") or "").replace("\n", "")
    if not encoded:
        return None
    try:
        text = base64.b64decode(encoded).decode("utf-8", errors="replace")
    except Exception:
        return None
    return text, str(payload.get("download_url") or "")


def _safe_id(full_name: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", full_name.lower().replace("/", ".")).strip("-.")
    value = re.sub(r"[-.]{2,}", "-", value)
    return ("gh." + value)[:78].rstrip("-.")


def _developer(repo: dict[str, Any]) -> dict[str, str]:
    owner = repo.get("owner") if isinstance(repo.get("owner"), dict) else {}
    return {
        "name": str(owner.get("login") or "GitHub developer")[:200],
        "url": str(owner.get("html_url") or "")[:1000],
    }


def _license(repo: dict[str, Any]) -> str:
    value = repo.get("license") if isinstance(repo.get("license"), dict) else {}
    return str(value.get("spdx_id") or value.get("name") or "")[:120]


def _native_manifest_item(manager, repo: dict[str, Any], manifest: dict[str, Any], manifest_url: str) -> dict[str, Any] | None:
    full_name = str(repo.get("full_name") or "")
    branch = str(repo.get("default_branch") or "main")
    if not full_name:
        return None
    row = dict(manifest)
    row.setdefault("id", _safe_id(full_name))
    row.setdefault("name", str(repo.get("name") or row["id"]))
    row.setdefault("version", str(manifest.get("version") or repo.get("pushed_at") or "github"))
    row.setdefault("description", str(repo.get("description") or "Расширение, найденное Лешим на GitHub."))
    row.setdefault("developer", _developer(repo))
    row.setdefault("homepage", str(repo.get("html_url") or ""))
    row.setdefault("license", _license(repo))
    row.setdefault("permissions", list(manifest.get("permissions") or []))
    row["package_url"] = f"https://github.com/{full_name}/archive/refs/heads/{branch}.zip"
    row["manifest_url"] = manifest_url
    normalized = manager._normalize_catalog_item(row, source_name=f"GitHub · {full_name}")
    normalized["discovered"] = True
    normalized["repository"] = str(repo.get("html_url") or "")
    normalized["generic_skill"] = False
    return normalized


def _generic_skill_item(manager, repo: dict[str, Any], skill_text: str, skill_url: str) -> dict[str, Any] | None:
    full_name = str(repo.get("full_name") or "")
    branch = str(repo.get("default_branch") or "main")
    if not full_name:
        return None
    heading = next((line.lstrip("# ").strip() for line in skill_text.splitlines() if line.strip().startswith("#")), "")
    description = str(repo.get("description") or "").strip()
    if not description:
        body_line = next((line.strip() for line in skill_text.splitlines() if line.strip() and not line.strip().startswith("#")), "")
        description = body_line[:500] or "Skill из SKILL.md, автоматически найденный Лешим."
    row = {
        "id": _safe_id(full_name),
        "name": heading[:160] or str(repo.get("name") or full_name.split("/")[-1]),
        "type": "skill",
        "version": str(repo.get("pushed_at") or "github")[:80],
        "description": description[:3000],
        "developer": _developer(repo),
        "homepage": str(repo.get("html_url") or ""),
        "license": _license(repo),
        "permissions": [],
        "package_url": f"https://github.com/{full_name}/archive/refs/heads/{branch}.zip",
        "manifest_url": skill_url,
        "sha256": "",
    }
    normalized = manager._normalize_catalog_item(row, source_name=f"GitHub Skill · {full_name}")
    normalized["discovered"] = True
    normalized["repository"] = str(repo.get("html_url") or "")
    normalized["generic_skill"] = True
    normalized["skill_file"] = "SKILL.md"
    return normalized


def discover(manager, query: str = "") -> dict[str, Any]:
    """Найти совместимые расширения без ручного ввода catalog.json пользователем."""
    queries = [str(query).strip()] if str(query).strip() else list(_DEFAULT_QUERIES)
    merged = {str(item.get("id")): dict(item) for item in getattr(manager, "_catalog", []) if item.get("id")}
    errors: list[str] = []
    seen_repos: set[str] = set()
    discovered = 0

    for term in queries:
        search_url = _GITHUB_API + "/search/repositories?" + parse.urlencode({
            "q": term,
            "sort": "updated",
            "order": "desc",
            "per_page": _MAX_REPOS_PER_QUERY,
        })
        try:
            payload = _github_json(search_url)
        except Exception as exc:
            errors.append(f"GitHub search {term}: {exc}")
            problem("marketplace_github_search_failed", str(exc), query=term)
            continue
        for repo in payload.get("items", []) if isinstance(payload, dict) else []:
            if not isinstance(repo, dict):
                continue
            full_name = str(repo.get("full_name") or "")
            if not full_name or full_name in seen_repos or bool(repo.get("archived")):
                continue
            seen_repos.add(full_name)
            branch = str(repo.get("default_branch") or "main")
            item = None
            for candidate in ("leshy-plugin.json", ".leshy/leshy-plugin.json", "plugin/leshy-plugin.json"):
                found = _github_file(full_name, candidate, branch)
                if not found:
                    continue
                text, raw_url = found
                try:
                    manifest = json.loads(text)
                    if isinstance(manifest, dict):
                        item = _native_manifest_item(manager, repo, manifest, raw_url)
                except Exception as exc:
                    errors.append(f"{full_name}/{candidate}: {exc}")
                if item:
                    break
            if item is None:
                found = _github_file(full_name, "SKILL.md", branch)
                if found:
                    text, raw_url = found
                    item = _generic_skill_item(manager, repo, text, raw_url)
            if item:
                merged[item["id"]] = item
                discovered += 1

    manager._catalog = sorted(merged.values(), key=lambda item: (str(item.get("name") or "").lower(), str(item.get("id") or "")))
    manager._write_json(
        manager.cache_path,
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "items": manager._catalog,
            "errors": errors[:50],
            "auto_discovery": True,
        },
    )
    event("marketplace_auto_discovery_finished", discovered=discovered, total=len(manager._catalog), errors=len(errors))
    return {"items": manager.list_items(), "discovered": discovered, "errors": errors[:50], "sources": manager.sources()}


def install(manager, item_id: str) -> dict[str, Any]:
    """Установить native leshy-plugin либо найденный generic SKILL.md."""
    entry = manager._catalog_entry(item_id)
    if not bool(entry.get("generic_skill")):
        return manager.install(item_id)

    with tempfile.TemporaryDirectory(prefix="leshy-marketplace-skill-") as temp_dir:
        temp = Path(temp_dir)
        archive = temp / "package.zip"
        manager._download_package(str(entry["package_url"]), archive, expected_sha256="")
        extracted = temp / "extracted"
        extracted.mkdir()
        manager._safe_extract(archive, extracted)
        skill_path = next(iter(extracted.rglob("SKILL.md")), None)
        if skill_path is None:
            raise ValueError("В найденном репозитории больше нет SKILL.md")
        source_root = skill_path.parent
        target = manager.root / str(entry["id"])
        staging = manager.root / ("." + str(entry["id"]) + ".installing")
        backup = manager.root / ("." + str(entry["id"]) + ".backup")
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(source_root, staging)
        manifest = {
            "schema_version": 1,
            "id": entry["id"],
            "name": entry.get("name") or entry["id"],
            "type": "skill",
            "version": entry.get("version") or "github",
            "description": entry.get("description") or "GitHub SKILL.md",
            "developer": entry.get("developer") or {},
            "homepage": entry.get("homepage") or entry.get("repository") or "",
            "license": entry.get("license") or "",
            "permissions": entry.get("permissions") or [],
            "skill": {"instructions_file": "SKILL.md"},
        }
        (staging / "leshy-plugin.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        shutil.rmtree(backup, ignore_errors=True)
        if target.exists():
            target.replace(backup)
        try:
            staging.replace(target)
            shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            if backup.exists():
                backup.replace(target)
            raise

    state = manager._state()
    state["extensions"][str(entry["id"])] = {
        "enabled": True,
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "source": "github-auto-discovery",
    }
    manager._save_state(state)
    event("marketplace_discovered_skill_installed", id=entry["id"], version=entry.get("version"))
    return {"ok": True, "item": next(row for row in manager.installed() if row["id"] == entry["id"])}


__all__ = ["discover", "install"]
