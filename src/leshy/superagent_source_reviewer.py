"""Independent, deterministic reviewer of Knowledge search SOURCE METADATA.

This is an offline, read-only second pass. It checks whether saved citations
are usable and diverse; it does not fetch URLs or assess factual truth.
Raw queries, claims, URLs and hostnames must never enter its output.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit
from typing import Any


def _citable(url: str) -> str | None:
    """Return a hostname only internally; never export it in the review."""
    if not url or len(url) > 500 or any(ord(c) < 33 for c in url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"https", "http"}:
            return None
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return None
        hostname = parsed.hostname.lower().rstrip(".")
        if not hostname or len(hostname) > 253:
            return None
        # A URL is not a source if it is clearly malformed. No request is made.
        if not re.fullmatch(r"[a-z0-9.:-]+", hostname):
            return None
        # Reject obvious internal hosts and IP literals; their presence is
        # not evidence of an independently attributable web citation.
        if hostname == "localhost" or hostname.endswith(".local"):
            return None
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            return None
        if "." not in hostname or ".." in hostname:
            return None
        try:
            _ = parsed.port
        except ValueError:
            return None
        return hostname
    except (ValueError, TypeError):
        return None


def review_knowledge_sources(result: Any) -> dict[str, Any]:
    """A second, non-LLM pass over citation presence, never fact verification.

    Only statistics and predefined issue codes are returned. Strict shape
    prevents corrupt tool output from becoming falsely "reviewed".
    """
    if not isinstance(result, dict):
        raise ValueError("Knowledge tool output is not a JSON object")
    items = result.get("items")
    count = result.get("count")
    if not isinstance(items, list) or type(count) is not int or not 0 <= count <= 5 or len(items) != count:
        raise ValueError("Knowledge output count is inconsistent")
    hosts: set[str] = set()
    valid_links = 0
    missing_links = 0
    repeated_claims = 0
    seen_claims: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Knowledge item is not an object")
        claim = item.get("claim")
        url = item.get("source_url")
        if not isinstance(claim, str) or not isinstance(url, str):
            raise ValueError("Knowledge claim/source has invalid type")
        normalized = " ".join(claim.casefold().split())
        if normalized in seen_claims:
            repeated_claims += 1
        else:
            seen_claims.add(normalized)
        hostname = _citable(url)
        if hostname:
            hosts.add(hostname)
            valid_links += 1
        else:
            missing_links += 1
    if not count:
        status = "empty"
    elif not valid_links:
        status = "missing_sources"
    elif missing_links or len(hosts) < 2 or repeated_claims:
        status = "limited_sources"
    else:
        status = "multiple_hosts"
    return {
        "version": 1, "status": status, "items": count,
        "linked_items": valid_links, "distinct_hosts": len(hosts),
        "missing_links": missing_links, "repeated_claims": repeated_claims,
        # Deliberately no claim text, source URLs, hostnames or prompts.
        "fact_checked": False,
    }


__all__ = ["review_knowledge_sources"]
