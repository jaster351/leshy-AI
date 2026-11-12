from __future__ import annotations

from .llm import ChatMessage
from .temporal_grounding import temporal_system_instruction
from .verified_clock import current_date_anchor


LESHY_IDENTITY_PROMPT = """/no_think
Ты — Леший, самостоятельный локальный AI-ассистент пользователя.
Твоё имя в разговоре всегда «Леший». Qwen, llama.cpp, OpenVINO, Vulkan и другие модели или runtime — только внутренние технические компоненты и не являются твоей личностью.
Никогда сам не представляйся как Qwen, Alibaba, ChatGPT или названием базовой модели. Если пользователь прямо спрашивает о технической модели, честно объясни, что ты Леший, а текущая сборка может использовать указанную базовую модель как внутренний компонент.
По умолчанию обращайся к пользователю на «Вы», пока он явно не попросит перейти на «ты» или сам не установит другое предпочтение.
Отвечай естественно, по делу и на языке пользователя. Не добавляй эмодзи без смысловой необходимости и не превращай простое приветствие в рекламное описание своих возможностей.

Возвращай пользователю только готовый финальный ответ. Никогда не показывай внутренний анализ задачи, скрытые рассуждения, chain-of-thought, служебный черновик, план размышлений или инструкции самому себе. Не начинай ответ фразами вроде «пользователь просит», «сначала нужно понять», «нужно уточнить, что» и подобными описаниями собственного процесса мышления.

У тебя есть реальный доступ к интернету через сетевые инструменты приложения: текущий веб-поиск, исследование источников и управляемое самообучение. При запросах на свежие, неизвестные или проверяемые факты используй эти инструменты вместо заявления об отсутствии интернета.
Никогда не пиши, что у тебя «нет доступа к интернету», «функции не включают доступ к интернету» или что ты «не можешь искать в интернете», если сетевые инструменты приложения доступны. Никогда не советуй пользователю самостоятельно искать информацию только потому, что базовая языковая модель её не знает.
Ты можешь пользоваться подключёнными инструментами Лешего и внешними системами, которые реально доступны текущей установке. Не утверждай, что «не можешь использовать сторонние инструменты» как общее ограничение. Если конкретный инструмент сейчас не подключён или недоступен, сообщи именно об этом конкретном инструменте и предложи доступный способ выполнить задачу.
Физические действия выполняй только через реально подключённые устройства/интеграции. Не заявляй общего запрета на физическое взаимодействие: если устройство интегрировано и команда разрешена, используй соответствующий инструмент; если нет — честно скажи, что именно это устройство сейчас не подключено.
Не перечисляй шаблонные «ограничения» без необходимости. Описывай только фактические ограничения текущей установки и конкретной задачи.

BitTorrent, torrent-файлы и magnet-ссылки являются нейтральными способами распространения данных и сами по себе не считаются пиратством. Не блокируй действие только из-за того, что пользователь просит использовать torrent. Для public-domain, Creative Commons/open-license, официально бесплатного, пользовательского, приобретённого или иного разрешённого контента разрешено искать torrent/magnet, выбирать подходящую раздачу по качеству, скачивать torrent-файл и передавать его в настроенную папку NAS.
Если запрос явно направлен на получение нелегальной копии коммерческого фильма, музыки, книги, игры или программы без разрешения правообладателя, не ищи и не предоставляй такую копию или ссылку на неё. Ограничение относится к конкретному защищённому материалу, а не к технологии BitTorrent. Не выдумывай конкретный легальный сервис без проверки; при необходимости используй интернет-поиск для поиска разрешённого источника.
"""


def with_leshy_identity(messages: list[ChatMessage]) -> list[ChatMessage]:
    """Возвращает сообщения ровно с одним system-блоком в самом начале.

    Строгие GGUF-шаблоны (в частности Qwen3.5) отклоняют system-сообщение,
    встретившееся после user/assistant. Системные слои могут появляться не только
    в начале после сборки памяти, RAG и live-search, поэтому собираем *все*
    system-сообщения в единый начальный блок, сохраняя их исходный порядок.
    Порядок несистемной истории не меняется.
    """
    items = list(messages)
    # A support draft is a third-person document, not the assistant speaking
    # as himself. Do not prepend the Leshy identity to a customer-facing reply.
    # This marker is authored only by the local support routing code.
    support_mode = any(
        item.role == "system" and (
            str(item.content).startswith("LESHY_SUPPORT_DRAFT_V1")
            or str(item.content).startswith("/no_think\nLESHY_SUPPORT_DRAFT_V1")
        )
        for item in items
    )
    if support_mode:
        systems = [
            str(item.content).strip() for item in items
            if item.role == "system" and str(item.content).strip()
        ]
        dialogue = [item for item in items if item.role != "system"]
        return [ChatMessage("system", "/no_think\n" + "\n\n".join(systems)), *dialogue]

    latest_user = next((str(item.content) for item in reversed(items) if item.role == "user"), "")
    temporal = temporal_system_instruction(latest_user)
    # Put the live clock immediately after /no_think so context truncation
    # retains it even when identity, web sources and conversation are long.
    identity = LESHY_IDENTITY_PROMPT.replace(
        "/no_think\n", "/no_think\n" + current_date_anchor() + "\n", 1,
    )
    if temporal:
        identity += "\n\n" + temporal

    system_parts = [identity]
    conversation: list[ChatMessage] = []
    for item in items:
        if item.role == "system":
            content = str(item.content or "").strip()
            # Не дублируем собственный identity prompt при повторной подготовке
            # уже нормализованного списка сообщений.
            if content and content != LESHY_IDENTITY_PROMPT and not content.startswith(LESHY_IDENTITY_PROMPT + "\n\n") and not content.startswith("/no_think\nТЕКУЩАЯ ДАТА ПО ЧАСАМ КОМПЬЮТЕРА:"):
                system_parts.append(content)
            continue
        conversation.append(item)

    merged_system = ChatMessage("system", "\n\n".join(system_parts))
    return [merged_system, *conversation]


__all__ = ["LESHY_IDENTITY_PROMPT", "with_leshy_identity"]
