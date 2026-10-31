from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Any

VALID_MODES = {
    "auto",
    "cpu",
    "gpu",
    "npu",
    "cpu_gpu",
    "cpu_npu",
    "gpu_npu",
    "cpu_gpu_npu",
}

MODE_LABELS = {
    "auto": "Автоматически",
    "cpu": "Только CPU",
    "gpu": "Только GPU",
    "npu": "Только NPU",
    "cpu_gpu": "CPU + GPU",
    "cpu_npu": "CPU + NPU",
    "gpu_npu": "GPU + NPU",
    "cpu_gpu_npu": "CPU + GPU + NPU",
}


@dataclass(frozen=True)
class HardwareConfig:
    mode: str = "auto"
    gpu_layers_mixed: int = 24
    prefer_npu_for_background: bool = True


@dataclass(frozen=True)
class HardwareCapabilities:
    cpu: bool
    cpu_name: str
    gpu: bool
    gpu_names: tuple[str, ...]
    gpu_offload_runtime: bool
    npu: bool
    npu_names: tuple[str, ...]
    openvino_npu: bool
    notes: tuple[str, ...]


@dataclass(frozen=True)
class ExecutionPlan:
    requested_mode: str
    effective_mode: str
    chat_device: str
    background_devices: tuple[str, ...]
    n_gpu_layers: int
    chat_backend: str
    embedding_backend: str
    npu_backend: str | None
    warnings: tuple[str, ...]


class HardwareConfigStore:
    def __init__(self, brain_dir: Path):
        self.path = Path(brain_dir) / "settings" / "hardware.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> HardwareConfig:
        if not self.path.exists():
            return HardwareConfig()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return HardwareConfig()
        mode = str(raw.get("mode", "auto")).strip().lower()
        if mode not in VALID_MODES:
            mode = "auto"
        try:
            layers = int(raw.get("gpu_layers_mixed", 24))
        except (TypeError, ValueError):
            layers = 24
        return HardwareConfig(
            mode=mode,
            gpu_layers_mixed=max(1, min(999, layers)),
            prefer_npu_for_background=bool(raw.get("prefer_npu_for_background", True)),
        )

    def save(self, config: HardwareConfig) -> HardwareConfig:
        if config.mode not in VALID_MODES:
            raise ValueError(f"Неизвестный режим вычислений: {config.mode}")
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)
        return config


def _powershell_json(command: str) -> Any:
    if os.name != "nt":
        return None
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
            return None
        return json.loads(completed.stdout)
    except Exception:
        return None


def _windows_gpus() -> tuple[str, ...]:
    data = _powershell_json(
        "@(Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name) | ConvertTo-Json -Compress"
    )
    if isinstance(data, str):
        values = (data,)
    elif isinstance(data, list):
        values = tuple(str(x) for x in data if str(x).strip())
    else:
        values = ()
    return tuple(v for v in values if "Microsoft Basic Display" not in v)


def _windows_npus() -> tuple[str, ...]:
    data = _powershell_json(
        "$x=Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | "
        "Where-Object { $_.FriendlyName -match '(NPU|Neural Processing|AI Boost|Ryzen AI|Neural Processor|Neural Accelerator)' } | "
        "Select-Object -ExpandProperty FriendlyName; @($x) | ConvertTo-Json -Compress"
    )
    if isinstance(data, str):
        return (data,)
    if isinstance(data, list):
        return tuple(str(x) for x in data if str(x).strip())
    return ()


def _llama_gpu_offload_supported() -> bool:
    try:
        import llama_cpp
        func = getattr(llama_cpp, "llama_supports_gpu_offload", None)
        return bool(func()) if callable(func) else False
    except Exception:
        return False


def _openvino_devices() -> tuple[str, ...]:
    try:
        from openvino import Core  # type: ignore
        return tuple(str(x) for x in Core().available_devices)
    except Exception:
        return ()


def detect_hardware() -> HardwareCapabilities:
    cpu_name = platform.processor().strip() or os.getenv("PROCESSOR_IDENTIFIER", "").strip() or "CPU"
    gpu_names = _windows_gpus() if os.name == "nt" else ()
    npu_names = _windows_npus() if os.name == "nt" else ()
    ov_devices = tuple(x.upper() for x in _openvino_devices())
    python_gpu = _llama_gpu_offload_supported()
    external_gpu = os.name == "nt" and bool(gpu_names)
    gpu_runtime = python_gpu or external_gpu
    npu_detected = bool(npu_names) or any(x.startswith("NPU") for x in ov_devices)
    openvino_npu = bool(npu_detected and os.name == "nt") or any(x.startswith("NPU") for x in ov_devices)
    notes: list[str] = []
    if gpu_names and not python_gpu:
        notes.append("Для GPU будет подготовлен локальный Vulkan runtime llama.cpp")
    if len(gpu_names) > 1:
        notes.append(f"Обнаружено GPU: {len(gpu_names)}; модель будет распределяться между доступными GPU")
    if npu_detected and not any(x.startswith("NPU") for x in ov_devices):
        notes.append("Для NPU будет подготовлен локальный OpenVINO runtime; требуется рабочий NPU-драйвер")
    return HardwareCapabilities(
        cpu=True,
        cpu_name=cpu_name,
        gpu=bool(gpu_names),
        gpu_names=gpu_names,
        gpu_offload_runtime=gpu_runtime,
        npu=npu_detected,
        npu_names=npu_names or tuple(x for x in ov_devices if x.startswith("NPU")),
        openvino_npu=openvino_npu,
        notes=tuple(notes),
    )


def resolve_execution_plan(config: HardwareConfig, caps: HardwareCapabilities | None = None) -> ExecutionPlan:
    caps = caps or detect_hardware()
    requested = config.mode if config.mode in VALID_MODES else "auto"
    warnings: list[str] = list(caps.notes)
    if requested == "auto":
        if caps.gpu_offload_runtime and caps.openvino_npu:
            effective = "cpu_gpu_npu"
        elif caps.gpu_offload_runtime:
            effective = "cpu_gpu"
        elif caps.openvino_npu:
            effective = "cpu_npu"
        else:
            effective = "cpu"
    else:
        effective = requested
    wants_gpu = "gpu" in effective
    wants_npu = "npu" in effective
    wants_cpu = "cpu" in effective
    if wants_gpu and not caps.gpu_offload_runtime:
        warnings.append("GPU-режим запрошен, но совместимый GPU backend недоступен; используется CPU")
        wants_gpu = False
    if wants_npu and not caps.openvino_npu:
        warnings.append("NPU-режим запрошен, но совместимый NPU/OpenVINO backend недоступен; используется доступный CPU/GPU")
        wants_npu = False
    if wants_gpu:
        chat_device = "gpu"
        chat_backend = "vulkan"
        n_gpu_layers = -1
    elif wants_npu:
        chat_device = "npu"
        chat_backend = "openvino"
        n_gpu_layers = 0
    else:
        chat_device = "cpu"
        chat_backend = "python-cpu"
        n_gpu_layers = 0
    background: list[str] = []
    if wants_npu and chat_device != "npu":
        background.append("npu")
    if wants_gpu and chat_device != "gpu":
        background.append("gpu")
    if wants_cpu and chat_device != "cpu":
        background.append("cpu")
    if not background:
        background.append(chat_device)
    if config.prefer_npu_for_background and "npu" in background:
        embedding_backend = "openvino"
    elif "gpu" in background or chat_device == "gpu":
        embedding_backend = "vulkan"
    elif "npu" in background or chat_device == "npu":
        embedding_backend = "openvino"
    else:
        embedding_backend = "python-cpu"
    effective_parts = []
    if wants_cpu:
        effective_parts.append("cpu")
    if wants_gpu:
        effective_parts.append("gpu")
    if wants_npu:
        effective_parts.append("npu")
    if not effective_parts:
        effective_parts = ["cpu"]
    return ExecutionPlan(
        requested_mode=requested,
        effective_mode="_".join(effective_parts),
        chat_device=chat_device,
        background_devices=tuple(background),
        n_gpu_layers=n_gpu_layers,
        chat_backend=chat_backend,
        embedding_backend=embedding_backend,
        npu_backend="openvino" if wants_npu else None,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def hardware_status(brain_dir: Path) -> dict[str, Any]:
    store = HardwareConfigStore(brain_dir)
    config = store.load()
    caps = detect_hardware()
    plan = resolve_execution_plan(config, caps)
    return {
        "config": asdict(config),
        "capabilities": asdict(caps),
        "plan": asdict(plan),
        "mode_labels": MODE_LABELS,
    }
