from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any
from urllib import parse, request

from .diagnostics import event, problem


_BASE_URL = "https://integration.api.lenta.com"
_USER_AGENT = "Leshy-AI/lenta-module"


@dataclass(slots=True)
class LentaCandidate:
    id: str
    name: str
    price: float | None
    unit: str
    available: bool | None
    raw: dict[str, Any]


class LentaModule:
    """Отдельный модуль Лешего для поиска товаров, цен и подготовки корзины Ленты.

    Открытый Integration API используется только для чтения каталога, цен,
    наличия и магазинов. Действия в личном кабинете и оформление заказа должны
    выполняться браузерным контуром пользователя, а необратимые действия — только
    после явного подтверждения пользователя.
    """

    name = "lenta"
    title = "Лента"

    def __init__(self, brain_dir: Path | str):
        self.brain_dir = Path(brain_dir)
        self.state_path = self.brain_dir / "settings" / "lenta.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _json_get(path: str, params: dict[str, Any] | None = None, timeout: float = 20.0) -> Any:
        query = parse.urlencode(params or {}, doseq=True)
        url = _BASE_URL + path + (("?" + query) if query else "")
        req = request.Request(url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT})
        with request.urlopen(req, timeout=timeout) as response:
            data = response.read()
        return json.loads(data.decode("utf-8"))

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "enabled": True,
            "api": _BASE_URL,
            "authentication": "не требуется",
            "supports": {
                "stores": True,
                "catalog": True,
                "prices": True,
                "availability": True,
                "basket_planning": True,
                "account_checkout": "browser",
                "final_order_requires_user_confirmation": True,
            },
        }

    def stores(self, *, retail_brand: str = "lo") -> list[dict[str, Any]]:
        result = self._json_get("/v1/stores", {"retailBrand": retail_brand})
        if isinstance(result, list):
            return [x for x in result if isinstance(x, dict)]
        for key in ("stores", "items", "hubs"):
            value = result.get(key) if isinstance(result, dict) else None
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return []

    def nearest(self, latitude: float, longitude: float, *, retail_brand: str = "lo") -> list[dict[str, Any]]:
        result = self._json_get(
            "/v1/stores/nearest/hub",
            {"latitude": latitude, "longitude": longitude, "retailBrand": retail_brand},
        )
        hubs = result.get("hubs") if isinstance(result, dict) else None
        return [x for x in hubs if isinstance(x, dict)] if isinstance(hubs, list) else []

    @staticmethod
    def _price_from(item: dict[str, Any]) -> float | None:
        candidates: list[Any] = []
        for key in ("price", "currentPrice", "regularPrice", "salePrice", "finalPrice"):
            if key in item:
                candidates.append(item.get(key))
        pricing = item.get("prices") or item.get("pricing")
        if isinstance(pricing, dict):
            for key in ("price", "current", "regular", "sale", "value"):
                candidates.append(pricing.get(key))
        for value in candidates:
            if isinstance(value, dict):
                value = value.get("value") or value.get("amount")
            try:
                if value is not None:
                    return float(value)
            except (TypeError, ValueError):
                continue
        return None

    @staticmethod
    def _name_from(item: dict[str, Any]) -> str:
        for key in ("name", "title", "displayName", "productName"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.split())
        return "Товар"

    @staticmethod
    def _id_from(item: dict[str, Any]) -> str:
        for key in ("id", "itemId", "sku", "skuId", "productId"):
            value = item.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
        return ""

    @staticmethod
    def _available_from(item: dict[str, Any]) -> bool | None:
        for key in ("available", "isAvailable", "inStock"):
            if key in item:
                return bool(item.get(key))
        for key in ("stock", "quantity", "availableQuantity"):
            value = item.get(key)
            try:
                return float(value) > 0
            except (TypeError, ValueError):
                pass
        return None

    def search(self, query: str, *, store_id: int | str, channel: str = "lo", limit: int = 10, offset: int = 0, retail_brand: str = "lo") -> list[LentaCandidate]:
        query = " ".join(str(query or "").split()).strip()
        if not query:
            raise ValueError("Пустой запрос товара")
        if channel not in {"lo", "cc", "locc", "utk", "b2b", "ozn"}:
            raise ValueError("Неизвестный канал Ленты")
        result = self._json_get(
            "/catalog/v1/items",
            {
                "stores": str(store_id),
                "channel": channel,
                "retailBrand": retail_brand,
                "query": query,
                "limit": max(1, min(int(limit), 50)),
                "offset": max(0, int(offset)),
            },
        )
        if isinstance(result, list):
            items = result
        elif isinstance(result, dict):
            items = []
            for key in ("items", "products", "results", "data"):
                value = result.get(key)
                if isinstance(value, list):
                    items = value
                    break
                if isinstance(value, dict) and isinstance(value.get("items"), list):
                    items = value["items"]
                    break
        else:
            items = []
        rows: list[LentaCandidate] = []
        for raw in items:
            if not isinstance(raw, dict):
                continue
            rows.append(
                LentaCandidate(
                    id=self._id_from(raw),
                    name=self._name_from(raw),
                    price=self._price_from(raw),
                    unit=str(raw.get("unit") or raw.get("measure") or "шт."),
                    available=self._available_from(raw),
                    raw=raw,
                )
            )
        event("lenta_catalog_search", query=query[:300], store_id=str(store_id), channel=channel, results=len(rows))
        return rows

    def product(self, item_id: str, *, store_id: int | str, channel: str = "lo", retail_brand: str = "lo") -> dict[str, Any]:
        return self._json_get(
            "/catalog/v1/items/" + parse.quote(str(item_id), safe=""),
            {"stores": str(store_id), "channel": channel, "retailBrand": retail_brand},
        )

    @staticmethod
    def plan_basket(request_text: str, candidates: dict[str, list[LentaCandidate]]) -> dict[str, Any]:
        """Сформировать детерминированный черновик корзины из уже найденных товаров.

        Модуль не выдумывает отсутствующие позиции: для каждой категории выбирается
        первый доступный кандидат с известной ценой, иначе позиция остаётся unresolved.
        """
        selected: list[dict[str, Any]] = []
        unresolved: list[str] = []
        total = 0.0
        for wanted, options in candidates.items():
            choice = next((x for x in options if x.available is not False and x.price is not None), None)
            if choice is None:
                unresolved.append(wanted)
                continue
            selected.append({"request": wanted, "item_id": choice.id, "name": choice.name, "price": choice.price, "quantity": 1})
            total += float(choice.price or 0.0)
        return {
            "request": " ".join(str(request_text or "").split()).strip(),
            "items": selected,
            "unresolved": unresolved,
            "estimated_total": round(total, 2),
            "currency": "RUB",
            "final_total_may_change": True,
        }

    @staticmethod
    def parse_simple_basket_request(text: str) -> list[str]:
        value = " ".join(str(text or "").split()).strip()
        value = re.sub(r"(?i)\b(спланируй|спроектируй|собери|составь|корзину|в ленте|лента)\b", " ", value)
        parts = [" ".join(x.split()).strip(" .,:;-") for x in re.split(r"[,;\n]+|\s+и\s+", value)]
        return [x for x in parts if len(x) >= 2]


__all__ = ["LentaModule", "LentaCandidate"]
