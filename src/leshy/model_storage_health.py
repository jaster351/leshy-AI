"""Portable machine-readable health report for local Leshy NVMe model staging.

Read-only inspection: no download, no inference launch, no backend switch.
The active model is NOT inferred from whether a checkpoint is installed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys

from .inkling_original import checkpoint_status
from .inferssd_observations import infer_home, native_streaming_status
from .moe_model_download import model_status as moe_status


def _infer_binary(brain: Path) -> tuple[bool, str, Path]:
    candidates = [brain / "runtime" / "inferssd", infer_home()]
    custom_home = os.environ.get("INFERSSD_HOME", "").strip()
    if custom_home:
        candidates.insert(0, Path(custom_home).expanduser())
    for home in candidates:
        home = home.resolve()
        exe = (
            home / "current" / "bin" / "inferssd.exe"
            if os.name == "nt" else home / "bin" / "inferssd"
        )
        if exe.is_file():
            return True, str(exe), home
    command = shutil.which("inferssd.exe" if os.name == "nt" else "inferssd")
    if command and Path(command).is_file():
        return True, str(command), infer_home()
    return False, "", candidates[0].resolve()


def storage_health(brain_dir: Path) -> dict:
    brain = Path(brain_dir).expanduser().resolve()
    original = checkpoint_status(brain)
    gguf = moe_status(brain)
    installed, executable, home = _infer_binary(brain)
    evidence = native_streaming_status(Path(gguf["file"]), home=home)
    locations = {
        "brain": brain,
        "inkling_original": Path(original["path"]),
        "inkling_offload": Path(original["offload_path"]),
        "inferssd_gguf": Path(gguf["file"]).parent,
        "inferssd_home": home,
    }
    space = {}
    for role, path in locations.items():
        try:
            existing = path
            while not existing.exists() and existing != existing.parent:
                existing = existing.parent
            usage = shutil.disk_usage(existing)
            space[role] = {
                "path": str(path),
                "free_bytes": usage.free,
                "total_bytes": usage.total,
            }
        except OSError as exc:
            space[role] = {"path": str(path), "error": type(exc).__name__}
    return {
        "schema": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "models": {
            "inkling_original": original,
            "inferssd_moe": gguf,
        },
        "native_engine": {
            "installed": installed,
            "executable": executable,
            "home": str(home),
        },
        "ssd_streaming": evidence,
        "storage": space,
        "no_remote_gpu": True,
        "note": "Downloaded checkpoint does not imply successful inference or native SSD streaming.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Leshy local NVMe status (read-only)")
    parser.add_argument("--brain-dir", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = storage_health(Path(args.brain_dir))
    value = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        target = Path(args.output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(value + "\n", encoding="utf-8")
        os.replace(temporary, target)
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
