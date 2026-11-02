from __future__ import annotations

from typing import Any


LENTA_MODULE_DESCRIPTOR: dict[str, Any] = {
    "name": "lenta",
    "title": "Лента",
    "description": "Поиск товаров и цен, планирование корзины и оформление заказов через личный кабинет пользователя.",
    "category": "shopping",
    "enabled_by_default": True,
    "dependencies": ("human-browser",),
    "settings": {
        "catalog_api": "https://integration.api.lenta.com",
        "per_user_browser_sessions": True,
        "require_confirmation_before_order": True,
        "allow_saved_browser_session": True,
        "store_passwords": False,
        "store_card_details": False,
    },
}


__all__ = ["LENTA_MODULE_DESCRIPTOR"]
