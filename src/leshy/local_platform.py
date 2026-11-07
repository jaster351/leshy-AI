from __future__ import annotations

"""Определение локального LLM-runtime по реальной ОС.

Никаких заглушек и облачных API: на Windows прежний менеджер официальных
llama.cpp-пакетов; на Linux — установленный локально llama-server или
llama-cpp-python. Настройки не влияют на маршрутизацию P2P-кластера.
"""

import os
from pathlib import Path
import platform
import shutil
from typing import Mapping


def runtime_platform(system: str | None = None) -> str:
    name = str(system if system is not None else platform.system()).strip().lower()
    if name.startswith("win"):
        return "windows"
    if name == "linux":
        return "linux"
    return "other"


def installed_llama_server(
    *,
    system: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path | None:
    """Вернуть только существующий исполняемый файл локального llama-server.

    Не пытаемся скачивать Windows ZIP на Linux. Явный путь может находиться
    вне PATH (например, в /opt/llama.cpp/build/bin).
    """
    if runtime_platform(system) == "windows":
        return None
    variables = os.environ if environ is None else environ
    explicit = str(variables.get("LESHY_LLAMA_SERVER") or "").strip()
    candidates = [explicit] if explicit else []
    found = shutil.which("llama-server")
    if found:
        candidates.append(found)
    candidates.extend(("/usr/local/bin/llama-server", "/usr/bin/llama-server"))
    for value in candidates:
        if not value:
            continue
        binary = Path(value).expanduser()
        try:
            if binary.is_file() and os.access(binary, os.X_OK):
                return binary.resolve()
        except OSError:
            continue
    return None


def local_backend_order(*, system: str | None = None, has_native_gpu: bool = False,
                        has_server: bool = False) -> tuple[str, ...]:
    """Приоритеты без изменения Windows-бэкендов.

    Windows продолжает использовать оригинальный GPU/NPU/llama-server;
    на Linux не объявляем Windows-only runtime.
    """
    if runtime_platform(system) == "windows":
        return ("cpu-llama-server", "cpu-native")
    result: list[str] = []
    if has_native_gpu:
        result.append("gpu-native")
    result.append("cpu-native")
    if has_server:
        result.append("cpu-llama-server")
    return tuple(result)


__all__ = ["runtime_platform", "installed_llama_server", "local_backend_order"]
