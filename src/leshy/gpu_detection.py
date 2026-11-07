"""Универсальное обнаружение физических GPU без каталогов конкретных моделей.

Отображаемое устройство и пригодность backend для inference — разные понятия.
Наличие GPU/драйвера само по себе не доказывает исправность Vulkan/CUDA/Metal.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import platform
from pathlib import Path
import re
import subprocess
from typing import Any, Callable


# PCI Vendor IDs стандартизованы; это НЕ список моделей или поколений GPU.
PCI_VENDORS = {
    "10de": "NVIDIA",
    "1002": "AMD",
    "1022": "AMD",
    "8086": "Intel",
    "106b": "Apple",
}

_PCI_VENDOR = re.compile(r"(?:^|[\\&])VEN_([0-9a-f]{4})", re.I)
_PCI_DEVICE = re.compile(r"(?:^|[\\&])DEV_([0-9a-f]{4})", re.I)
_LSPCI = re.compile(
    r"^(\S+)\s+(?:VGA compatible controller|3D controller|Display controller)"
    r"\s+\[[0-9a-fA-F]{4}\]:\s+(.+?)\s+\[([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\]",
    re.I,
)


@dataclass(frozen=True)
class GPUDevice:
    name: str
    vendor: str
    vendor_id: str | None = None
    device_id: str | None = None
    vram_bytes: int | None = None
    driver_version: str | None = None
    bus_id: str | None = None


def gpu_vendor(vendor_id: str | None, name: str = "") -> str:
    """PCI Vendor ID имеет приоритет перед ненадёжным именем из драйвера."""
    key = str(vendor_id or "").strip().lower().removeprefix("0x")
    if key in PCI_VENDORS:
        return PCI_VENDORS[key]
    label = str(name).casefold()
    if "nvidia" in label or "geforce" in label:
        return "NVIDIA"
    if "radeon" in label or "advanced micro devices" in label or " amd" in f" {label}" or "ati " in label:
        return "AMD"
    if "intel" in label:
        return "Intel"
    if "apple" in label:
        return "Apple"
    return "Unknown"


def _vram_bytes(value: Any, *, windows_wmi: bool = False) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number <= 0:
        return None
    # Win32_VideoController.AdapterRAM — uint32; на GPU с >4 ГБ поле переполняется.
    if windows_wmi and number >= 0xFFF00000:
        return None
    return number


def windows_gpu_devices(powershell_json: Callable[[str], Any]) -> tuple[GPUDevice, ...]:
    data = powershell_json(
        "$g=Get-CimInstance Win32_VideoController | "
        "Select-Object Name,PNPDeviceID,AdapterRAM,DriverVersion; "
        "@($g) | ConvertTo-Json -Compress"
    )
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return ()
    result = []
    seen = set()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("Name") or "").strip()
        if not name or any(marker in name.casefold() for marker in (
            "microsoft basic display", "microsoft basic render", "remote display adapter"
        )):
            continue
        pnp = str(entry.get("PNPDeviceID") or "")
        ven = _PCI_VENDOR.search(pnp)
        dev = _PCI_DEVICE.search(pnp)
        vendor_id = ven.group(1).lower() if ven else None
        device_id = dev.group(1).lower() if dev else None
        key = pnp or name
        if key in seen:
            continue
        seen.add(key)
        result.append(GPUDevice(
            name=name,
            vendor=gpu_vendor(vendor_id, name),
            vendor_id=vendor_id,
            device_id=device_id,
            vram_bytes=_vram_bytes(entry.get("AdapterRAM"), windows_wmi=True),
            driver_version=str(entry.get("DriverVersion") or "").strip() or None,
            bus_id=pnp or None,
        ))
    return tuple(result)


def _run_output(args: list[str], *, timeout: float = 4.0) -> str:
    try:
        completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, check=False)
        return completed.stdout if completed.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _lspci_records(output: str) -> dict[str, tuple[str, str, str]]:
    records: dict[str, tuple[str, str, str]] = {}
    for line in output.splitlines():
        match = _LSPCI.match(line.strip())
        if match:
            bus, name, vendor_id, device_id = match.groups()
            records[bus.lower()] = (name.strip(), vendor_id.lower(), device_id.lower())
    return records


def _sysfs_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def linux_gpu_devices(sysfs_root: Path | None = None, *, lspci_output: str | None = None) -> tuple[GPUDevice, ...]:
    root = Path(sysfs_root) if sysfs_root is not None else Path("/sys/class/drm")
    records = _lspci_records(_run_output(["lspci", "-D", "-nn"], timeout=3.0)
                             if lspci_output is None else lspci_output)
    devices: list[GPUDevice] = []
    seen: set[str] = set()
    try:
        cards = list(root.iterdir())
    except OSError:
        cards = []
    for card in cards:
        if not re.fullmatch(r"(?:card|renderD)\d+", card.name):
            continue
        device_path = card / "device"
        ven = _sysfs_read(device_path / "vendor").lower().removeprefix("0x") or None
        dev = _sysfs_read(device_path / "device").lower().removeprefix("0x") or None
        if not ven:
            continue
        try:
            bus = device_path.resolve().name.lower()
        except OSError:
            bus = card.name
        # В тестовых/виртуальных sysfs путь может быть обычной папкой
        # device, а не ссылкой на PCI BDF. Тогда используем имя card.
        if not re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]", bus):
            bus = card.name
        if bus in seen:
            continue
        seen.add(bus)
        pci = records.get(bus)
        name = pci[0] if pci else f"GPU PCI {ven}:{dev or 'unknown'} ({bus})"
        vram = _sysfs_read(device_path / "mem_info_vram_total")
        devices.append(GPUDevice(
            name=name,
            vendor=gpu_vendor(ven, name),
            vendor_id=ven,
            device_id=dev,
            vram_bytes=_vram_bytes(vram),
            bus_id=bus,
        ))
    # Без /sys/class/drm используем доступный системный PCI inventory.
    for bus, (name, ven, dev) in records.items():
        if bus not in seen:
            devices.append(GPUDevice(name=name, vendor=gpu_vendor(ven, name),
                                     vendor_id=ven, device_id=dev, bus_id=bus))
    return tuple(devices)


def macos_gpu_devices(*, system_profiler_json: str | None = None) -> tuple[GPUDevice, ...]:
    raw = (_run_output(["system_profiler", "SPDisplaysDataType", "-json"], timeout=8.0)
           if system_profiler_json is None else system_profiler_json)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return ()
    entries = data.get("SPDisplaysDataType", []) if isinstance(data, dict) else []
    result = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("sppci_model") or entry.get("_name") or "").strip()
        if not name:
            continue
        vendor_label = str(entry.get("spdisplays_vendor") or "")
        vendor_id = re.search(r"0x([0-9a-f]{4})", vendor_label, flags=re.I)
        vid = vendor_id.group(1).lower() if vendor_id else None
        result.append(GPUDevice(
            name=name,
            vendor=gpu_vendor(vid, f"{vendor_label} {name}"),
            vendor_id=vid,
        ))
    return tuple(result)


def detect_gpu_devices(*, system: str | None = None,
                       powershell_json: Callable[[str], Any] | None = None,
                       sysfs_root: Path | None = None,
                       lspci_output: str | None = None,
                       system_profiler_json: str | None = None) -> tuple[GPUDevice, ...]:
    """Обнаружить доступные устройства; драйвер/backend проверяются отдельно."""
    current = (system or platform.system()).lower()
    if current == "windows":
        return windows_gpu_devices(powershell_json) if powershell_json is not None else ()
    if current == "linux":
        return linux_gpu_devices(sysfs_root, lspci_output=lspci_output)
    if current == "darwin":
        return macos_gpu_devices(system_profiler_json=system_profiler_json)
    return ()
