"""Boundary between a user's instruction and quoted task data.

A pasted ticket or Knowledge document can mention IPs, parcels, payments and
browser actions. Such mentions do not authorize the corresponding tool.
"""
from __future__ import annotations

import ipaddress
import re


def local_memory_intent(message: str) -> bool:
    from .owner_knowledge_capture import (
        is_owner_document_save_request, is_owner_document_status_request,
    )
    from .dialogue_rules import is_direct_rule_request, is_rule_inspection_request
    value = " ".join(str(message or "").casefold().replace("ё", "е").split())
    if not value:
        return False
    if value.startswith(("/remember ", "/memories")):
        return True
    if (is_owner_document_save_request(message) or is_owner_document_status_request(message)
            or is_direct_rule_request(message) or is_rule_inspection_request(message)):
        return True
    start = value[:230]
    return bool(
        re.match(
            r"^(?:леший[,!:]?\s+)?(?:пожалуйста\s+)?"
            r"(?:сохрани|запомни|запиши|добавь|внеси)\b", start,
        )
        and any(term in start for term in (
            "память", "памяти", "knowledge", "знани", "инструкц", "правил",
        ))
    )


_NETWORK_COMMAND = re.compile(
    r"^(?:леший[,!:]?\s+)?(?:пожалуйста[,!:]?\s+)?"
    r"(?:проверь|проверить|проверка|проверяй|продиагностируй|"
    r"диагностика|проведи\s+проверку|"
    r"определи\s+(?:блокировку|доступность)|"
    r"проверь,\s+блокируется\s+ли)\b",
    re.I,
)
_NETWORK_TERMS = ("ip", "айпи", "адрес", "домен", "сайт", "доступ", "блокир",
                  "порт", "ssh", "rdp", "tcp", "udp", "тспу", "чебур", "check-host")


def explicit_network_probe(message: str, target: str) -> bool:
    raw = " ".join(str(message or "").split()).strip()
    if not raw or not target or local_memory_intent(message):
        return False
    # Standalone IPv4/IPv6 address is an existing explicit diagnostic shortcut.
    if raw.strip("[]") == target:
        try:
            ipaddress.ip_address(target)
            return True
        except ValueError:
            return False
    q = raw.casefold().replace("ё", "е")
    if not _NETWORK_COMMAND.match(q[:170]):
        return False
    return any(term in q[:420] for term in _NETWORK_TERMS)


def local_document_study(message: str) -> bool:
    """'Study this earlier local instruction' is not a web-research task."""
    q = " ".join(str(message or "").casefold().replace("ё", "е").split())
    if not re.match(
        r"^(?:леший[,!:]?\s+)?(?:изучи|исследуй|прочитай|выучи|освой)\b", q
    ):
        return False
    if "http://" in q or "https://" in q:
        return False
    return any(term in q[:330] for term in (
        "предыдущ", "ранее передан", "из истории", "в этом диалоге",
        "инструкц", "документ", "моих правил", "knowledge",
    ))


__all__ = ["local_memory_intent", "explicit_network_probe", "local_document_study"]