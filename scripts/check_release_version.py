#!/usr/bin/env python3
"""Проверяет обязательное повышение версии при изменении исполняемого кода.

Использование: python scripts/check_release_version.py <base-commit>
GitHub Actions передаёт SHA базового коммита PR или предыдущего коммита main.
"""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_PROGRAM_SUFFIXES = {
    ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".html", ".css",
    ".ps1", ".cmd", ".bat", ".sh", ".toml", ".yml", ".yaml", ".iss",
}
_PROGRAM_ROOTS = ("src/", "web/", "windows/", "scripts/", ".github/workflows/", "packaging/")
_PROGRAM_ROOT_FILES = {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"}


def git(*arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=ROOT, text=True, encoding="utf-8", stderr=subprocess.PIPE
    ).strip()


def numeric_version(text: str, location: str) -> tuple[int, int, int]:
    value = text.strip()
    match = _VERSION_RE.fullmatch(value)
    if not match:
        raise ValueError(f"Некорректный VERSION в {location}: {value!r}; ожидается X.Y.Z")
    return tuple(int(part) for part in match.groups())


def is_program_file(name: str) -> bool:
    path = name.replace("\\", "/")
    if path in _PROGRAM_ROOT_FILES:
        return True
    if not path.startswith(_PROGRAM_ROOTS):
        return False
    return Path(path).suffix.lower() in _PROGRAM_SUFFIXES


def check(base: str) -> int:
    changed = git("diff", "--name-only", "--diff-filter=ACMRT", base, "HEAD").splitlines()
    program_files = [name for name in changed if is_program_file(name)]
    if not program_files:
        print("VERSION: исполняемые файлы не менялись; повышение версии не требуется.")
        return 0

    previous = numeric_version(git("show", f"{base}:VERSION"), base)
    current = numeric_version((ROOT / "VERSION").read_text(encoding="utf-8"), "рабочем дереве")
    if current <= previous:
        print("ОШИБКА: исполняемые файлы изменены, но VERSION не повышен.", file=sys.stderr)
        print(f"Было: {'.'.join(map(str, previous))}; сейчас: {'.'.join(map(str, current))}", file=sys.stderr)
        print("Изменены:\n" + "\n".join(f"  {name}" for name in program_files[:30]), file=sys.stderr)
        print("Увеличьте VERSION до следующей версии перед выпуском.", file=sys.stderr)
        return 1
    print(
        f"VERSION: {'.'.join(map(str, previous))} → {'.'.join(map(str, current))}; "
        f"проверено исполняемых файлов: {len(program_files)}."
    )
    return 0


def main() -> int:
    if len(sys.argv) != 2 or not re.fullmatch(r"[0-9a-fA-F]{7,40}", sys.argv[1]):
        print("Использование: python scripts/check_release_version.py <base-commit>", file=sys.stderr)
        return 2
    try:
        return check(sys.argv[1])
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        print(f"Не удалось проверить версию выпуска: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
