from __future__ import annotations

"""Реальные измерения ресурсов узла P2P без внешних сервисов и LLM.

Данные берутся только с текущего устройства: OS, CPU, ОЗУ и загрузка.
Если источник недоступен, вместо выдуманных характеристик возвращается 0
(неизвестно); планировщик не должен считать такой ресурс гарантированным.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import platform
import socket


_GIB = 1024 ** 3


@dataclass(frozen=True)
class DeviceTelemetry:
    hostname: str
    os_name: str
    os_version: str
    cpu_name: str
    cpu_threads: int
    memory_total_gb: float
    memory_available_gb: float
    load: float


def _cpu_threads() -> int:
    try:
        affinity = os.sched_getaffinity(0)
        if affinity:
            return len(affinity)
    except (AttributeError, OSError):
        pass
    return max(1, int(os.cpu_count() or 1))


def _cpu_name() -> str:
    if platform.system().lower() == "linux":
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith("model name") and ":" in line:
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    return str(platform.processor() or os.getenv("PROCESSOR_IDENTIFIER") or "").strip()


def _linux_memory() -> tuple[float, float]:
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            parts = raw.strip().split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) * 1024
        total = values.get("MemTotal", 0)
        available = values.get("MemAvailable", 0)
        if total <= 0:
            return 0.0, 0.0
        return round(total / _GIB, 3), round(min(max(0, available), total) / _GIB, 3)
    except (OSError, ValueError):
        return 0.0, 0.0


def _memory_info() -> tuple[float, float]:
    try:
        import psutil

        value = psutil.virtual_memory()
        total, available = float(value.total), float(value.available)
        if total > 0:
            return round(total / _GIB, 3), round(min(max(0.0, available), total) / _GIB, 3)
    except (ImportError, OSError, ValueError):
        pass
    if platform.system().lower() == "linux":
        return _linux_memory()
    return 0.0, 0.0


def _load_ratio(cpu_threads: int) -> float:
    try:
        if platform.system().lower() != "windows":
            return max(0.0, float(os.getloadavg()[0]) / max(1, cpu_threads))
    except (AttributeError, OSError, ValueError):
        pass
    try:
        import psutil

        return max(0.0, float(psutil.cpu_percent(interval=0.05)) / 100.0)
    except (ImportError, OSError, ValueError):
        # Не выдавать неизвестную нагрузку за простой устройства.
        return 1.0


def measure_device() -> DeviceTelemetry:
    threads = _cpu_threads()
    total, available = _memory_info()
    return DeviceTelemetry(
        hostname=socket.gethostname(),
        os_name=platform.system() or "unknown",
        os_version=platform.release() or "",
        cpu_name=_cpu_name(),
        cpu_threads=threads,
        memory_total_gb=total,
        memory_available_gb=available,
        load=_load_ratio(threads),
    )


__all__ = ["DeviceTelemetry", "measure_device"]
