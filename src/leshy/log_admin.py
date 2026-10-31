from __future__ import annotations

from pathlib import Path
from typing import Any

from .diagnostics import event, problem


class RuntimeLogAdmin:
    """Безопасное управление только runtime-логами Лешего.

    Никогда не выходит за пределы LeshyBrain/logs и не затрагивает SQLite,
    настройки, модели, историю чатов или другие пользовательские данные.
    """

    def __init__(self, brain_dir: Path | str):
        self.log_dir = Path(brain_dir).resolve() / "logs"

    def clear(self) -> dict[str, Any]:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        removed = 0
        truncated = 0
        failed: list[str] = []
        for path in list(self.log_dir.rglob("*")):
            if not path.is_file():
                continue
            try:
                # Активный leshy.log может быть открыт текущим процессом. Сначала
                # пробуем очистить содержимое без удаления дескриптора.
                if path.name.lower() == "leshy.log":
                    path.write_text("", encoding="utf-8")
                    truncated += 1
                else:
                    path.unlink()
                    removed += 1
            except OSError as exc:
                try:
                    path.write_text("", encoding="utf-8")
                    truncated += 1
                except OSError:
                    failed.append(f"{path.name}: {exc}")
        for directory in sorted((p for p in self.log_dir.rglob("*") if p.is_dir()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        event("runtime_logs_cleared", removed=removed, truncated=truncated, failed=len(failed))
        if failed:
            problem("runtime_logs_clear_partial", "; ".join(failed[:10]), failed=len(failed))
        return {
            "ok": not failed,
            "path": str(self.log_dir),
            "removed_files": removed,
            "truncated_files": truncated,
            "failed": failed,
        }


__all__ = ["RuntimeLogAdmin"]
