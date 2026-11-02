from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
from typing import Any

from .diagnostics import event, problem
from .tools_gateway import McpToolsGateway, ToolManifest, ToolPermission, ToolRegistry


@dataclass(frozen=True, slots=True)
class MarketplaceEntry:
    extension_id: str
    title: str
    package_spec: str
    module: str
    module_args: tuple[str, ...] = ()
    permissions: tuple[ToolPermission, ...] = (ToolPermission.READ,)
    repository: str = ""
    license: str = ""
    description: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MarketplaceEntry":
        return cls(
            extension_id=str(raw.get("extension_id") or "").strip(),
            title=str(raw.get("title") or raw.get("extension_id") or "").strip(),
            package_spec=str(raw.get("package_spec") or "").strip(),
            module=str(raw.get("module") or "").strip(),
            module_args=tuple(str(x) for x in (raw.get("module_args") or [])),
            permissions=tuple(ToolPermission(str(x)) for x in (raw.get("permissions") or ["read"])),
            repository=str(raw.get("repository") or "").strip(),
            license=str(raw.get("license") or "").strip(),
            description=str(raw.get("description") or "").strip(),
        )

    def summary(self) -> dict[str, Any]:
        return {
            "extension_id": self.extension_id,
            "title": self.title,
            "package_spec": self.package_spec,
            "module": self.module,
            "permissions": [x.value for x in self.permissions],
            "repository": self.repository,
            "license": self.license,
            "description": self.description,
        }


class ExtensionApprovalRequired(PermissionError):
    def __init__(self, entry: MarketplaceEntry):
        self.entry = entry
        super().__init__(f"Установка расширения «{entry.title}» требует явного подтверждения")


class ExtensionMarketplace:
    """Безопасный локальный менеджер устанавливаемых MCP/Skills.

    Python-расширения ставятся в отдельные venv. Никакой shell-строки из manifest
    не выполняется: запускается только `python -m <module>` из изолированного venv.
    После установки MCP server обязан успешно объявить хотя бы один tool, иначе
    новая версия откатывается, а предыдущая рабочая версия восстанавливается.
    """

    def __init__(self, registry: ToolRegistry | None = None, *, root: str | Path | None = None):
        self.registry = registry or ToolRegistry()
        if root is None:
            brain = Path(os.getenv("LESHY_BRAIN_DIR", "LeshyBrain")).expanduser()
            root = brain / "extensions"
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate(entry: MarketplaceEntry) -> None:
        if not entry.extension_id or not entry.extension_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("Некорректный extension_id")
        if not entry.package_spec or "==" not in entry.package_spec:
            raise ValueError("Marketplace требует закреплённую версию package_spec через ==")
        if not entry.module or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_." for ch in entry.module):
            raise ValueError("Некорректный Python module")
        if not entry.license:
            raise ValueError("Для расширения должна быть указана лицензия")
        if not entry.repository.startswith("https://"):
            raise ValueError("Для расширения требуется HTTPS repository URL")

    @staticmethod
    def load_catalog(path: str | Path) -> list[MarketplaceEntry]:
        source = Path(path).expanduser()
        raw = json.loads(source.read_text(encoding="utf-8"))
        items = raw.get("items") if isinstance(raw, dict) else raw
        return [MarketplaceEntry.from_dict(dict(item)) for item in (items or [])]

    def _extension_dir(self, extension_id: str) -> Path:
        return self.root / extension_id

    @staticmethod
    def _venv_python(directory: Path) -> Path:
        if os.name == "nt":
            return directory / "venv" / "Scripts" / "python.exe"
        return directory / "venv" / "bin" / "python"

    def inspect(self, entry: MarketplaceEntry) -> dict[str, Any]:
        self._validate(entry)
        data = entry.summary()
        data["requires_approval"] = True
        data["isolated"] = True
        data["installed"] = self._extension_dir(entry.extension_id).is_dir()
        return data

    def install(self, entry: MarketplaceEntry, *, approved: bool = False, timeout_seconds: int = 300) -> dict[str, Any]:
        self._validate(entry)
        if not approved:
            event("extension_install_approval_required", extension_id=entry.extension_id)
            raise ExtensionApprovalRequired(entry)

        directory = self._extension_dir(entry.extension_id)
        staging = self.root / (entry.extension_id + ".installing")
        backup = self.root / (entry.extension_id + ".backup")
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)

        previous_manifest: ToolManifest | None = None
        try:
            previous_manifest = self.registry.get(entry.extension_id)
        except KeyError:
            pass

        staging.mkdir(parents=True)
        promoted = False
        registry_replaced = False
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", str(staging / "venv")],
                check=True,
                timeout=max(30, int(timeout_seconds)),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            python = self._venv_python(staging)
            install = subprocess.run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--disable-pip-version-check",
                    "--no-input",
                    entry.package_spec,
                ],
                check=False,
                timeout=max(30, int(timeout_seconds)),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if install.returncode != 0:
                tail = "\n".join((install.stdout or "").splitlines()[-30:])
                raise RuntimeError("pip install завершился ошибкой:\n" + tail)

            # Двухфазная замена: старая рабочая версия не удаляется, а атомарно
            # уходит в backup до завершения health-check новой версии.
            if directory.exists():
                directory.replace(backup)
            staging.replace(directory)
            promoted = True

            python = self._venv_python(directory)
            manifest = ToolManifest(
                tool_id=entry.extension_id,
                title=entry.title,
                transport="stdio",
                command=str(python),
                args=("-m", entry.module, *entry.module_args),
                permissions=entry.permissions,
                source=entry.repository,
                enabled=True,
            )
            self.registry.register(manifest)
            registry_replaced = True

            gateway = McpToolsGateway(self.registry)
            tools = gateway.list_tools(entry.extension_id)
            if not tools:
                raise RuntimeError("Установленный MCP server не объявил ни одного tool")

            receipt = {
                **entry.summary(),
                "installed": True,
                "tool_count": len(tools),
                "tools": [item.get("name") for item in tools],
            }
            (directory / "leshy-install.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            shutil.rmtree(backup, ignore_errors=True)
            event("extension_installed", extension_id=entry.extension_id, tool_count=len(tools))
            return receipt
        except Exception as exc:
            problem("extension_install_failed", str(exc), extension_id=entry.extension_id)
            shutil.rmtree(staging, ignore_errors=True)

            # Если новая версия уже была выдвинута на рабочий путь, удаляем её.
            # Затем возвращаем предыдущий каталог и предыдущий ToolManifest.
            if promoted and directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            if backup.exists():
                backup.replace(directory)
                event("extension_install_rolled_back", extension_id=entry.extension_id)

            if registry_replaced:
                self.registry.remove(entry.extension_id)
            if previous_manifest is not None:
                self.registry.register(previous_manifest)
            raise

    def uninstall(self, extension_id: str, *, approved: bool = False) -> None:
        if not approved:
            raise PermissionError("Удаление расширения требует подтверждения пользователя")
        normalized = str(extension_id).strip()
        self.registry.remove(normalized)
        shutil.rmtree(self._extension_dir(normalized), ignore_errors=True)
        event("extension_uninstalled", extension_id=normalized)

    def healthcheck(self, extension_id: str) -> dict[str, Any]:
        gateway = McpToolsGateway(self.registry)
        try:
            tools = gateway.list_tools(extension_id)
            return {"ok": True, "tool_count": len(tools), "tools": [x.get("name") for x in tools]}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


__all__ = [
    "ExtensionApprovalRequired",
    "ExtensionMarketplace",
    "MarketplaceEntry",
]
