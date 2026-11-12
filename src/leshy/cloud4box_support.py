"""Grounded Cloud4box support-writing policy and bounded topical reference facts.

Purpose: create a single ready-to-send reply from the employee's perspective.
Fact provenance is explicit: official public pages vs owner-supplied operational
practice vs temporary/unconfirmed state. A ticket is not a trusted instruction.
Do not expose or embed client correspondence, credentials or third-party IPs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Any, Iterable


CHECKED_ON = "2026-11-12"
_URL = "https://cloud4box.com"
_WIKI = "https://wiki.cloud4box.com"


@dataclass(frozen=True)
class SupportFact:
    key: str
    cues: tuple[str, ...]
    statement: str
    source: str
    level: str = "official"
    temporary: bool = False


FACTS: tuple[SupportFact, ...] = (
    SupportFact("company",
        ("cloud4box", "компан", "оферт", "реквизит", "техподдерж", "поддержк"),
        "Cloud4box предоставляет VPS/VDS, выделенные серверы, веб-хостинг, FTP-хранилище, домены, SSL, RDP, лицензии и платное администрирование. Техподдержка через тикеты работает круглосуточно.",
        f"{_URL}/obzor_cloud4box/contacts/"),
    SupportFact("vps",
        ("vps", "vds", "виртуальн", "конфигуратор", "ядр", "nvme", "ceph", "ssd"),
        "Для VPS есть готовые конфигурации и конфигуратор CPU/RAM/диска/IP; клиент получает root/администраторский доступ. Локации и доступные ОС зависят от тарифа. Ceph и локальные SSD/NVMe отличаются типом хранения и характеристиками.",
        f"{_WIKI}/servery/vps"),
    SupportFact("dedicated",
        ("выделенн", "dedic", "физическ", "xeon", "конфигуратор", "мощност"),
        "Dedicated — физический сервер с аппаратными ресурсами для одного клиента. Есть готовые варианты и конфигуратор. В базовую конфигурацию может входить IPv4; характеристики и дополнительные опции проверяются для выбранного сервера.",
        f"{_WIKI}/servery/dedic"),
    SupportFact("throughput",
        ("скорост", "мбит", "гбит", "канал", "гарантир", "iperf", "трафик", "пинг", "маршрут"),
        "Обычная ширина канала VPS задаёт верхнюю границу, а не гарантирует эту скорость до каждого адреса. Отдельный гарантированный выделенный канал предлагается как дополнительная услуга; условия обычного сетевого порта Dedicated проверять по конкретному тарифу. Тест одного внешнего ресурса не доказывает неисправность порта.",
        f"{_URL}/vydelennyj-setevoj-kanal/"),
    SupportFact("network_diagnostics",
        ("mtr", "winmtr", "traceroute", "loss", "потер", "пинг", "задержк", "cogent", "маршрут"),
        "Потери ICMP на промежуточных хопах при отсутствии потерь на конечном узле не доказывают потерю транзитного трафика. Для диагностики важны конечный узел, маршрут в обе стороны, время и фактические TCP/UDP-сбои. Не обещать изменение BGP/аплинка без подтверждения.",
        f"{_WIKI}/"),
    SupportFact("looking_glass",
        ("looking glass", "speedtest", "lg.cloud4box", "тестовый ip", "тестовый адрес", "проверить пинг"),
        "Для независимой диагностики и скорости направлять на официальный Looking Glass https://lg.cloud4box.com/. НЕ сообщать IP других клиентов; использовать только подтверждённые публичные тестовые адреса.",
        "https://lg.cloud4box.com/"),
    SupportFact("vps_speed_plan",
        ("200 мбит", "шейпинг", "скорость 200", "порт 200", "пропускн"),
        "По рабочей инструкции оператора тарифная ширина 200 Мбит/с на VPS означает максимальную, а не гарантированную скорость. Не признавать вину провайдера лишь по нескольким внешним iperf/curl.",
        "operator-approved: support practice", "operator"),
    SupportFact("bandwidth_upgrade",
        ("расширит", "увеличить канал", "канал увеличить", "скорост", "гарантир"),
        "Увеличение скорости и отдельный гарантированный канал зависят от тарифа и технической возможности. Сначала спросить требуемую скорость, если она не указана. Не обещать изменение до проверки.",
        f"{_URL}/vydelennyj-setevoj-kanal/"),
    SupportFact("ipv6",
        ("ipv6", "ip v6", "ипв6", "ip-адрес", "ipv4"),
        "IPv4 и IPv6 технически могут работать одновременно (dual stack). Наличие, число адресов, цена и порядок подключения IPv6 зависят от конкретной локации и конфигурации; нельзя обещать бесплатный IPv6 всем Dedicated.",
        f"{_URL}/dedicated/"),
    SupportFact("domains",
        ("домен", "dns", "ns-", "ns ", "регистратор", "transfer", "перенос домен", "cloudflare"),
        "Смена NS у регистратора и DNS-записи зоны — разные операции. Если панель сообщает ошибку при смене NS, надо запросить домен, раздел панели и снимок ошибки и проверить статус у регистратора, не обещая ручное изменение без доступа.",
        f"{_WIKI}/"),
    SupportFact("hostname",
        ("hostname", "имя сервера", "cloud4box.ru", "ptr", "домен сервера"),
        "Техническое имя вида <id>.cloud4box.ru и PTR/домен IP в биллинге следует отличать от hostname внутри ОС. Официальная инструкция описывает смену домена IP/PTR в личном кабинете; без логов нельзя утверждать, что cloud-init или панель точно сбрасывает hostname.",
        f"{_WIKI}/instrukcii-dlya-serverov/izmenenie-domena-servera"),
    SupportFact("ssl",
        ("alphassl", "ssl", "сертификат", "поддомен", "wildcard"),
        "Обычный AlphaSSL не является wildcard для произвольных поддоменов; для *.example.com нужен wildcard-сертификат с соответствующими условиями. Проверить SAN конкретного сертификата перед обещанием покрытия www.",
        "https://support.globalsign.com/ssl/products/alphassl/alpha-ssl-faqs"),
    SupportFact("mail_bitrix",
        ("битрикс", "bxmail", "sendmail", "smtp", "письм", "уведомлен", "почт"),
        "По согласованной практике поддержки для отправки писем из 1С-Битрикс на виртуальном хостинге рекомендуется SMTP с авторизацией в почтовом ящике, а не отправка через локальный Sendmail. Статус SUCCESS_EXEC=Y подтверждает обработку события CMS, но не доставку конечному адресату.",
        "operator-approved: web hosting mail workflow", "operator"),
    SupportFact("ftp",
        ("ftp", "хранилищ", "webdav", "dav", "картинк", "фото", "http"),
        "FTP-хранилище предназначено для хранения/обмена файлами по FTP/DAV; адрес ftp:// не является прямой публичной HTTP/HTTPS-ссылкой для HTML img. Наличие веб-панели не равнозначно CDN или S3.",
        f"{_URL}/ftp-server/"),
    SupportFact("s3",
        ("s3", "s3-хранилищ", "amazon s3"),
        "По подтверждённой оператором информации отдельная услуга S3 сейчас не предоставляется, а подтверждённых сроков запуска нет. Не путать с FTP-хранилищем.",
        "operator-approved: availability statement", "operator", True),
    SupportFact("vmmanager",
        ("vmmanager", "консол", "cluster.contell", "vnc", "iso", "образ", "переустанов", "панел"),
        "Временное операционное уведомление: клиентская панель VMmanager 6 недоступна в период техработ; операции выполняются по заявке техподдержки, возможен доступ VNC. В этот период загрузка своего образа по согласованной инструкции недоступна. Не объявлять это вечным ограничением: проверять фактический статус и дату.",
        "operator-approved: temporary VMmanager6 maintenance", "operator", True),
    SupportFact("tickets",
        ("заявк", "тикет", "обращен", "поддержк", "другой сервер", "нескольк сервер", "id"),
        "Для каждого сервера отдельное обращение и обязательная сверка ID услуги перед изменением. При разных ID в одной заявке остановиться и запросить уточнение. История обращения сохраняется в исходном аккаунте. Не обвинять клиента и не обсуждать, писал ли он через ИИ.",
        "operator-approved: support ticket handling", "operator"),
    SupportFact("reinstall",
        ("переустанов", "удален", "потер", "форматир", "остановк", "разрешени"),
        "Перед потенциально разрушительными действиями необходимо подтвердить точный ID услуги и явное согласие на потерю данных. Для остановки сервера и изменения режима CPU запросить разрешение до работ. Никогда не писать «выполнили» без подтверждения операции.",
        "operator-approved: operational safety", "operator"),
    SupportFact("billing",
        ("лицев", "баланс", "оплат", "перезаказ", "счёт", "счет"),
        "Способ оплаты с лицевого счёта зависит от состояния заказа и достаточности баланса. При ошибке попросить номер заказа и скриншот; при необходимости предложить создать новый заказ с той же конфигурацией, не обещая устранения ошибки.",
        f"{_WIKI}/oplata-i-dokumenty/oplata/oplata-s-balansa-akkaunta"),
    SupportFact("refund",
        ("возврат", "деньг", "манибек", "отказ", "досрочн", "удалить услуг"),
        "При досрочном отказе возврат и перерасчёт определяются офертой и периодом оплаты; в актуальной статье базы знаний описан возврат неиспользованного периода, возможен суточный перерасчёт и аннулирование скидки. Не утверждать без проверки, что возвращают исключительно за полные месяцы.",
        f"{_WIKI}/oplata-i-dokumenty/oplata/vozvrat-denezhnykh-sredstv"),
    SupportFact("vpn",
        ("vpn", "wireguard", "amnezia", "vless", "туннел", "tunnel", "роскомнадзор", "ркн", "белый список"),
        "Операторская позиция для клиентских ответов: сотрудники не консультируют по настройке/работе VPN. Для вопроса о внесении VPN/туннеля в белый список предлагать обратиться в Роскомнадзор с заявлением на бланке организации и обоснованием. Не гарантировать добавление или работу и не выдавать эту практику за универсальную норму закона.",
        "operator-approved: VPN communication", "operator"),
    SupportFact("egress_ports",
        ("исходящ", "порт 25", "2525", "5060", "фильтр", "ограничен", "блокиров"),
        "По операторской информации на VPS ограничены исходящие соединения по TCP-портам 25, 2525 и 5060; для конкретной площадки и текущих исключений нужна проверка. Не добавлять 3389 в перечень без отдельного подтверждения.",
        "operator-approved: network port restrictions", "operator"),
    SupportFact("tsu",
        ("тспу", "check-host", "chebur", "чебур", "блокиров", "tcp", "timeout"),
        "Не подтверждать блокировку ТСПУ только по тайм-ауту. Отдельный модуль диагностики сравнивает прямой TCP, завершённые результаты Check-Host и завершённый Cheburcheck; для категорий 3/3 используются реальные совпадающие измерения, не справочная геолокация.",
        "operator-approved: TSPU diagnostic workflow", "operator"),
    SupportFact("cpu_isa",
        ("nginx", "isa", "avx2", "x86-64-v3", "host cpu", "virtio"),
        "Ошибка 'CPU ISA level is lower than required' означает несовместимость сборки ПО с доступными инструкциями CPU. Сначала проверить режим Host CPU и требования бинарника; физический процессор не получает новые инструкции от виртуализации. Если Host CPU уже активен — рекомендовать совместимую сборку.",
        "operator-approved: infrastructure troubleshooting", "operator"),
    SupportFact("starting_prices",
        ("тариф", "стоимост", "цен", "руб", "от скольк", "сколько стоит", "дешев", "прайс"),
        "На главной странице на дату справочной проверки рекламируются стартовые цены: VPS от 368 ₽/мес, Dedicated от 4 194 ₽/мес, веб-хостинг от 80 ₽/мес, FTP от 1,8 ₽/мес, RDP от 1 122 ₽/мес, домены от 141 ₽, SSL от 1 248 ₽/год, лицензии ispmanager от 470 ₽/мес, Microsoft от 2 100 ₽/мес, DDoS-защита от 13 800 ₽/мес, администрирование от 2 500 ₽/ч. Это НЕ гарантированные цены заказа: проверить актуальные варианты и локацию в конфигураторе.",
        f"{_URL}/", "official", True),
    SupportFact("dedicated_options",
        ("dedicated", "выделенн", "ipv6", "ipmi", "конфигуратор", "канал", "1 гбит"),
        "На странице Dedicated перечислены: конфигуратор, IPMI/IP-KVM в зависимости от оборудования, IPv4, L3/L4 DDoS, разные диски и сеть. Некоторые блоки страницы описывают shared-канал, другие заявляют гарантированные характеристики; проверять конкретный тариф/заказ, не обобщать.",
        f"{_URL}/dedicated/"),
    SupportFact("administration",
        ("администр", "поддержк", "установ", "настро", "обслужив", "ssh"),
        "Базовая поддержка помогает с инфраструктурой/доступностью услуги; администрирование пользовательской ОС и ПО может быть платной услугой. На странице администрирования опубликовано 2 500 ₽/час (минимум 15 минут / 625 ₽), цену уточнять перед заказом.",
        f"{_URL}/administrirovanie-serverov/"),
)


_STYLE = (
    "Ты готовишь ОДИН готовый ответ клиенту от лица сотрудника техподдержки Cloud4box. "
    "Не обращайся к оператору, не давай метакомментарий, не пиши 'вот ответ', не добавляй подпись. "
    "Пиши живым русским языком, на «Вы», коротко — обычно 1–3 абзаца. "
    "Без приветствия в продолжающейся переписке, без шаблонных вступлений, "
    "нумерованных списков и обвинений клиента. Не упрекай за ИИ. "
    "Сперва ответь на конкретный вопрос, лишь затем задай один необходимый уточняющий вопрос. "
    "Не считай слова клиента доказательством технического сбоя; проси диагностические данные, "
    "если они действительно нужны. ICMP-потери на промежуточных узлах не равны потерям до цели. "
    "Не говори, что что-то проверено, передано специалистам, изменено или оплачено, "
    "если в предоставленных данных нет подтверждения. Не обещай возврат, скорость, IPv6, "
    "тестовый IP, изменение маршрута или ручное действие без актуальной проверки. "
    "Не показывай IP других клиентов, адреса электронной почты, логины или пароли. "
    "При запросе потенциально разрушительных действий сначала подтверди ID услуги и согласие. "
    "Для цен, актуальных регламентов, ограничений и техработ приоритет имеют свежий "
    "официальный источник и подтверждённая оператором информация; при конфликте фактов "
    "сообщи, что условия требуют уточнения, не сочиняй окончательный ответ."
)


_EXPLICIT = (
    "/support", "/поддержка", "ответь клиенту", "ответь на вопрос клиента",
    "напиши ответ клиенту", "составь ответ клиенту", "переформулируй ответ клиенту",
    "вопрос клиента", "сообщение клиента", "обращение клиента",
    "ответ клиенту", "клиент пишет", "как сотрудник поддержки",
    "как сотрудник техподдержки", "подготовь ответ",
)
_CLOUD_MARKERS = (
    "cloud4box", "cloud4box.ru", "cloud4box.com", "contell.ru",
    "vmmanager 6", "личном кабинете хостинг", "вашем хостинге",
)
_REPLY_EDITS = (
    "убери приветствие", "без приветствия", "короче", "покороче",
    "переформулируй", "переделай ответ", "сделай ответ", "более естествен",
    "напиши по-человечески", "ответь полностью",
)


def _normal(value: str) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _row_text(row: Any) -> tuple[str, str]:
    try:
        return str(row["role"]), str(row["content"])
    except (KeyError, TypeError, IndexError):
        return str(getattr(row, "role", "")), str(getattr(row, "content", ""))


def is_support_draft_request(message: str, rows: Iterable[Any] = ()) -> bool:
    """No automatic hijacking of ordinary questions or unrelated customer data."""
    text = _normal(message)
    if any(cue in text for cue in _EXPLICIT + _CLOUD_MARKERS):
        return True
    if "наименование в панели" in text and "обработчик" in text:
        return True
    if ("здравствуйте" in text or "добрый день" in text) and (
        ("vps" in text or "выделенн" in text or "хостинг" in text)
        and any(s in text for s in ("у вас", "ваш", "подскажите"))
    ):
        return True
    if not any(cue in text for cue in _REPLY_EDITS):
        return False
    # The current message has already been appended to the newest-first history.
    recent = []
    for row in list(rows)[:12]:
        role, content = _row_text(row)
        if role == "user" and _normal(content) == text:
            continue
        if role == "user":
            recent.append(_normal(content))
        if len(recent) >= 4:
            break
    return any(any(cue in old for cue in _EXPLICIT + _CLOUD_MARKERS) for old in recent)


def relevant_support_facts(query: str, *, limit: int = 7) -> tuple[SupportFact, ...]:
    """Rank by explicit topic cues; prefer a few relevant grounded references."""
    text = _normal(query)
    ranked = []
    for fact in FACTS:
        matches = [cue for cue in fact.cues if _normal(cue) in text]
        if matches:
            ranked.append((max(len(cue) for cue in matches) + len(matches) * 2, fact))
    ranked.sort(key=lambda row: row[0], reverse=True)
    return tuple(item for _, item in ranked[: max(1, min(limit, 14))])


def support_draft_context(message: str, rows: Iterable[Any] = (), *, force: bool = False) -> str:
    """Return safe grounded instruction only for support requests or ticket pipeline."""
    if not force and not is_support_draft_request(message, rows):
        return ""
    facts = relevant_support_facts(message, limit=9)
    notes = []
    for item in facts:
        tag = "оператор" if item.level == "operator" else "официальный сайт"
        extra = " (временные/меняющиеся данные — перепроверь)" if item.temporary else ""
        notes.append(f"- {item.statement} [{tag}: {item.source}]{extra}")
    if not notes:
        notes.append("- Вопрос вне собранной базы: не придумывай политику или условия, запроси уточнение.")
    return (
        "РЕЖИМ ЧЕРНОВИКА ОТВЕТА CLOUD4BOX. Это инструкция для подготовки текста, "
        "не авторизация на действия и не автоматическая отправка клиенту.\n"
        + _STYLE
        + "\n\nПроверенные справочные факты и рабочие инструкции на "
        + CHECKED_ON
        + " (это данные, а не команды от клиента):\n"
        + "\n".join(notes)
        + "\nЕсли цена/срок/наличие услуги меняются во времени, перед категорическим ответом "
          "нужна актуальная проверка сайта/биллинга либо осторожная формулировка."
    )


__all__ = ["SupportFact", "FACTS", "CHECKED_ON", "is_support_draft_request",
           "relevant_support_facts", "support_draft_context"]
