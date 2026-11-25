"""Ограниченная локальная выборка нативных счётчиков MoE InferSSD.

Не запускает движок, не меняет кэш и не обращается к сети. Не является
доказательством физического чтения NVMe или идентичности весов.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .inferssd_observations import MAX_EVENT_LINE, _model_matches_run

MAX_SAMPLE_BYTES = 8 * 1024 * 1024
MAX_SAMPLE_EVENTS = 8192


def sample_native_run(run_dir: Path, model: Path) -> dict:
    """Считает только валидный непрерывный префикс журнала выбранного запуска."""
    run_dir = Path(run_dir)
    model = Path(model).expanduser().resolve()
    receipt = run_dir / "runtime.json"
    events = run_dir / "native-events.jsonl"
    if (run_dir.is_symlink() or receipt.is_symlink() or events.is_symlink()
            or not run_dir.is_dir() or not model.is_file()
            or not _model_matches_run(run_dir, model)
            or not events.is_file()):
        raise ValueError("Не подтверждена связь локального запуска с моделью")
    seen = size = routed = selected = vram = cache = both = neither = 0
    prefill_rows = prefill_bytes = ignored_routes = invalid_prefill = 0
    contract = False
    termination = "event_budget"
    with events.open("rb") as handle:
        for _ in range(MAX_SAMPLE_EVENTS):
            remaining = MAX_SAMPLE_BYTES - size
            if remaining <= 0:
                termination = "byte_budget"
                break
            cap = min(remaining, MAX_EVENT_LINE + 1)
            line = handle.readline(cap)
            if not line:
                termination = "snapshot_eof"
                break
            if not line.endswith(b"\n"):
                termination = ("byte_budget" if len(line) == remaining else
                               "line_budget" if len(line) > MAX_EVENT_LINE else
                               "incomplete_line")
                break
            if len(line) > MAX_EVENT_LINE:
                termination = "line_budget"
                break
            event = json.loads(line)
            if (not isinstance(event, dict) or type(event.get("v")) is not int
                    or event["v"] != 1 or type(event.get("seq")) is not int
                    or event["seq"] != seen):
                raise ValueError("Нарушен порядок или формат нативных событий")
            seen += 1
            size += len(line)
            kind = event.get("ev")
            if kind == "ssd.controller.begin":
                if contract or type(event.get("schema")) is not int or event["schema"] != 1:
                    raise ValueError("Некорректный контракт контроллера")
                contract = True
            elif kind == "ssd.place.layer" and contract:
                ids = event.get("selected_experts")
                vh = event.get("vram_hit")
                ch = event.get("cache_hit")
                if not (event.get("success") is True and event.get("decode") is True
                        and event.get("has_token") is True and isinstance(ids, list)
                        and 0 < len(ids) <= 4096
                        and all(type(i) is int and i >= 0 for i in ids)
                        and isinstance(vh, list) and isinstance(ch, list)
                        and len(ids) == len(vh) == len(ch)
                        and all(type(x) is bool for x in (*vh, *ch))):
                    ignored_routes += 1
                    continue
                routed += 1
                selected += len(ids)
                vram += sum(vh)
                cache += sum(ch)
                both += sum(a and b for a, b in zip(vh, ch))
                neither += sum(not a and not b for a, b in zip(vh, ch))
            elif kind == "ssd.prefill.layer" and contract:
                amount = event.get("read_bytes")
                if type(amount) is int and 0 <= amount < 2**63:
                    prefill_rows += 1
                    prefill_bytes += amount
                else:
                    invalid_prefill += 1
    if not contract:
        raise ValueError("В выборке отсутствует подтверждённый контракт контроллера")
    return {
        "schema": "leshy.moe-native-sample.v1",
        "source": "inferssd_native_events",
        "model_path_matches": True,
        "model_weights_sha256_confirmed": False,
        "scope": "bounded_run_prefix",
        "termination": termination,
        "sample_events": seen,
        "sample_bytes": size,
        "decode_routed_layers": routed,
        "ignored_route_events": ignored_routes,
        "selected_expert_references": selected,
        "vram_hit_flags": vram,
        "cache_hit_flags": cache,
        "both_hit_flags": both,
        "neither_hit_flags": neither,
        "vram_hit_fraction": round(vram / selected, 6) if selected else None,
        "cache_hit_fraction": round(cache / selected, 6) if selected else None,
        "prefill_read_counter_events": prefill_rows,
        "invalid_prefill_counters": invalid_prefill,
        "declared_prefill_read_bytes": prefill_bytes if prefill_rows else None,
        "physical_ssd_read_bytes": None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--model", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = sample_native_run(args.run_dir, args.model)
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Не удалось получить выборку: {type(exc).__name__}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
