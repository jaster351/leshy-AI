"""Fail-fast build diagnostics for missing Leshy Python modules.

Usage:
    python windows/check-frozen-bundle.py source
    python windows/check-frozen-bundle.py archive .autobuild/pyinstaller-work/Leshy/PYZ-00.toc
This script does not modify source files, installation, or build caches.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


REQUIRED_MODULES = (
    "leshy.network_blocking_runtime",
    "leshy.network_blocking_module",
    "leshy.provider_support",
    "leshy.private_support_bootstrap",
)


def source_preflight(root: Path) -> None:
    source = root / "src"
    sys.path.insert(0, str(source))
    for module in REQUIRED_MODULES:
        expected = source.joinpath(*module.split(".")).with_suffix(".py").resolve()
        if not expected.is_file():
            raise RuntimeError(
                f"Missing local source: {expected}. "
                "Check git status, sparse-checkout, and git ls-files for this path."
            )
        spec = importlib.util.find_spec(module)
        origin = Path(str(spec.origin or "")).resolve() if spec else None
        if origin != expected:
            raise RuntimeError(
                f"Wrong import location for {module}: {origin!s} (expected {expected}). "
                "The build may be using a stale/duplicate Python installation."
            )
        print(f"BUILD PREFLIGHT OK: {module} -> {expected}")


def archive_preflight(path: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Missing PyInstaller manifest: {path}")
    content = path.read_text(encoding="utf-8", errors="replace")
    for module in REQUIRED_MODULES:
        if module not in content:
            raise RuntimeError(
                f"PyInstaller did not bundle {module} in {path}. "
                "Check PyInstaller warnings and import resolution before smoke-test."
            )
        print(f"PYINSTALLER MANIFEST OK: {module}")


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    mode = sys.argv[1] if len(sys.argv) > 1 else "source"
    if mode == "source":
        source_preflight(root)
    elif mode == "archive" and len(sys.argv) == 3:
        archive_preflight(Path(sys.argv[2]))
    else:
        raise SystemExit("Usage: check-frozen-bundle.py source|archive <PYZ-00.toc>")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BUILD PREFLIGHT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
