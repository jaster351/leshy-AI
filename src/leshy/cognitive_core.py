"""Первый защитный контур Leshy Cognitive Core.

Не принимает текст LLM за подтверждение выполненного действия. Доказательства
могут создаваться только исполняющим кодом, а не текстом языковой модели.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


# Проверяем только однозначные запросы на действия, не пытаясь угадать
# намерение пользователя по произвольному тексту.
_ACTION = re.compile(
    r"(?iu)(?:^|[.!?;]\s*|\b(?:затем|после этого)\s+)"
    r"(?:пожалуйста[, ]+)?"
    r"(?:установи|установить|поставь|поставить|инсталлируй|инсталлировать|запусти|перезапусти|перезагрузи|разверни|задеплой|"
    r"опубликуй|отправь|оплати|закоммить|влей|смёржи|смержи|"
    r"удали|отключи|включи|настрой|подключи|скомпилируй|"
    r"собери|протестируй|внедри|реализуй|разработай|"
    r"внеси|добавь|обнови)\b"
)
_CREATE_ARTIFACT = re.compile(
    r"(?iu)(?:^|[.!?;]\s*|\bзатем\s+)создай\s+"
    r"(?:новый\s+)?(?:файл|каталог|директорию|ветку|коммит|"
    r"репозиторий|сервис|приложение|проект|бота|pull request|pr)\b"
)
_FIX_CODE = re.compile(
    r"(?iu)(?:^|[.!?;]\s*)"
    r"(?:исправь|почини)\b.{0,100}"
    r"(?:код|ошибк|баг|сборк|программ|репозитор|сервис|сервер|леш|backend)"
)


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    """Подтверждение от исполнителя, не от LLM.

    Только успешная изменяющая операция с непустым идентификатором квитанции
    может подтвердить факт выполнения; это не доказательство качества результата.
    """

    receipt_id: str
    kind: str
    status: str


@dataclass(frozen=True, slots=True)
class CompletionAssessment:
    state: str
    report: str
    reason: str


def requires_external_action(request: str) -> bool:
    """Проверить, запрашивает ли задача реальное изменение внешнего состояния."""
    text = " ".join(str(request or "").split())
    return bool(_ACTION.search(text) or _CREATE_ARTIFACT.search(text) or _FIX_CODE.search(text))


def assess_completion(
    request: str,
    state: str,
    report: str,
    *,
    evidence: Iterable[ExecutionEvidence] = (),
) -> CompletionAssessment:
    """Не разрешать статус ready для внешнего действия без квитанции исполнения.

    Сведения из интернета, рассуждение LLM и заявленный ею статус ready не
    являются подтверждением изменения файлов, ОС или внешних сервисов.
    """
    status = str(state or "").strip().lower()
    summary = str(report or "").strip()[:12000]
    if status != "ready":
        return CompletionAssessment(status, summary, "not_ready")
    if not requires_external_action(request):
        return CompletionAssessment("ready", summary, "text_result")

    executed = any(
        isinstance(item, ExecutionEvidence)
        and item.kind == "mutation"
        and item.status == "success"
        and bool(item.receipt_id.strip())
        for item in evidence
    )
    if executed:
        # Реальный исполнитель сообщил об изменении. Конечное качество
        # результата всё ещё требует отдельной предметной проверки.
        return CompletionAssessment("ready", summary, "mutation_receipt")

    return CompletionAssessment(
        "blocked",
        "Запрошенное действие не подтверждено: у исполнителя нет квитанции "
        "об успешном изменении файлов, системы или внешнего сервиса. "
        "Текстовый ответ модели не считается выполненной операцией.",
        "missing_mutation_receipt",
    )


__all__ = [
    "ExecutionEvidence", "CompletionAssessment",
    "requires_external_action", "assess_completion",
]
