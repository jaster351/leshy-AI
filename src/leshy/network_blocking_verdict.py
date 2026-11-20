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
    *,
    cheburcheck: Any = None,
    chebur_probe: Any = None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Return a concise verdict and an independent assessment for each port."""
    all_ports = owned.get("ports") or {"22": owned}
    assessments: dict[str, dict[str, Any]] = {}
    # Only a completed Cheburcheck probe's tspu_block is direct service
    # attribution; list inclusion alone says nothing about live TCP filtering.
    reported_tspu = bool(
        chebur_probe and getattr(chebur_probe, "ok", False)
        and getattr(chebur_probe, "finished", False)
        and any(
            "tspu_block" in getattr(row, "verdicts", ())
            for row in getattr(chebur_probe, "observations", ())
        )
    )
    listed = bool(cheburcheck and getattr(cheburcheck, "ok", False) and getattr(cheburcheck, "blocked", False))

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
            # The two owner-controlled probes are the primary observation.
            # Third-party vantage points may corroborate but cannot override
            # the owner's measured path or prove the filtering mechanism.
            if not region_known:
                state = "possible_restriction"
                explanation = (
                    "Собственные точки расходятся (Леший: 0/3, EU: 3/3), "
                    "но расположение локальной точки в РФ не подтверждено"
                )
            elif reported_tspu and ru_ch_timeout and other_ch_ok and not ru_ch_ok:
                state = "tspu_detected"
                explanation = (
                    "Собственные точки: Леший 0/3 timeout, EU 3/3 connected; "
                    "Check-Host сообщает timeout из РФ и доступность извне; "
                    "завершённый Cheburcheck Probe сообщил tspu_block. "
                    "Это диагностический вывод, не прямое измерение оборудования ТСПУ"
                )
            else:
                state = "owned_path_restriction"
                explanation = (
                    "Собственные точки: Леший 0/3 (тайм-аут), "
                    "EU 3/3 (успешно). Различие доступности подтверждено, "
                    "причина не установлена; блокировка именно ТСПУ не подтверждена"
                )
                if ru_ch_ok:
                    explanation += (
                        "; Check-Host из РФ подключается, но его результаты "
                        "не отменяют измерения Вашей сети"
                    )
                elif ru_ch_timeout and other_ch_ok:
                    explanation += "; Check-Host дополнительно показывает различие по странам"
        elif len(local) == 3 and all(x == "refused" for x in local) and eu_ok == 3:
            state = "possible_restriction"
            explanation = "Из сети Лешего соединение отклоняется, из Нидерландов доступно; возможны ACL/firewall"
        elif len(local) == 3 and all(x in ("timeout", "refused") for x in local) and len(eu) == 3 and all(x in ("timeout", "refused") for x in eu):
            explanation = "Порт недоступен в обеих сетях; это не доказывает блокировку"
        elif ru_ch_timeout and other_ch_ok and not ru_ch_ok:
            # External samples alone must never establish a positive verdict.
            explanation = (
                "Check-Host видит различия между странами, но собственных "
                "измерений Лешего и EU недостаточно для вывода"
            )
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

    detected = [v["label"] for v in assessments.values() if v["state"] == "tspu_detected"]
    owned_path = [v["label"] for v in assessments.values() if v["state"] == "owned_path_restriction"]
    likely = [v["label"] for v in assessments.values() if v["state"] == "likely_blocked"]
    possible = [v["label"] for v in assessments.values() if v["state"] == "possible_restriction"]
    available = [v["label"] for v in assessments.values() if v["state"] == "not_blocked"]
    if detected:
        verdict = (
            "Обнаружена блокировка на стороне ТСПУ: " + ", ".join(detected)
            + " (по совокупности диагностических признаков)"
        )
    elif owned_path:
        verdict = (
            "Блокировка на стороне ТСПУ не подтверждена для "
            + ", ".join(owned_path)
            + "; собственные точки показывают разную доступность, причина не установлена"
        )
    elif likely:
        verdict = "Вероятная блокировка из РФ: " + ", ".join(likely) + ". Причина не доказана"
    elif possible:
        verdict = "Возможное ограничение доступа: " + ", ".join(possible) + ". Причина не установлена"
    elif len(available) == 2:
        verdict = "Блокировка не обнаружена на стороне ТСПУ для SSH/22 и RDP/3389"
    elif available:
        verdict = "Блокировка не обнаружена на стороне ТСПУ для " + ", ".join(available) + "; другой порт не подтверждён"
    else:
        verdict = "Недостаточно данных, чтобы подтвердить или исключить блокировку TCP/22 и TCP/3389"
    return verdict, assessments
