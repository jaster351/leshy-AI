from __future__ import annotations

from . import lenta_runtime as runtime
from .diagnostics import event, problem


_INSTALLED = False
_ORIGINAL_PREPARE_BASKET = None
_ORIGINAL_PREPARE_CHECKOUT = None


def _preference_suffix(app, owner_id: str) -> tuple[str, str]:
    """Вернуть служебные инструкции и оплату по умолчанию для owner_id."""
    try:
        profile = runtime._session(app, owner_id)
        preferences = profile.preferences()
    except Exception as exc:
        problem("lenta_preferences_read_failed", str(exc), owner_id=owner_id)
        return "", ""

    delivery = str(preferences.get("delivery_mode") or "account")
    substitutions = str(preferences.get("substitution_policy") or "automatic")
    payment = str(preferences.get("payment_mode") or "account")

    rules: list[str] = []
    if delivery == "delivery":
        rules.append(
            "Предпочтение владельца: доставка. Если доставка доступна для текущего профиля, используй её; "
            "не меняй сохранённый адрес без явной команды пользователя."
        )
    elif delivery == "pickup":
        rules.append(
            "Предпочтение владельца: самовывоз. Если самовывоз доступен, используй текущий сохранённый магазин; "
            "не меняй город или магазин без явной команды пользователя."
        )
    else:
        rules.append("Не меняй текущий способ получения, сохранённый в личном кабинете, без явной команды пользователя.")

    if substitutions == "ask":
        rules.append(
            "Если нужного товара нет, НЕ заменяй его автоматически: оставь позицию нерешённой, перечисли её в результате "
            "и дождись решения пользователя."
        )
    elif substitutions == "none":
        rules.append("Если нужного товара нет, не заменяй его: пропусти позицию и явно сообщи об этом.")
    else:
        rules.append(
            "Если нужного товара нет, можно подобрать только очевидный близкий аналог и обязательно перечислить замену."
        )

    default_payment = "" if payment == "account" else payment
    return "\n\nСлужебные предпочтения владельца:\n- " + "\n- ".join(rules), default_payment


def install_lenta_preferences_runtime() -> None:
    """Применить настройки профиля к существующему разговорному модулю «Лента»."""
    global _INSTALLED, _ORIGINAL_PREPARE_BASKET, _ORIGINAL_PREPARE_CHECKOUT
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_PREPARE_BASKET = runtime._prepare_basket
    _ORIGINAL_PREPARE_CHECKOUT = runtime._prepare_checkout_from_basket

    def prepare_basket(app, owner_id: str, session_id: str, intent, *, ask_order: bool):
        suffix, default_payment = _preference_suffix(app, owner_id)
        payment = intent.payment or default_payment
        enriched = runtime.LentaIntent(intent.kind, str(intent.text or "") + suffix, payment)
        event(
            "lenta_preferences_applied",
            owner_id=owner_id,
            action=intent.kind,
            default_payment=bool(not intent.payment and default_payment),
        )
        return _ORIGINAL_PREPARE_BASKET(app, owner_id, session_id, enriched, ask_order=ask_order)

    def prepare_checkout_from_basket(app, owner_id: str, session_id: str, intent):
        _suffix, default_payment = _preference_suffix(app, owner_id)
        payment = intent.payment or default_payment
        enriched = runtime.LentaIntent(intent.kind, intent.text, payment)
        return _ORIGINAL_PREPARE_CHECKOUT(app, owner_id, session_id, enriched)

    runtime._prepare_basket = prepare_basket
    runtime._prepare_checkout_from_basket = prepare_checkout_from_basket


__all__ = ["install_lenta_preferences_runtime"]
