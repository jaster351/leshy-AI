"""Fail-fast build diagnostics for missing Leshy Python modules.

Usage:
    python windows/check-frozen-bundle.py source
    python windows/check-frozen-bundle.py archive .autobuild/pyinstaller-work/Leshy/PYZ-00.toc .autobuild/package-dist/Leshy
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
    "leshy.superagent_verified_ssh",
    "leshy.superagent_ssh_plan",
    "leshy.superagent_ssh_jobs",
    "leshy.kitesurf_cdp_adapter",
    "leshy.superagent_remote_gateway",
    "leshy.superagent_rdp_profile",
)


def source_preflight(root: Path) -> None:
    # Parse *every* checked-in Python module before installing dependencies or
    # spending minutes building a frozen EXE. Invalid syntax may look like
    # "missing module" in PyInstaller's warning output.
    verified = 0
    errors: list[str] = []
    for directory in ("src", "tests", "windows"):
        base = root / directory
        if not base.is_dir():
            continue
        for source_file in sorted(base.rglob("*.py")):
            if "__pycache__" in source_file.parts:
                continue
            try:
                compile(source_file.read_bytes(), str(source_file), "exec")
            except (SyntaxError, UnicodeError) as exc:
                line = getattr(exc, "lineno", None)
                message = getattr(exc, "msg", str(exc))
                errors.append(f"{source_file}:{line}: {message}")
                continue
            verified += 1
    if errors:
        raise RuntimeError(
            f"Invalid Python syntax in {len(errors)} source(s):\n"
            + "\n".join(errors)
            + "\nCorrect all reported files before building."
        )
    print(f"BUILD PREFLIGHT OK: {verified} Python sources parsed.")

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
        # Python's import finder can resolve a module with invalid source;
        # compile it without executing side effects before building the EXE.
        compile(expected.read_bytes(), str(expected), "exec")
        print(f"BUILD PREFLIGHT OK: {module} -> {expected}")


def archive_preflight(path: Path, package_root: Path) -> None:
    if not path.is_file():
        raise RuntimeError(f"Missing PyInstaller manifest: {path}")
    content = path.read_text(encoding="utf-8", errors="replace")
    repository_root = Path(__file__).resolve().parents[1]
    source_root = repository_root / "src" / "leshy"
    for module in REQUIRED_MODULES:
        if module in content:
            print(f"PYINSTALLER MANIFEST OK: {module} (compiled PYZ)")
            continue
        filename = module.rsplit(".", 1)[-1] + ".py"
        expected = source_root / filename
        candidates = (
            package_root / "_internal" / "leshy" / filename,
            package_root / "leshy" / filename,
        )
        # Source fallback can be loaded explicitly by frozen launcher.
        if any(
            candidate.is_file() and candidate.read_bytes() == expected.read_bytes()
            for candidate in candidates
        ):
            print(f"PYINSTALLER MANIFEST OK: {module} (verified frozen source fallback)")
            continue
        raise RuntimeError(
            f"PyInstaller failed to package required module {module}: "
            f"neither PYZ nor unchanged source fallback found in {package_root}."
        )


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    mode = sys.argv[1] if len(sys.argv) > 1 else "source"
    if mode == "source":
        source_preflight(root)
    elif mode == "archive" and len(sys.argv) == 4:
        archive_preflight(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        raise SystemExit("Usage: check-frozen-bundle.py source | archive <PYZ-00.toc> <package-root>")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"BUILD PREFLIGHT FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
