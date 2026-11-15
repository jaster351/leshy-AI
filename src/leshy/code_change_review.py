"""Offline Python change review: syntax and high-risk API heuristics.

Never executes repository Python code, imports it, or runs a test suite.
Findings are advisories, not a guarantee of correctness or security.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, asdict
from pathlib import Path
import subprocess
from typing import Any


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    severity: str
    rule: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReviewError(ValueError):
    pass


def _safe_path(root: Path, name: str) -> Path:
    if not name or '\x00' in name or Path(name).is_absolute() or '..' in Path(name).parts:
        raise ReviewError('Invalid relative path')
    target = root / name
    if target.is_symlink() or not target.is_file():
        raise ReviewError('File missing or symlinked')
    if not target.resolve().is_relative_to(root.resolve()):
        raise ReviewError('Path outside repository')
    if target.stat().st_size > 512 * 1024:
        raise ReviewError('File too large to review')
    return target


def review_python_file(root: str | Path, name: str) -> list[Finding]:
    repo = Path(root).resolve(strict=True)
    target = _safe_path(repo, name)
    try:
        source = target.read_text(encoding='utf-8')
    except UnicodeError:
        return [Finding(name, 1, 'error', 'encoding', 'Source is not UTF-8')]
    try:
        module = ast.parse(source, filename=name)
    except SyntaxError as exc:
        return [Finding(name, int(exc.lineno or 1), 'error', 'syntax', str(exc.msg))]
    findings: list[Finding] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.Call):
            continue
        call = node.func
        ident = call.id if isinstance(call, ast.Name) else None
        attr = call.attr if isinstance(call, ast.Attribute) else None
        base = call.value.id if isinstance(call, ast.Attribute) and isinstance(call.value, ast.Name) else None
        if ident in {'eval', 'exec'}:
            findings.append(Finding(name, node.lineno, 'warning', 'dynamic-code',
                                    'Dynamic Python execution requires explicit review'))
        if base == 'os' and attr == 'system':
            findings.append(Finding(name, node.lineno, 'warning', 'shell-command',
                                    'os.system bypasses argument-based subprocess execution'))
        if base == 'pickle' and attr in {'load', 'loads'}:
            findings.append(Finding(name, node.lineno, 'warning', 'unsafe-deserialization',
                                    'Do not unpickle untrusted data'))
        if ((base == 'subprocess' and attr in {'run', 'Popen', 'call', 'check_call', 'check_output'}) or
            ident in {'run', 'Popen'}) and any(k.arg == 'shell' and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
            findings.append(Finding(name, node.lineno, 'warning', 'shell-true',
                                    'shell=True allows shell interpretation of commands'))
    return sorted(findings, key=lambda x: (x.line, x.rule))


def review_changed_python(repo: str | Path, *, max_files: int = 250) -> dict[str, Any]:
    """Review tracked changes and untracked Python files, without running them."""
    root = Path(repo).resolve(strict=True)
    commands = (
        ('git', '-C', str(root), 'diff', '--name-only', '-z', '--diff-filter=ACMR', 'HEAD', '--'),
        ('git', '-C', str(root), 'ls-files', '--others', '--exclude-standard', '-z', '--'),
    )
    paths: set[str] = set()
    for cmd in commands:
        result = subprocess.run(cmd, capture_output=True, timeout=20, check=False)
        if result.returncode:
            raise ReviewError('Could not enumerate changed files')
        paths.update(p.decode('utf-8', errors='surrogateescape') for p in result.stdout.split(b'\0') if p)
    selected = sorted(name for name in paths if name.lower().endswith('.py'))
    if len(selected) > max_files:
        raise ReviewError('Too many Python files for bounded review')
    findings: list[Finding] = []
    for name in selected:
        try:
            findings.extend(review_python_file(root, name))
        except ReviewError as exc:
            findings.append(Finding(name, 1, 'error', 'file-boundary', str(exc)))
    return {'files_checked': selected, 'findings': [f.as_dict() for f in findings],
            'syntax_ok': all(f.rule != 'syntax' for f in findings),
            'review_passed': not any(f.severity == 'error' for f in findings)}
