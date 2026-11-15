from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any


@dataclass(frozen=True)
class MemoryPolicy:
    allow_os_swap: bool = True
    auto_use_when_ram_low: bool = True
    low_ram_threshold_mb: int = 2048


class MemoryPolicyStore:
    """Постоянная политика использования RAM и swap/pagefile.

    Леший не создаёт и не меняет размер системного pagefile самостоятельно. Если
    ОС уже предоставляет виртуальную память, локальные runtime могут использовать
    её как резерв при нехватке физической RAM.
    """

    def __init__(self, brain_dir: Path | str):
        self.path = Path(brain_dir) / "settings" / "memory.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> MemoryPolicy:
        if not self.path.is_file():
            return MemoryPolicy()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return MemoryPolicy()
        if not isinstance(raw, dict):
            return MemoryPolicy()
        try:
            threshold = int(raw.get("low_ram_threshold_mb", 2048))
        except (TypeError, ValueError):
            threshold = 2048
        return MemoryPolicy(
            allow_os_swap=bool(raw.get("allow_os_swap", True)),
            auto_use_when_ram_low=bool(raw.get("auto_use_when_ram_low", True)),
            low_ram_threshold_mb=max(256, min(threshold, 32768)),
        )

    def save(self, policy: MemoryPolicy) -> MemoryPolicy:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(policy), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        return policy

    def ensure_default(self) -> MemoryPolicy:
        policy = self.load()
        if not self.path.is_file():
            self.save(policy)
        return policy


def _windows_memory() -> dict[str, int | bool | str]:
    command = (
        "$os=Get-CimInstance Win32_OperatingSystem; "
        "$pf=@(Get-CimInstance Win32_PageFileUsage -ErrorAction SilentlyContinue); "
        "$allocated=($pf | Measure-Object -Property AllocatedBaseSize -Sum).Sum; "
        "$used=($pf | Measure-Object -Property CurrentUsage -Sum).Sum; "
        "[pscustomobject]@{"
        "TotalVisibleKB=[int64]$os.TotalVisibleMemorySize;"
        "FreePhysicalKB=[int64]$os.FreePhysicalMemory;"
        "TotalVirtualKB=[int64]$os.TotalVirtualMemorySize;"
        "FreeVirtualKB=[int64]$os.FreeVirtualMemory;"
        "PagefileAllocatedMB=[int64]($allocated -as [int64]);"
        "PagefileUsedMB=[int64]($used -as [int64])"
        "} | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            creationflags=0x08000000,
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return {"available": False, "error": completed.stderr.strip()[:500]}
        raw = json.loads(completed.stdout)
        total_ram = int(raw.get("TotalVisibleKB") or 0) * 1024
        free_ram = int(raw.get("FreePhysicalKB") or 0) * 1024
        total_virtual = int(raw.get("TotalVirtualKB") or 0) * 1024
        free_virtual = int(raw.get("FreeVirtualKB") or 0) * 1024
        pagefile_total = int(raw.get("PagefileAllocatedMB") or 0) * 1024 * 1024
        pagefile_used = int(raw.get("PagefileUsedMB") or 0) * 1024 * 1024
        return {
            "available": True,
            "platform": "windows",
            "ram_total_bytes": total_ram,
            "ram_free_bytes": free_ram,
            "virtual_total_bytes": total_virtual,
            "virtual_free_bytes": free_virtual,
            "pagefile_total_bytes": pagefile_total,
            "pagefile_used_bytes": pagefile_used,
            "pagefile_free_bytes": max(0, pagefile_total - pagefile_used),
            "pagefile_enabled": pagefile_total > 0,
        }
    except Exception as exc:
        return {"available": False, "platform": "windows", "error": str(exc)[:500]}


def _linux_memory() -> dict[str, int | bool | str]:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return {"available": False, "platform": "posix"}
    try:
        values: dict[str, int] = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            number = value.strip().split()[0]
            values[key] = int(number) * 1024
        swap_total = values.get("SwapTotal", 0)
        swap_free = values.get("SwapFree", 0)
        return {
            "available": True,
            "platform": "linux",
            "ram_total_bytes": values.get("MemTotal", 0),
            "ram_free_bytes": values.get("MemAvailable", values.get("MemFree", 0)),
            "virtual_total_bytes": values.get("MemTotal", 0) + swap_total,
            "virtual_free_bytes": values.get("MemAvailable", values.get("MemFree", 0)) + swap_free,
            "pagefile_total_bytes": swap_total,
            "pagefile_used_bytes": max(0, swap_total - swap_free),
            "pagefile_free_bytes": swap_free,
            "pagefile_enabled": swap_total > 0,
        }
    except Exception as exc:
        return {"available": False, "platform": "linux", "error": str(exc)[:500]}


def system_memory_status() -> dict[str, Any]:
    if os.name == "nt":
        return _windows_memory()
    return _linux_memory()


def memory_status(brain_dir: Path | str) -> dict[str, Any]:
    store = MemoryPolicyStore(brain_dir)
    policy = store.ensure_default()
    system = system_memory_status()
    free_ram = int(system.get("ram_free_bytes") or 0)
    threshold = int(policy.low_ram_threshold_mb) * 1024 * 1024
    pagefile_enabled = bool(system.get("pagefile_enabled"))
    swap_available = bool(policy.allow_os_swap and pagefile_enabled)
    return {
        "policy": asdict(policy),
        "system": system,
        "swap_available": swap_available,
        "ram_low": bool(free_ram and free_ram < threshold),
        "effective_behavior": (
            "use_os_swap_when_needed"
            if swap_available and policy.auto_use_when_ram_low
            else ("swap_allowed" if swap_available else "physical_memory_only")
        ),
        "note": (
            "Swap/pagefile разрешён как аварийный резерв; ОС управляет фактическим paging автоматически."
            if policy.allow_os_swap
            else "Использование swap/pagefile запрещено политикой Лешего."
        ),
    }


__all__ = ["MemoryPolicy", "MemoryPolicyStore", "system_memory_status", "memory_status"]
