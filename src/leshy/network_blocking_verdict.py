"""Plain-language blocking verdict for independent TCP/22 and TCP/3389 probes.

Country comparisons are evidence of reachability, never proof of a specific
filtering mechanism. Check-Host is independent supporting evidence.
"""
from __future__ import annotations

from typing import Any


PORT_NAMES = {22: "SSH/22", 3389: "RDP/3389"}


def assess_ports(
    owned: dict[str, Any],
    check_host: tuple[Any, ...],
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Return a concise verdict and an independent assessment for each port."""
    all_ports = owned.get("ports") or {"22": owned}
    assessments: dict[str, dict[str, Any]] = {}

    for port in (22, 3389):
        pair = all_ports.get(str(port)) or {}
        local = [row.get("status") for row in pair.get("local_attempts", ()) if isinstance(row, dict)]
        eu = [row.get("status") for row in pair.get("eu_attempts", ()) if isinstance(row, dict)]
        ru_ch = [
            row.status for row in check_host
            if getattr(row, "port", None) == port
            and str(getattr(row, "country_code", "")).lower() == "ru"
        ]
        eu_ch = [
            row.status for row in check_host
            if getattr(row, "port", None) == port
            and str(getattr(row, "country_code", "")).lower() not in ("", "ru")
        ]
        local_ok = local.count("connected")
        eu_ok = eu.count("connected")
        ru_ch_ok = ru_ch.count("connected")
        ru_ch_timeout = ru_ch.count("timeout")
        other_ch_ok = eu_ch.count("connected")
        region_known = pair.get("status") != "local_region_unverified"

        state = "insufficient"
        explanation = "Недостаточно данных для определения блокировки"
        if local_ok == 3:
            state = "not_blocked"
            explanation = "Порт доступен из сети Лешего (3/3)"
        elif local_ok:
            state = "intermittent"
            explanation = "Порт частично доступен из сети Лешего; возможна нестабильность"
        elif len(local) == 3 and all(x == "timeout" for x in local) and eu_ok == 3:
            if not region_known:
                state = "possible_restriction"
                explanation = "Из сети Лешего тайм-аут, из Нидерландов доступен; география локальной сети не подтверждена"
            elif ru_ch_ok:
                state = "possible_restriction"
                explanation = "Из сети Лешего тайм-аут, но российские узлы Check-Host подключаются; возможна локальная фильтрация"
            else:
                state = "likely_blocked"
                explanation = (
                    "Вероятная блокировка из РФ: три тайм-аута локально, три подключения из Нидерландов"
                    + ("; российские узлы Check-Host также получают timeout" if ru_ch_timeout else
                       "; Check-Host не подтвердил ограничение из РФ")
                )
        elif len(local) == 3 and all(x == "refused" for x in local) and eu_ok == 3:
            state = "possible_restriction"
            explanation = "Из сети Лешего соединение отклоняется, из Нидерландов доступно; возможны ACL/firewall"
        elif len(local) == 3 and all(x in ("timeout", "refused") for x in local) and len(eu) == 3 and all(x in ("timeout", "refused") for x in eu):
            explanation = "Порт недоступен в обеих сетях; это не доказывает блокировку"
        elif ru_ch_timeout and other_ch_ok and not ru_ch_ok:
            state = "possible_restriction"
            explanation = "Check-Host видит тайм-ауты в РФ и доступность за рубежом, но собственные измерения неполные"
        elif pair.get("status") in ("not_configured", "probe_error"):
            explanation = "Зарубежный агент не предоставил результат по этому порту"
        assessments[str(port)] = {
            "state": state,
            "label": PORT_NAMES[port],
            "explanation": explanation,
            "local_ok": local_ok, "local_total": len(local),
            "nl_ok": eu_ok, "nl_total": len(eu),
            "checkhost_ru_ok": ru_ch_ok, "checkhost_ru_total": len(ru_ch),
            "checkhost_ru_timeout": ru_ch_timeout,
            "checkhost_foreign_ok": other_ch_ok,
            "checkhost_foreign_total": len(eu_ch),
        }

    likely = [v["label"] for v in assessments.values() if v["state"] == "likely_blocked"]
    possible = [v["label"] for v in assessments.values() if v["state"] == "possible_restriction"]
    available = [v["label"] for v in assessments.values() if v["state"] == "not_blocked"]
    if likely:
        verdict = "Вероятная блокировка из РФ: " + ", ".join(likely) + ". Механизм ТСПУ не установлен"
    elif possible:
        verdict = "Возможное ограничение доступа: " + ", ".join(possible) + ". Причина не установлена"
    elif len(available) == 2:
        verdict = "Блокировка SSH/22 и RDP/3389 из сети Лешего не выявлена"
    elif available:
        verdict = "Доступен " + ", ".join(available) + "; по другому порту недостаточно данных"
    else:
        verdict = "Недостаточно данных, чтобы подтвердить или исключить блокировку SSH/22 и RDP/3389"
    return verdict, assessments
