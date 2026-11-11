from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from pathlib import Path
import os
import threading
from typing import Any, Awaitable
from urllib.parse import urlparse

from .diagnostics import event, problem
from .action_safety_guard import check_external_action


class ToolPermission(str, Enum):
    READ = "read"
    NETWORK = "network"
    FILESYSTEM = "filesystem"
    MUTATE = "mutate"
    DESKTOP = "desktop"
    SECRETS = "secrets"
    FINANCIAL = "financial"


_DANGEROUS_PERMISSIONS = {
    ToolPermission.MUTATE,
    ToolPermission.DESKTOP,
    ToolPermission.SECRETS,
    ToolPermission.FINANCIAL,
}


@dataclass(frozen=True, slots=True)
class ToolManifest:
    tool_id: str
    title: str
    transport: str
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    permissions: tuple[ToolPermission, ...] = (ToolPermission.READ,)
    source: str = "local"
    enabled: bool = True
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ToolManifest":
        permissions = tuple(ToolPermission(str(x)) for x in raw.get("permissions") or ["read"])
        return cls(
            tool_id=str(raw.get("tool_id") or "").strip(),
            title=str(raw.get("title") or raw.get("tool_id") or "").strip(),
            transport=str(raw.get("transport") or "stdio").strip().lower(),
            command=str(raw.get("command") or "").strip(),
            args=tuple(str(x) for x in (raw.get("args") or [])),
            url=str(raw.get("url") or "").strip(),
            permissions=permissions,
            source=str(raw.get("source") or "local").strip(),
            enabled=bool(raw.get("enabled", True)),
            env={str(k): str(v) for k, v in dict(raw.get("env") or {}).items()},
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["args"] = list(self.args)
        data["permissions"] = [item.value for item in self.permissions]
        return data


class ToolApprovalRequired(PermissionError):
    def __init__(self, manifest: ToolManifest, tool_name: str):
        self.manifest = manifest
        self.tool_name = tool_name
        super().__init__(f"Требуется подтверждение для инструмента {manifest.title}: {tool_name}")


class ToolRegistry:
    """Локальный реестр MCP/Skills с атомарным JSON-хранилищем."""

    def __init__(self, path: str | Path | None = None):
        if path is None:
            root = Path(os.getenv("LESHY_BRAIN_DIR", "LeshyBrain")).expanduser()
            path = root / "tools" / "registry.json"
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self) -> dict[str, ToolManifest]:
        if not self.path.is_file():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            items = raw.get("items") if isinstance(raw, dict) else []
            result: dict[str, ToolManifest] = {}
            for item in items or []:
                manifest = ToolManifest.from_dict(dict(item))
                if manifest.tool_id:
                    result[manifest.tool_id] = manifest
            return result
        except Exception as exc:
            problem("tools_registry_read_failed", str(exc), path=str(self.path))
            return {}

    def _write(self, items: dict[str, ToolManifest]) -> None:
        payload = {"version": 1, "items": [items[key].to_dict() for key in sorted(items)]}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except Exception:
            pass
        temporary.replace(self.path)

    def list(self) -> list[ToolManifest]:
        return list(self._read().values())

    def get(self, tool_id: str) -> ToolManifest:
        item = self._read().get(str(tool_id).strip())
        if item is None:
            raise KeyError(f"Инструмент не установлен: {tool_id}")
        return item

    def register(self, manifest: ToolManifest) -> ToolManifest:
        if not manifest.tool_id:
            raise ValueError("tool_id обязателен")
        if manifest.transport not in {"stdio", "http"}:
            raise ValueError("Поддерживаются только stdio и http MCP transport")
        if manifest.transport == "stdio" and not manifest.command:
            raise ValueError("stdio MCP требует command")
        if manifest.transport == "http":
            parsed = urlparse(manifest.url)
            local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            if parsed.scheme != "https" and not local_http:
                raise ValueError("Удалённый MCP URL должен использовать HTTPS")
        items = self._read()
        items[manifest.tool_id] = manifest
        self._write(items)
        event("tools_registry_registered", tool_id=manifest.tool_id, permissions=[x.value for x in manifest.permissions])
        return manifest

    def set_enabled(self, tool_id: str, enabled: bool) -> ToolManifest:
        items = self._read()
        current = items.get(str(tool_id).strip())
        if current is None:
            raise KeyError(f"Инструмент не установлен: {tool_id}")
        updated = ToolManifest(
            tool_id=current.tool_id,
            title=current.title,
            transport=current.transport,
            command=current.command,
            args=current.args,
            url=current.url,
            permissions=current.permissions,
            source=current.source,
            enabled=bool(enabled),
            env=current.env,
        )
        items[updated.tool_id] = updated
        self._write(items)
        event("tools_registry_toggled", tool_id=updated.tool_id, enabled=updated.enabled)
        return updated

    def remove(self, tool_id: str) -> None:
        items = self._read()
        if str(tool_id).strip() in items:
            del items[str(tool_id).strip()]
            self._write(items)
            event("tools_registry_removed", tool_id=str(tool_id).strip())


class McpToolsGateway:
    """MCP v2 client с единым approval gate поверх установленных инструментов."""

    def __init__(self, registry: ToolRegistry | None = None):
        self.registry = registry or ToolRegistry()

    @staticmethod
    def _run_sync(awaitable: Awaitable[Any]) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(awaitable)
        result: dict[str, Any] = {}
        error: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                result["value"] = asyncio.run(awaitable)
            except BaseException as exc:  # pragma: no cover
                error["value"] = exc

        thread = threading.Thread(target=worker, name="leshy-mcp", daemon=True)
        thread.start()
        thread.join()
        if error:
            raise error["value"]
        return result.get("value")

    @staticmethod
    def _target(manifest: ToolManifest):
        from mcp import StdioServerParameters

        if manifest.transport == "http":
            return manifest.url
        return StdioServerParameters(
            command=manifest.command,
            args=list(manifest.args),
            env=dict(manifest.env) if manifest.env else None,
        )

    @staticmethod
    def _needs_approval(manifest: ToolManifest) -> bool:
        return any(item in _DANGEROUS_PERMISSIONS for item in manifest.permissions)

    async def _list_tools(self, manifest: ToolManifest) -> list[dict[str, Any]]:
        from mcp import Client

        async with Client(self._target(manifest)) as client:
            page = await client.list_tools()
            return [
                {
                    "name": tool.name,
                    "title": getattr(tool, "title", None),
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in page.tools
            ]

    async def _call(self, manifest: ToolManifest, tool_name: str, arguments: dict[str, Any]):
        from mcp import Client

        async with Client(self._target(manifest)) as client:
            listed = await client.list_tools()
            names = {item.name for item in listed.tools}
            if tool_name not in names:
                raise KeyError(f"MCP server не объявил tool {tool_name}")
            result = await client.call_tool(tool_name, arguments)
            content: list[dict[str, Any]] = []
            for item in getattr(result, "content", []) or []:
                if hasattr(item, "model_dump"):
                    content.append(item.model_dump(mode="json"))
                else:
                    content.append({"type": type(item).__name__, "value": str(item)})
            return {
                "content": content,
                "structured_content": getattr(result, "structured_content", None),
                "is_error": bool(getattr(result, "is_error", False)),
            }

    def list_tools(self, tool_id: str) -> list[dict[str, Any]]:
        manifest = self.registry.get(tool_id)
        if not manifest.enabled:
            raise RuntimeError("Инструмент выключен")
        result = self._run_sync(self._list_tools(manifest))
        event("mcp_tools_listed", tool_id=manifest.tool_id, count=len(result))
        return result

    def call(self, tool_id: str, tool_name: str, arguments: dict[str, Any] | None = None, *, approved: bool = False) -> dict[str, Any]:
        manifest = self.registry.get(tool_id)
        if not manifest.enabled:
            raise RuntimeError("Инструмент выключен")
        try:
            decision = check_external_action(str(tool_name), arguments)
        except PermissionError:
            event("mcp_tool_blocked", tool_id=manifest.tool_id, tool_name=str(tool_name))
            raise
        if (self._needs_approval(manifest) or decision.requires_human_approval) and not approved:
            event("mcp_tool_approval_required", tool_id=manifest.tool_id, tool_name=tool_name)
            raise ToolApprovalRequired(manifest, tool_name)
        try:
            result = self._run_sync(self._call(manifest, str(tool_name), dict(arguments or {})))
            event("mcp_tool_called", tool_id=manifest.tool_id, tool_name=tool_name, is_error=result.get("is_error"))
            return result
        except Exception as exc:
            problem("mcp_tool_call_failed", str(exc), tool_id=manifest.tool_id, tool_name=tool_name)
            raise


__all__ = [
    "McpToolsGateway",
    "ToolApprovalRequired",
    "ToolManifest",
    "ToolPermission",
    "ToolRegistry",
]
