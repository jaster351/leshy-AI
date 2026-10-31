from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
from typing import Any
from urllib import parse, request
import zipfile

from .diagnostics import event, problem


MAX_CATALOG_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_BYTES = 256 * 1024 * 1024
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")


@dataclass(slots=True)
class MarketplaceItem:
    id: str
    name: str
    kind: str
    version: str
    description: str
    developer_name: str
    developer_url: str
    homepage: str
    license: str
    permissions: list[str]
    package_url: str
    manifest_url: str
    sha256: str
    source_name: str
    installed: bool = False
    enabled: bool = False
    installed_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.kind,
            "version": self.version,
            "description": self.description,
            "developer": {"name": self.developer_name, "url": self.developer_url},
            "homepage": self.homepage,
            "license": self.license,
            "permissions": list(self.permissions),
            "package_url": self.package_url,
            "manifest_url": self.manifest_url,
            "sha256": self.sha256,
            "source_name": self.source_name,
            "installed": self.installed,
            "enabled": self.enabled,
            "installed_version": self.installed_version,
            "update_available": bool(self.installed and self.version and self.installed_version and self.version != self.installed_version),
        }


class MarketplaceManager:
    """Marketplace расширений Лешего.

    Формат расширения открыт: каталог публикует метаданные, а пакет содержит
    `leshy-plugin.json`. Skills могут дополнительно содержать `SKILL.md`.
    Удалённый произвольный Python-код автоматически в процесс Лешего не импортируется.
    """

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.root = self.brain_dir / "plugins"
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings_dir = self.brain_dir / "settings"
        self.settings_dir.mkdir(parents=True, exist_ok=True)
        self.sources_path = self.settings_dir / "marketplace-sources.json"
        self.state_path = self.settings_dir / "marketplace-state.json"
        self.cache_path = self.settings_dir / "marketplace-cache.json"
        self._catalog: list[dict[str, Any]] = []
        self._load_cache()

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

    def _state(self) -> dict[str, Any]:
        value = self._read_json(self.state_path, {"extensions": {}, "delete_approvals": {}})
        if not isinstance(value, dict):
            value = {}
        value.setdefault("extensions", {})
        value.setdefault("delete_approvals", {})
        return value

    def _save_state(self, value: dict[str, Any]) -> None:
        self._write_json(self.state_path, value)

    def sources(self) -> list[dict[str, str]]:
        value = self._read_json(self.sources_path, {"sources": []})
        rows = value.get("sources", []) if isinstance(value, dict) else []
        result: list[dict[str, str]] = []
        for row in rows:
            if isinstance(row, dict) and str(row.get("url") or "").strip():
                result.append({"name": str(row.get("name") or "Каталог"), "url": str(row["url"]).strip()})
        return result

    def add_source(self, url: str, name: str = "") -> list[dict[str, str]]:
        url = self._validate_https_url(url)
        rows = self.sources()
        if not any(item["url"] == url for item in rows):
            rows.append({"name": str(name or parse.urlparse(url).netloc or "Каталог")[:120], "url": url})
            self._write_json(self.sources_path, {"sources": rows})
            event("marketplace_source_added", url=url)
        return rows

    def remove_source(self, url: str) -> list[dict[str, str]]:
        value = str(url or "").strip()
        rows = [item for item in self.sources() if item["url"] != value]
        self._write_json(self.sources_path, {"sources": rows})
        event("marketplace_source_removed", url=value)
        return rows

    @staticmethod
    def _validate_https_url(url: str) -> str:
        value = str(url or "").strip()
        parsed = parse.urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Marketplace принимает только HTTPS URL")
        return value

    @staticmethod
    def _download_json(url: str) -> Any:
        req = request.Request(url, headers={"User-Agent": "Leshy-AI/Marketplace", "Accept": "application/json"})
        with request.urlopen(req, timeout=20) as response:
            length = int(response.headers.get("Content-Length") or 0)
            if length and length > MAX_CATALOG_BYTES:
                raise ValueError("Каталог Marketplace слишком большой")
            data = response.read(MAX_CATALOG_BYTES + 1)
        if len(data) > MAX_CATALOG_BYTES:
            raise ValueError("Каталог Marketplace слишком большой")
        return json.loads(data.decode("utf-8"))

    def _load_cache(self) -> None:
        value = self._read_json(self.cache_path, {"items": []})
        items = value.get("items", []) if isinstance(value, dict) else []
        self._catalog = [dict(item) for item in items if isinstance(item, dict)]

    def refresh(self) -> dict[str, Any]:
        merged: dict[str, dict[str, Any]] = {}
        errors: list[dict[str, str]] = []
        for source in self.sources():
            try:
                payload = self._download_json(source["url"])
                rows = payload.get("items", []) if isinstance(payload, dict) else []
                source_name = str(payload.get("name") or source["name"]) if isinstance(payload, dict) else source["name"]
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    normalized = self._normalize_catalog_item(row, source_name=source_name)
                    merged[normalized["id"]] = normalized
            except Exception as exc:
                errors.append({"url": source["url"], "error": str(exc)[:500]})
                problem("marketplace_catalog_refresh_failed", str(exc), url=source["url"])
        self._catalog = sorted(merged.values(), key=lambda item: (str(item.get("name") or "").lower(), item["id"]))
        self._write_json(self.cache_path, {"updated_at": datetime.now(timezone.utc).isoformat(), "items": self._catalog, "errors": errors})
        event("marketplace_refreshed", items=len(self._catalog), sources=len(self.sources()), errors=len(errors))
        return {"items": self.list_items(), "errors": errors, "sources": self.sources()}

    def _normalize_catalog_item(self, row: dict[str, Any], *, source_name: str) -> dict[str, Any]:
        item_id = str(row.get("id") or "").strip().lower()
        if not _ID_RE.fullmatch(item_id):
            raise ValueError(f"Некорректный id расширения: {item_id!r}")
        kind = str(row.get("type") or row.get("kind") or "plugin").strip().lower()
        if kind not in {"plugin", "skill"}:
            raise ValueError(f"Неподдерживаемый тип расширения: {kind}")
        developer = row.get("developer") if isinstance(row.get("developer"), dict) else {}
        package_url = self._validate_https_url(str(row.get("package_url") or ""))
        manifest_url = str(row.get("manifest_url") or "").strip()
        if manifest_url:
            manifest_url = self._validate_https_url(manifest_url)
        return {
            "id": item_id,
            "name": str(row.get("name") or item_id)[:160],
            "type": kind,
            "version": str(row.get("version") or "0")[:80],
            "description": str(row.get("description") or "")[:3000],
            "developer": {"name": str(developer.get("name") or row.get("author") or "Не указан")[:200], "url": str(developer.get("url") or "")[:1000]},
            "homepage": str(row.get("homepage") or "")[:1000],
            "license": str(row.get("license") or "")[:120],
            "permissions": [str(x)[:120] for x in (row.get("permissions") or []) if str(x).strip()][:50],
            "package_url": package_url,
            "manifest_url": manifest_url,
            "sha256": str(row.get("sha256") or "").lower().strip(),
            "source_name": str(source_name or "Marketplace")[:160],
        }

    def _installed_manifest(self, path: Path) -> dict[str, Any] | None:
        manifest = path / "leshy-plugin.json"
        if manifest.is_file():
            value = self._read_json(manifest, None)
            if isinstance(value, dict):
                return value
        skill = path / "SKILL.md"
        if skill.is_file():
            text = skill.read_text(encoding="utf-8", errors="replace")
            name = path.name
            first = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), "")
            return {
                "schema_version": 1,
                "id": path.name.lower(),
                "name": first or name,
                "type": "skill",
                "version": "local",
                "description": "Локальный skill из SKILL.md",
                "developer": {"name": "Локальная установка", "url": ""},
                "permissions": [],
                "skill": {"instructions_file": "SKILL.md"},
            }
        return None

    def installed(self) -> list[dict[str, Any]]:
        state = self._state()
        rows: list[dict[str, Any]] = []
        for child in sorted(self.root.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            manifest = self._installed_manifest(child)
            if not manifest:
                continue
            try:
                normalized = self._normalize_manifest(manifest, expected_id=child.name.lower())
            except Exception as exc:
                rows.append({"id": child.name, "name": child.name, "type": "unknown", "enabled": False, "invalid": True, "error": str(exc)})
                continue
            ext_state = state["extensions"].get(normalized["id"], {})
            normalized["enabled"] = bool(ext_state.get("enabled", True))
            normalized["installed"] = True
            normalized["path"] = str(child)
            rows.append(normalized)
        return rows

    def list_items(self) -> list[dict[str, Any]]:
        installed = {row["id"]: row for row in self.installed() if row.get("id")}
        result: list[dict[str, Any]] = []
        for row in self._catalog:
            data = dict(row)
            local = installed.get(data["id"])
            data["installed"] = bool(local)
            data["enabled"] = bool(local and local.get("enabled"))
            data["installed_version"] = str(local.get("version") or "") if local else ""
            data["update_available"] = bool(local and data.get("version") and data["version"] != data["installed_version"])
            result.append(data)
        catalog_ids = {row["id"] for row in self._catalog}
        for item_id, local in installed.items():
            if item_id not in catalog_ids:
                data = dict(local)
                data["source_name"] = "Локально"
                data["package_url"] = ""
                data["manifest_url"] = ""
                data["sha256"] = ""
                data["update_available"] = False
                result.append(data)
        return result

    def status(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "sources": self.sources(),
            "items": self.list_items(),
            "installed_count": len(self.installed()),
            "enabled_count": sum(1 for row in self.installed() if row.get("enabled")),
            "manifest_name": "leshy-plugin.json",
            "supports_skill_md": True,
        }

    def _catalog_entry(self, item_id: str) -> dict[str, Any]:
        value = str(item_id or "").strip().lower()
        for row in self._catalog:
            if row.get("id") == value:
                return dict(row)
        raise KeyError("Расширение не найдено в загруженном Marketplace")

    def _normalize_manifest(self, manifest: dict[str, Any], *, expected_id: str = "") -> dict[str, Any]:
        item_id = str(manifest.get("id") or expected_id).strip().lower()
        if not _ID_RE.fullmatch(item_id):
            raise ValueError("Манифест содержит некорректный id")
        if expected_id and item_id != expected_id:
            raise ValueError("id установленного пакета не совпадает с каталогом")
        kind = str(manifest.get("type") or "plugin").lower().strip()
        if kind not in {"plugin", "skill"}:
            raise ValueError("Тип должен быть plugin или skill")
        developer = manifest.get("developer") if isinstance(manifest.get("developer"), dict) else {}
        return {
            "id": item_id,
            "name": str(manifest.get("name") or item_id)[:160],
            "type": kind,
            "version": str(manifest.get("version") or "0")[:80],
            "description": str(manifest.get("description") or "")[:3000],
            "developer": {"name": str(developer.get("name") or manifest.get("author") or "Не указан")[:200], "url": str(developer.get("url") or "")[:1000]},
            "homepage": str(manifest.get("homepage") or "")[:1000],
            "license": str(manifest.get("license") or "")[:120],
            "permissions": [str(x)[:120] for x in (manifest.get("permissions") or []) if str(x).strip()][:50],
            "skill": manifest.get("skill") if isinstance(manifest.get("skill"), dict) else {},
            "plugin": manifest.get("plugin") if isinstance(manifest.get("plugin"), dict) else {},
            "schema_version": int(manifest.get("schema_version") or 1),
        }

    def install(self, item_id: str) -> dict[str, Any]:
        entry = self._catalog_entry(item_id)
        url = entry["package_url"]
        with tempfile.TemporaryDirectory(prefix="leshy-marketplace-") as temp_dir:
            temp = Path(temp_dir)
            archive = temp / "package.zip"
            self._download_package(url, archive, expected_sha256=str(entry.get("sha256") or ""))
            extracted = temp / "extracted"
            extracted.mkdir()
            self._safe_extract(archive, extracted)
            manifest_path = next(iter(extracted.rglob("leshy-plugin.json")), None)
            if manifest_path is None:
                raise ValueError("В пакете отсутствует leshy-plugin.json")
            manifest = self._read_json(manifest_path, None)
            if not isinstance(manifest, dict):
                raise ValueError("Некорректный leshy-plugin.json")
            normalized = self._normalize_manifest(manifest, expected_id=entry["id"])
            package_root = manifest_path.parent
            target = self.root / normalized["id"]
            staging = self.root / ("." + normalized["id"] + ".installing")
            backup = self.root / ("." + normalized["id"] + ".backup")
            shutil.rmtree(staging, ignore_errors=True)
            shutil.copytree(package_root, staging)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            if target.exists():
                target.replace(backup)
            try:
                staging.replace(target)
                shutil.rmtree(backup, ignore_errors=True)
            except Exception:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                if backup.exists():
                    backup.replace(target)
                raise
        state = self._state()
        state["extensions"][normalized["id"]] = {"enabled": True, "installed_at": datetime.now(timezone.utc).isoformat()}
        self._save_state(state)
        event("marketplace_extension_installed", id=normalized["id"], type=normalized["type"], version=normalized["version"])
        return {"ok": True, "item": next(row for row in self.installed() if row["id"] == normalized["id"])}

    @staticmethod
    def _download_package(url: str, target: Path, *, expected_sha256: str = "") -> None:
        req = request.Request(url, headers={"User-Agent": "Leshy-AI/Marketplace"})
        digest = sha256()
        total = 0
        with request.urlopen(req, timeout=60) as response, target.open("wb") as output:
            length = int(response.headers.get("Content-Length") or 0)
            if length and length > MAX_PACKAGE_BYTES:
                raise ValueError("Пакет расширения превышает 256 МБ")
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError("Пакет расширения превышает 256 МБ")
                digest.update(chunk)
                output.write(chunk)
        if expected_sha256 and digest.hexdigest().lower() != expected_sha256.lower():
            raise ValueError("SHA-256 пакета не совпадает с каталогом разработчика")

    @staticmethod
    def _safe_extract(archive: Path, target: Path) -> None:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                destination = (target / info.filename).resolve()
                if target.resolve() not in destination.parents and destination != target.resolve():
                    raise ValueError("Пакет содержит небезопасный путь")
            zf.extractall(target)

    def set_enabled(self, item_id: str, enabled: bool) -> dict[str, Any]:
        item_id = str(item_id or "").strip().lower()
        target = self.root / item_id
        if not target.is_dir() or not self._installed_manifest(target):
            raise FileNotFoundError("Расширение не установлено")
        state = self._state()
        current = state["extensions"].setdefault(item_id, {})
        current["enabled"] = bool(enabled)
        current["updated_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)
        event("marketplace_extension_toggled", id=item_id, enabled=bool(enabled))
        return {"ok": True, "id": item_id, "enabled": bool(enabled)}

    def request_remove(self, item_id: str) -> dict[str, Any]:
        item_id = str(item_id or "").strip().lower()
        target = self.root / item_id
        manifest = self._installed_manifest(target) if target.is_dir() else None
        if not manifest:
            raise FileNotFoundError("Расширение не установлено")
        normalized = self._normalize_manifest(manifest, expected_id=item_id)
        token = secrets.token_urlsafe(18)
        expires = datetime.now(timezone.utc) + timedelta(minutes=10)
        state = self._state()
        state["delete_approvals"][token] = {"id": item_id, "expires_at": expires.isoformat(), "used": False}
        self._save_state(state)
        return {
            "approval_required": True,
            "token": token,
            "id": item_id,
            "name": normalized["name"],
            "description": f"Удалить {normalized['type']} «{normalized['name']}» с компьютера и отключить его от Лешего?",
            "yes_label": "Да, удалить",
            "no_label": "Нет, отменить",
            "expires_at": expires.isoformat(),
        }

    def confirm_remove(self, token: str, allow: bool) -> dict[str, Any]:
        value = str(token or "").strip()
        state = self._state()
        approval = state["delete_approvals"].get(value)
        if not isinstance(approval, dict) or approval.get("used"):
            raise ValueError("Запрос удаления не найден или уже использован")
        expires = datetime.fromisoformat(str(approval.get("expires_at")))
        if datetime.now(timezone.utc) >= expires:
            approval["used"] = True
            approval["status"] = "expired"
            self._save_state(state)
            return {"ok": False, "status": "expired", "message": "Срок подтверждения истёк. Плагин/skill не удалён."}
        approval["used"] = True
        item_id = str(approval.get("id") or "")
        if not allow:
            approval["status"] = "cancelled"
            self._save_state(state)
            return {"ok": True, "status": "cancelled", "message": "Удаление отменено."}
        target = self.root / item_id
        if target.is_dir():
            shutil.rmtree(target)
        state["extensions"].pop(item_id, None)
        approval["status"] = "deleted"
        self._save_state(state)
        event("marketplace_extension_removed", id=item_id)
        return {"ok": True, "status": "deleted", "id": item_id, "message": "Плагин/skill удалён с компьютера и отключён от Лешего."}

    def skill_context(self, *, max_chars: int = 24000) -> str:
        chunks: list[str] = []
        used = 0
        for item in self.installed():
            if not item.get("enabled") or item.get("type") != "skill":
                continue
            path = self.root / str(item["id"])
            skill_cfg = item.get("skill") if isinstance(item.get("skill"), dict) else {}
            filename = str(skill_cfg.get("instructions_file") or "SKILL.md")
            candidate = (path / filename).resolve()
            if path.resolve() not in candidate.parents and candidate != path.resolve():
                continue
            if not candidate.is_file():
                continue
            text = candidate.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                continue
            prefix = f"\n\n### Skill: {item.get('name')} ({item.get('id')})\n"
            available = max_chars - used - len(prefix)
            if available <= 0:
                break
            block = prefix + text[:available]
            chunks.append(block)
            used += len(block)
        return "".join(chunks).strip()

    def plugin_context(self, *, max_chars: int = 12000) -> str:
        rows: list[str] = []
        used = 0
        for item in self.installed():
            if not item.get("enabled") or item.get("type") != "plugin":
                continue
            plugin_cfg = item.get("plugin") if isinstance(item.get("plugin"), dict) else {}
            tools = plugin_cfg.get("tools") if isinstance(plugin_cfg.get("tools"), list) else []
            line = f"- {item.get('name')} ({item.get('id')}): {item.get('description','')}"
            if tools:
                names = ", ".join(str(tool.get("name") or "") for tool in tools if isinstance(tool, dict) and tool.get("name"))
                if names:
                    line += f". Инструменты: {names}"
            if used + len(line) + 1 > max_chars:
                break
            rows.append(line)
            used += len(line) + 1
        return "\n".join(rows)


__all__ = ["MarketplaceManager", "MarketplaceItem"]
