"""Numeric, mentor-only inference telemetry. Never capture prompts or answers."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import ctypes
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

_CAPTURE = ContextVar("mentor_performance_capture", default=None)
_BACKENDS = {"vulkan", "openvino", "cpu", "native-gguf", "cpu-native", "cpu-llama-server"}
_REPO = "jaster351/AI-mentor"


def _num(value, maximum=1e12):
    if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum:
        return value
    return None


def _count(value):
    return value if type(value) is int and 0 <= value <= 10_000_000 else None


def model_descriptor(value):
    # Only conventional public family/size/quantization identifiers.
    # Never send an arbitrary filename, path or user-defined model alias.
    name = Path(str(value or "").replace("\\", "/")).name.lower()
    match = re.match(r"^(qwen[0-9.]*|llama[0-9.]*|mistral|mixtral|gemma[0-9.]*|deepseek|phi[0-9.]*|gpt-oss)(?:[-_.]|$)", name)
    family = match.group(1) if match else None
    size = re.search(r"(?:^|[-_.])([0-9]{1,4}(?:\.[0-9]+)?b)(?=[-_.]|$)", name)
    quant = re.search(r"(?:^|[-_.])(q[2-6]_k(?:_[sml])?|q[458]_[01]|q8_k|iq[1-4]_(?:s|m|xs|xxs|nl)|f16|bf16|f32)(?=[-.]|$)", name)
    return {"family": family, "parameter_size": size.group(1) if family and size else None,
            "quantization": quant.group(1).upper() if family and quant else None}


@contextmanager
def capture_mentor_inference():
    rows = []
    marker = _CAPTURE.set(rows)
    try:
        yield rows
    finally:
        _CAPTURE.reset(marker)


def observe_completion(body, *, backend, model, duration_seconds):
    """Called only for actual non-streaming backend responses; no global chat history."""
    rows = _CAPTURE.get()
    if rows is None or len(rows) >= 16:
        return
    try:
        usage = body.get("usage") if isinstance(body, dict) else None
        timings = body.get("timings") if isinstance(body, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        timings = timings if isinstance(timings, dict) else {}
        prompt = _count(usage.get("prompt_tokens"))
        output = _count(usage.get("completion_tokens"))
        predicted = _count(timings.get("predicted_n"))
        ms = _num(timings.get("predicted_ms"), 3_600_000)
        # Backend generation throughput only when both real timing counters exist.
        rate = predicted * 1000 / ms if predicted is not None and ms is not None and ms > 0 else None
        rows.append({"backend": backend if backend in _BACKENDS else "other",
                     "model": model_descriptor(model),
                     "prompt_tokens": prompt, "completion_tokens": output,
                     "generation_tokens": predicted if rate is not None else None,
                     "generation_ms": ms if rate is not None else None,
                     "generation_tokens_per_second": round(rate, 3) if rate is not None else None,
                     "backend_request_seconds": _num(duration_seconds, 3600),
                     "request_output_tokens_per_second": round(output/duration_seconds, 3)
                     if output is not None and _num(duration_seconds, 3600) and duration_seconds > 0 else None})
    except Exception:
        # Diagnostics must never change the answer or fail over a backend.
        return


def cpu_clock():
    try:
        if os.name == "nt":
            from ctypes import wintypes
            idle, kernel, user = (wintypes.FILETIME() for _ in range(3))
            if not ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return None
            def ticks(x):
                return (x.dwHighDateTime << 32) | x.dwLowDateTime
            return ticks(idle), ticks(kernel) + ticks(user)
        if Path("/proc/stat").is_file():
            with open("/proc/stat", encoding="ascii") as f:
                values = [int(x) for x in f.readline().split()[1:9]]
            return values[3] + values[4], sum(values)
    except Exception:
        pass
    return None


def _cpu_percent(before, after):
    if before is None or after is None:
        return None
    idle, total = after[0] - before[0], after[1] - before[1]
    if total <= 0 or not 0 <= idle <= total:
        return None
    return round(100 * (total-idle) / total, 2)


def _memory():
    try:
        if os.name == "nt":
            class Memory(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (key, ctypes.c_ulonglong) for key in (
                        "total_phys", "avail_phys", "total_page", "avail_page",
                        "total_virtual", "avail_virtual", "avail_extended")]
            m = Memory()
            m.length = ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return {"ram_total_mib": m.total_phys // 1048576,
                        "ram_available_mib": m.avail_phys // 1048576,
                        "ram_used_percent": int(m.load)}
        elif Path("/proc/meminfo").is_file():
            with open("/proc/meminfo", encoding="ascii") as f:
                rows = {line.split(":")[0]: int(line.split()[1]) for line in f if line.startswith(("MemTotal:", "MemAvailable:"))}
            total, free = rows["MemTotal"], rows["MemAvailable"]
            if total > 0:
                return {"ram_total_mib": total // 1024, "ram_available_mib": free // 1024,
                        "ram_used_percent": round(100*(total-free)/total, 2)}
    except Exception:
        pass
    return {}


def _gpu():
    # Numeric device counters only. No names, serial numbers, process lists or UUIDs.
    try:
        exe = shutil.which("nvidia-smi")
        if not exe and os.name == "nt":
            candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
            if candidate.is_file():
                exe = str(candidate)
        if not exe:
            return []
        result = subprocess.run([exe, "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                                 "--format=csv,noheader,nounits"], capture_output=True,
                                text=True, timeout=2, check=True,
                                creationflags=0x08000000 if os.name == "nt" else 0)
        rows = []
        for line in result.stdout.splitlines()[:16]:
            values = line.split(",")
            if len(values) != 4:
                continue
            def number(v, maximum):
                try:
                    return _num(float(v.strip()), maximum)
                except ValueError:
                    return None
            load, used, total, temp = (number(v, cap) for v, cap in zip(values, (100, 10_000_000, 10_000_000, 150)))
            rows.append({"gpu_index": len(rows), "gpu_used_percent": load,
                         "vram_used_mib": used, "vram_total_mib": total, "temperature_c": temp})
        return rows
    except Exception:
        return []


def _build_signal(brain):
    # Fresh profiler sample is a signal, not evidence that the task is still alive.
    try:
        if len(brain.parents) < 3 or brain != brain.parents[2] / "dist" / "Leshy" / "LeshyBrain":
            return None
        auto = brain.parents[2] / ".autobuild"
        log = auto / "logs" / "build-performance-latest.log"
        if time.time() - log.stat().st_mtime > 20:
            return None
        stage_path = auto / "profile-stage.tmp"
        with stage_path.open(encoding="ascii") as f:
            stage = f.read(80).strip()
        allowed = {"preflight", "local-module-preflight", "pip-dependencies", "brand-icon",
                   "workspace-preparation", "pyinstaller", "frozen-manifest-preflight",
                   "knowledge-copy", "frozen-smoke-test", "inno-setup", "publish-or-stage"}
        return stage if stage in allowed else None
    except Exception:
        return None


class CyclePerformance:
    """Ephemeral aggregation of real mentor calls, with bounded hardware sampling."""
    def __init__(self, brain):
        self.brain = Path(brain).resolve()
        self.rows = []
        self.hardware = []
        self.last_hardware = -1e30
        self.answers = 0
        self.duration = 0.0
        self.min_answer = None
        self.max_answer = None

    def add(self, rows, elapsed, before):
        self.answers += 1
        duration = _num(elapsed, 3600)
        if duration is not None:
            self.duration += duration
            self.min_answer = duration if self.min_answer is None else min(self.min_answer, duration)
            self.max_answer = duration if self.max_answer is None else max(self.max_answer, duration)
        self.rows.extend(rows[:16])
        # Sample host resources, not private process names, no more than once/15 seconds.
        if time.monotonic() - self.last_hardware >= 15 and len(self.hardware) < 256:
            self.last_hardware = time.monotonic()
            self.hardware.append({"cpu_used_percent": _cpu_percent(before, cpu_clock()),
                                  **_memory(), "gpus": _gpu(),
                                  "fresh_build_stage": _build_signal(self.brain)})

    def summary(self):
        rows = self.rows
        def stats(key):
            values = [r[key] for r in rows if r.get(key) is not None]
            return {"samples": len(values), "sum": sum(values) if values else None,
                    "min": min(values) if values else None,
                    "mean": round(sum(values)/len(values), 3) if values else None,
                    "max": max(values) if values else None}
        measured = [r for r in rows if r["generation_tokens_per_second"] is not None]
        ms = sum(r["generation_ms"] for r in measured)
        gen = stats("generation_tokens_per_second")
        gen["weighted_mean"] = round(1000*sum(r["generation_tokens"] for r in measured)/ms, 3) if ms > 0 else None
        # Separate actual backend decode time from end-to-end answer latency.
        models = []
        for r in rows:
            item = {"backend": r["backend"], **r["model"]}
            if item not in models:
                models.append(item)
        cpu = [r["cpu_used_percent"] for r in self.hardware if r["cpu_used_percent"] is not None]
        ram = [r["ram_used_percent"] for r in self.hardware if r.get("ram_used_percent") is not None]
        return {"scope": "mentor_answers_only", "answers_completed": self.answers,
                "backend_response_samples": len(rows), "models": models,
                "prompt_tokens": stats("prompt_tokens"), "completion_tokens": stats("completion_tokens"),
                "generation_tokens_per_second": gen,
                "request_output_tokens_per_second": stats("request_output_tokens_per_second"),
                "answer_seconds": {"sum": round(self.duration, 3),
                                   "min": self.min_answer, "max": self.max_answer,
                                   "mean": round(self.duration/self.answers, 3) if self.answers else None},
                "host_samples": len(self.hardware),
                "cpu_mean_percent": round(sum(cpu)/len(cpu), 2) if cpu else None,
                "cpu_max_percent": max(cpu) if cpu else None,
                "ram_max_used_percent": max(ram) if ram else None,
                "fresh_build_samples": sum(bool(r["fresh_build_stage"]) for r in self.hardware),
                "latest_host_sample": self.hardware[-1] if self.hardware else None,
                "token_measurement_note": "partial coverage; absent counters are null, not zero"}
