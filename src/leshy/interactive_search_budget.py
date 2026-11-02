from __future__ import annotations

import queue
import threading
import time

from .diagnostics import event, problem


INTERACTIVE_SEARCH_BUDGET_SECONDS = 12.0
_MAX_DETACHED_SEARCHES = 3
_INSTALLED = False
_ORIGINAL_LIVE_SEARCH = None
_ACTIVE_LOCK = threading.RLock()
_ACTIVE_DETACHED = 0


def install_interactive_search_budget(app_class) -> None:
    """Не позволять live-search задерживать пользовательский ответ на минуту.

    Поиск выполняется до inference scheduler. Последовательный DDGS failover
    способен занимать десятки секунд, поэтому интерактивный запрос получает
    общий wall-clock budget. Истекший worker остаётся daemon и может завершить
    внутренний сетевой вызов самостоятельно, но пользовательский чат продолжает
    работу сразу после budget timeout.
    """
    global _INSTALLED, _ORIGINAL_LIVE_SEARCH
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_LIVE_SEARCH = app_class.live_search

    def live_search(self, query: str, *, limit: int = 5, reason: str = "прямой запрос пользователя") -> dict:
        global _ACTIVE_DETACHED
        result_queue: queue.Queue[tuple[str, object]] = queue.Queue(maxsize=1)
        started = time.monotonic()

        with _ACTIVE_LOCK:
            if _ACTIVE_DETACHED >= _MAX_DETACHED_SEARCHES:
                event(
                    "interactive_search_capacity_exhausted",
                    active_detached=_ACTIVE_DETACHED,
                    query=str(query or "")[:300],
                )
                raise TimeoutError("Интернет-поиск временно занят предыдущими медленными источниками")
            _ACTIVE_DETACHED += 1

        def worker() -> None:
            global _ACTIVE_DETACHED
            try:
                value = _ORIGINAL_LIVE_SEARCH(self, query, limit=limit, reason=reason)
                try:
                    result_queue.put_nowait(("result", value))
                except queue.Full:
                    pass
            except BaseException as exc:
                try:
                    result_queue.put_nowait(("error", exc))
                except queue.Full:
                    pass
            finally:
                with _ACTIVE_LOCK:
                    _ACTIVE_DETACHED = max(0, _ACTIVE_DETACHED - 1)

        thread = threading.Thread(target=worker, name="leshy-live-search-budget", daemon=True)
        thread.start()
        try:
            kind, payload = result_queue.get(timeout=INTERACTIVE_SEARCH_BUDGET_SECONDS)
        except queue.Empty as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            event(
                "interactive_search_budget_exceeded",
                query=str(query or "")[:300],
                reason=str(reason or "")[:200],
                budget_seconds=INTERACTIVE_SEARCH_BUDGET_SECONDS,
                elapsed_ms=elapsed_ms,
            )
            raise TimeoutError(
                f"Свежий интернет-поиск не завершился за {int(INTERACTIVE_SEARCH_BUDGET_SECONDS)} секунд"
            ) from exc

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if kind == "error":
            problem("interactive_search_failed", str(payload), elapsed_ms=elapsed_ms)
            raise payload  # type: ignore[misc]
        event("interactive_search_finished", elapsed_ms=elapsed_ms, query=str(query or "")[:300])
        return payload  # type: ignore[return-value]

    app_class.live_search = live_search
    event(
        "interactive_search_budget_installed",
        budget_seconds=INTERACTIVE_SEARCH_BUDGET_SECONDS,
        max_detached_searches=_MAX_DETACHED_SEARCHES,
    )


__all__ = ["INTERACTIVE_SEARCH_BUDGET_SECONDS", "install_interactive_search_budget"]
