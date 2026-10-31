from __future__ import annotations

import json
import re
from typing import Any
from urllib import parse, request

from .diagnostics import event, problem


MAX_PLUGIN_RESPONSE = 2 * 1024 * 1024


class MarketplacePluginRuntime:
    """Исполняет декларативные HTTPS-инструменты включённых Marketplace plugins.

    Плагин не импортируется как произвольный Python-код внутрь процесса Лешего.
    Инструменты описываются в `plugin.tools` манифеста и вызываются как HTTPS JSON API.
    """

    def __init__(self, marketplace):
        self.marketplace = marketplace

    @staticmethod
    def _words(text: str) -> set[str]:
        return {token for token in re.findall(r"[\w.-]{3,}", str(text or "").lower(), flags=re.UNICODE)}

    def tools(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for ext in self.marketplace.installed():
            if not ext.get("enabled") or ext.get("type") != "plugin":
                continue
            cfg = ext.get("plugin") if isinstance(ext.get("plugin"), dict) else {}
            declared = cfg.get("tools") if isinstance(cfg.get("tools"), list) else []
            for tool in declared:
                if not isinstance(tool, dict):
                    continue
                name = str(tool.get("name") or "").strip()
                endpoint = str(tool.get("endpoint") or tool.get("url") or "").strip()
                if not name or not endpoint:
                    continue
                parsed = parse.urlparse(endpoint)
                if parsed.scheme != "https" or not parsed.netloc:
                    continue
                rows.append({
                    "ref": f"{ext['id']}.{name}",
                    "plugin_id": str(ext["id"]),
                    "plugin_name": str(ext.get("name") or ext["id"]),
                    "name": name,
                    "description": str(tool.get("description") or "")[:1000],
                    "endpoint": endpoint,
                    "method": str(tool.get("method") or "POST").upper(),
                    "input_field": str(tool.get("input_field") or "query")[:80],
                    "triggers": [str(x).lower().strip() for x in (tool.get("triggers") or []) if str(x).strip()][:50],
                    "timeout": max(2, min(int(tool.get("timeout") or 20), 60)),
                })
        return rows

    def match(self, message: str) -> dict[str, Any] | None:
        text = str(message or "").lower()
        words = self._words(text)
        best: tuple[int, dict[str, Any]] | None = None
        for tool in self.tools():
            score = 0
            plugin_id = str(tool["plugin_id"]).lower()
            plugin_name = str(tool["plugin_name"]).lower()
            tool_name = str(tool["name"]).lower()
            ref = str(tool["ref"]).lower()
            if f"@{ref}" in text or f"/{ref}" in text:
                score += 100
            if plugin_id in text or plugin_name in text:
                score += 8
            if tool_name in text:
                score += 8
            for trigger in tool["triggers"]:
                if trigger and trigger in text:
                    score += 12
            hint_words = self._words(tool_name + " " + str(tool.get("description") or ""))
            score += min(5, len(words & hint_words))
            if score >= 8 and (best is None or score > best[0]):
                best = (score, tool)
        return dict(best[1]) if best else None

    def maybe_execute(self, message: str) -> dict[str, Any] | None:
        tool = self.match(message)
        if not tool:
            return None
        try:
            result = self.execute(tool, message)
            event("marketplace_plugin_tool_finished", tool=tool["ref"], response_chars=len(result.get("text") or ""))
            return result
        except Exception as exc:
            problem("marketplace_plugin_tool_failed", str(exc), tool=tool["ref"])
            return {
                "ok": False,
                "tool": tool["ref"],
                "plugin_name": tool["plugin_name"],
                "text": f"Инструмент {tool['ref']} завершился ошибкой: {exc}",
                "error": str(exc),
            }

    def execute(self, tool: dict[str, Any], message: str) -> dict[str, Any]:
        endpoint = str(tool["endpoint"])
        field = str(tool.get("input_field") or "query")
        payload = {field: str(message)}
        method = str(tool.get("method") or "POST").upper()
        headers = {"User-Agent": "Leshy-AI/Marketplace-Plugin", "Accept": "application/json, text/plain;q=0.9"}
        if method == "GET":
            separator = "&" if parse.urlparse(endpoint).query else "?"
            url = endpoint + separator + parse.urlencode(payload)
            req = request.Request(url, headers=headers, method="GET")
        elif method == "POST":
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            req = request.Request(endpoint, data=body, headers=headers, method="POST")
        else:
            raise ValueError("Marketplace plugin поддерживает только GET и POST")

        event("marketplace_plugin_tool_started", tool=tool["ref"], endpoint=endpoint)
        with request.urlopen(req, timeout=int(tool.get("timeout") or 20)) as response:
            length = int(response.headers.get("Content-Length") or 0)
            if length and length > MAX_PLUGIN_RESPONSE:
                raise ValueError("Ответ плагина превышает 2 МБ")
            raw = response.read(MAX_PLUGIN_RESPONSE + 1)
            content_type = str(response.headers.get("Content-Type") or "")
        if len(raw) > MAX_PLUGIN_RESPONSE:
            raise ValueError("Ответ плагина превышает 2 МБ")
        text = raw.decode("utf-8", errors="replace")
        if "json" in content_type.lower():
            try:
                obj = json.loads(text)
                text = json.dumps(obj, ensure_ascii=False, indent=2)[:MAX_PLUGIN_RESPONSE]
            except Exception:
                pass
        return {
            "ok": True,
            "tool": tool["ref"],
            "plugin_id": tool["plugin_id"],
            "plugin_name": tool["plugin_name"],
            "text": text,
        }


__all__ = ["MarketplacePluginRuntime"]
