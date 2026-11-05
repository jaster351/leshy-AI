from __future__ import annotations

"""Локальный CPU/GPU профиль llama.cpp; кластерные политики не изменяются.

Управляет только параметрами *одного* llama-server. Выбор узла, маршрутизация
между машинами и резервирование cluster peers остаются за существующим кодом.
"""

from dataclasses import asdict, dataclass
import json
import os
from typing import Mapping


@dataclass(frozen=True)
class LocalInferenceProfile:
    backend: str
    mode: str
    logical_cpus: int
    threads: int
    batch_threads: int

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


def _cpu_count() -> int:
    """Учитываем CPU affinity Linux, не предполагая полного доступа к ядрам."""
    try:
        affinity = os.sched_getaffinity(0)
        if affinity:
            return max(1, len(affinity))
    except (AttributeError, OSError):
        pass
    return max(1, int(os.cpu_count() or 1))


def _thread_override(value: str | None, limit: int) -> int | None:
    try:
        selected = int(str(value).strip())
        return max(1, min(limit, selected)) if selected > 0 else None
    except (TypeError, ValueError):
        return None


def choose_llama_profile(
    backend: str,
    *,
    logical_cpus: int | None = None,
    env: Mapping[str, str] | None = None,
) -> LocalInferenceProfile:
    """Подбираем потоковую конфигурацию без опроса LLM и без рестартов в чате.

    Vulkan при смешанной CPU/GPU нагрузке часто проигрывает при запуске всех
    логических потоков: на гибридном CPU это включает и E-ядра. Ограничиваем
    вычислительную часть GPU backend разумным числом потоков, оставляя
    CPU fallback без изменений. Результат можно переопределить из окружения.
    """
    variables = os.environ if env is None else env
    capacity = max(1, int(logical_cpus if logical_cpus is not None else _cpu_count()))
    name = str(backend).strip().lower()
    mode = str(variables.get("LESHY_PERFORMANCE_PROFILE", "auto")).strip().lower()
    if mode not in {"auto", "compat"}:
        mode = "auto"

    if mode == "auto" and name == "vulkan":
        # Intel 20 logical cores -> 8 рабочих потоков GPU-пути.
        # На слабой машине сохраняем минимум один рабочий поток.
        threads = max(1, min(capacity, 8, max(2, capacity // 2)))
    else:
        threads = capacity

    specific = "LESHY_LLAMA_GPU_THREADS" if name == "vulkan" else "LESHY_LLAMA_CPU_THREADS"
    override = _thread_override(
        variables.get(specific) or variables.get("LESHY_LLAMA_THREADS"), capacity
    )
    if override is not None:
        threads = override
    batch_threads = _thread_override(variables.get("LESHY_LLAMA_BATCH_THREADS"), capacity)
    if batch_threads is None:
        batch_threads = threads

    return LocalInferenceProfile(
        backend=name,
        mode=mode,
        logical_cpus=capacity,
        threads=threads,
        batch_threads=batch_threads,
    )


def main() -> None:
    for backend in ("vulkan", "cpu", "openvino"):
        print(json.dumps(choose_llama_profile(backend).as_dict(), ensure_ascii=False))


if __name__ == "__main__":
    main()
