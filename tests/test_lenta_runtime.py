from leshy.lenta_runtime import _explicit_lenta, _parse_explicit_intent, _payment


def test_lenta_capability_intent():
    intent = _parse_explicit_intent("https://lenta.com/ Ты умеешь работать с этим сайтом и заказывать продукты?")
    assert intent is not None
    assert intent.kind == "capability"


def test_lenta_catalog_intent():
    intent = _parse_explicit_intent("Сколько стоит молоко в Ленте?")
    assert intent is not None
    assert intent.kind == "catalog"


def test_lenta_basket_intent():
    intent = _parse_explicit_intent("Собери в Ленте корзину продуктов на неделю")
    assert intent is not None
    assert intent.kind == "basket"


def test_lenta_checkout_intent_has_priority_over_basket_words():
    intent = _parse_explicit_intent("Закажи в Ленте продукты и собери корзину")
    assert intent is not None
    assert intent.kind == "checkout"


def test_lenta_payment_variants():
    assert _payment("картой при получении") == "card_on_delivery"
    assert _payment("оплатить картой онлайн") == "card_online"
    assert _payment("оплата наличными") == "cash"
    assert _payment("дальше я сам оплачу") == "self"


def test_unrelated_ribbon_is_not_lenta_store_intent():
    assert not _explicit_lenta("Добавь красную ленту на изображение")


def test_lenta_telegram_ice_cream_request_is_basket_not_os():
    from leshy.os_control import OperatingSystemController
    message = "https://lenta.com/ Зайди на этот сайт и добавь мне в корзину моего профиля самое дешевое мороженое"
    intent = _parse_explicit_intent(message)
    assert intent is not None
    assert intent.kind == "basket"
    controller = OperatingSystemController.__new__(OperatingSystemController)
    assert not controller.is_request("Выполни задачу. Запрещаю придумывать.")
    assert not controller.is_request(message)
    assert controller.is_request("Выполни команду PowerShell")
    assert controller.is_request("/os status")
