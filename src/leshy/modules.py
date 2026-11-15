from __future__ import annotations

from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    """Описание подключаемого модуля Лешего."""

    name: str
    version: str = "0.1.0"
    description: str = ""
    priority: int = 100


@dataclass(slots=True)
class ModuleRequest:
    """Нормализованный запрос, который можно передать любому модулю."""

    text: str
    user_id: str = "local"
    channel: str = "local"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModuleResponse:
    """Ответ модуля. handled=False означает передачу запроса дальше."""

    handled: bool
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class LeshyModule(Protocol):
    """Минимальный контракт любого функционального модуля."""

    manifest: ModuleManifest

    def can_handle(self, request: ModuleRequest) -> bool: ...

    def handle(self, request: ModuleRequest) -> ModuleResponse: ...


@dataclass(frozen=True, slots=True)
class ModuleState:
    name: str
    version: str
    description: str
    priority: int
    enabled: bool
    last_error: str = ""


class ModuleLoadError(RuntimeError):
    pass


class ModuleManager:
    """Реестр модулей с изоляцией ошибок и быстрым включением/выключением."""

    def __init__(self) -> None:
        self._modules: dict[str, LeshyModule] = {}
        self._enabled: dict[str, bool] = {}
        self._errors: dict[str, str] = {}

    def register(self, module: LeshyModule, *, enabled: bool = True) -> None:
        manifest = getattr(module, "manifest", None)
        if manifest is None or not isinstance(manifest, ModuleManifest):
            raise ModuleLoadError("Модуль не содержит корректный ModuleManifest")
        if not manifest.name.strip():
            raise ModuleLoadError("Имя модуля не может быть пустым")
        if manifest.name in self._modules:
            raise ModuleLoadError(f"Модуль уже зарегистрирован: {manifest.name}")
        if not callable(getattr(module, "can_handle", None)) or not callable(getattr(module, "handle", None)):
            raise ModuleLoadError(f"Модуль {manifest.name} не реализует обязательный контракт")
        self._modules[manifest.name] = module
        self._enabled[manifest.name] = bool(enabled)
        self._errors.pop(manifest.name, None)

    def load(self, spec: str, *, enabled: bool = True) -> LeshyModule:
        """Загрузить модуль по строке `пакет.модуль:объект_или_фабрика`."""

        module_path, separator, attribute_name = spec.partition(":")
        if not separator or not module_path or not attribute_name:
            raise ModuleLoadError("Формат модуля: пакет.модуль:объект_или_фабрика")
        try:
            python_module = import_module(module_path)
            target = getattr(python_module, attribute_name)
            module = target() if callable(target) else target
        except Exception as exc:  # noqa: BLE001 — граница изоляции стороннего модуля.
            raise ModuleLoadError(f"Не удалось загрузить модуль {spec}: {exc}") from exc
        self.register(module, enabled=enabled)
        return module

    def unregister(self, name: str) -> None:
        self._modules.pop(name, None)
        self._enabled.pop(name, None)
        self._errors.pop(name, None)

    def set_enabled(self, name: str, enabled: bool) -> None:
        if name not in self._modules:
            raise KeyError(f"Неизвестный модуль: {name}")
        self._enabled[name] = bool(enabled)

    def states(self) -> list[ModuleState]:
        states: list[ModuleState] = []
        for name, module in self._modules.items():
            manifest = module.manifest
            states.append(
                ModuleState(
                    name=name,
                    version=manifest.version,
                    description=manifest.description,
                    priority=manifest.priority,
                    enabled=self._enabled.get(name, False),
                    last_error=self._errors.get(name, ""),
                )
            )
        return sorted(states, key=lambda item: (-item.priority, item.name))

    def dispatch(self, request: ModuleRequest) -> ModuleResponse:
        """Передать запрос модулям по приоритету; сбой одного модуля не ломает ядро."""

        ordered = sorted(
            self._modules.values(),
            key=lambda module: (-module.manifest.priority, module.manifest.name),
        )
        for module in ordered:
            name = module.manifest.name
            if not self._enabled.get(name, False):
                continue
            try:
                if not module.can_handle(request):
                    continue
                response = module.handle(request)
            except Exception as exc:  # noqa: BLE001 — модуль обязан быть изолирован от ядра.
                self._errors[name] = str(exc)
                continue
            self._errors.pop(name, None)
            if response.handled:
                return response
        return ModuleResponse(handled=False)
