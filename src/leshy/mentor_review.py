"""Оценки наставника, привязанные к ответам, и осторожная часовая планка.

Отчёт содержит данные, а не команды. Только явно утверждённая оценка
опубликованных ответов может менять локальную планку.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_ID = re.compile(r"mentor-batch-[a-z0-9-]{1,55}")
_QID = re.compile(r"q[0-9]{1,6}")
_HASH = re.compile(r"[0-9a-f]{64}")
_TAG = re.compile(r"[A-Za-z0-9_.-]{1,80}")
CAUSES = {"question", "rubric", "knowledge", "context", "code", "model",
          "resources", "transport", "unknown"}
CRITERIA = ("factuality", "constraints", "meaning")
INITIAL_QUOTA = 30
MAX_PART = 256  # транспортная часть; следующие части имеют отдельные ID


def apply_review(root, config, *, read_obj, read_bytes, atomic, digest, now):
    root = Path(root)
    state_path = root / "load-policy.json"
    state = read_obj(state_path) if state_path.exists() else {
        "quota": INITIAL_QUOTA, "maximum_confirmed": 0, "applied": [],
        "last_review": None, "decision": "baseline", "investigation": [],
    }
    quota = state.get("quota")
    if type(quota) is not int or not 1 <= quota <= MAX_PART:
        raise ValueError("Некорректная сохранённая планка")
    expected = config.get("approved_review_sha256", "")
    if not expected:
        return state
    if not isinstance(expected, str) or not _HASH.fullmatch(expected):
        raise ValueError("Некорректный SHA-256 оценки")
    applied = state.get("applied")
    if not isinstance(applied, list) or any(not isinstance(x, str) or not _HASH.fullmatch(x) for x in applied):
        raise ValueError("Повреждён журнал оценок")
    if expected in applied:
        return state
    if len(applied) >= 4096:
        raise ValueError("Требуется архивирование журнала оценок")
    raw = read_bytes(root / "review.json", 256 * 1024)
    if digest(raw) != expected:
        raise PermissionError("Оценка не совпадает с локальным разрешением")
    obj = json.loads(raw)
    if not isinstance(obj, dict) or obj.get("schema") != "ai-mentor.evaluation.v1" or obj.get("source") != "AI-mentor":
        raise ValueError("Некорректная оценка")
    bid, bh = obj.get("batch_id"), obj.get("batch_sha256")
    benchmark, rubric = obj.get("benchmark_id"), obj.get("rubric_id")
    if (not isinstance(bid, str) or not _ID.fullmatch(bid)
            or not isinstance(bh, str) or not _HASH.fullmatch(bh)
            or not isinstance(benchmark, str) or not _TAG.fullmatch(benchmark)
            or not isinstance(rubric, str) or not _TAG.fullmatch(rubric)):
        raise ValueError("Некорректная идентификация оценки")
    manifest = read_obj(root / (bid + ".manifest.json"))
    if (manifest.get("batch_id") != bid or manifest.get("batch_sha256") != bh
            or manifest.get("repository") != config.get("repository")):
        raise PermissionError("Оценка не относится к принятому пакету")
    qids = manifest.get("question_ids")
    if (not isinstance(qids, list) or not 1 <= len(qids) <= MAX_PART
            or any(not isinstance(q, str) or not _QID.fullmatch(q) for q in qids)
            or len(set(qids)) != len(qids)):
        raise ValueError("Некорректный манифест")
    rows = obj.get("scores")
    if not isinstance(rows, list) or not 1 <= len(rows) <= len(qids):
        raise ValueError("Нет допустимых оценок")
    seen, issues, models, revisions = set(), [], set(), set()
    total = 0
    reactions = []
    reference_total = 0
    reference_count = 0
    reference_model = obj.get("reference_model")
    if reference_model is not None and (not isinstance(reference_model, str)
            or not _TAG.fullmatch(reference_model)):
        raise ValueError("Некорректная идентификация модели сравнения")
    critical = False
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Некорректная строка оценки")
        qid = row.get("id")
        if not isinstance(qid, str) or qid not in qids or qid in seen:
            raise ValueError("Чужой или повторный вопрос")
        seen.add(qid)
        rid = bid + "-" + qid
        a = read_obj(root / (rid + ".json"))
        marker = read_obj(root / (rid + ".published.json"))
        answer = a.get("answer")
        if (a.get("request_id") != rid or a.get("batch_sha256") != bh
                or a.get("status") != "answered" or a.get("source") != "leshy_model"
                or not isinstance(answer, str) or not answer
                or marker.get("request_id") != rid
                or marker.get("answer_sha256") != digest(answer.encode("utf-8"))
                or row.get("answer_sha256") != marker.get("answer_sha256")):
            raise PermissionError("Оценка не привязана к опубликованному ответу")
        score = row.get("scores")
        if (not isinstance(score, dict) or set(score) != set(CRITERIA)
                or any(type(score[k]) is not int or not 0 <= score[k] <= 2 for k in CRITERIA)):
            raise ValueError("Оценки должны быть целыми числами 0–2")
        # Сравнение по тем же критериям, а не по сходству текста ответов.
        if "reference_scores" in row or "reference_answer_sha256" in row:
            rs = row.get("reference_scores")
            rh = row.get("reference_answer_sha256")
            if (not reference_model or not isinstance(rh, str) or not _HASH.fullmatch(rh)
                    or not isinstance(rs, dict) or set(rs) != set(CRITERIA)
                    or any(type(rs[k]) is not int or not 0 <= rs[k] <= 2 for k in CRITERIA)):
                raise ValueError("Некорректная оценка ответа модели сравнения")
            reference_total += sum(rs.values())
            reference_count += 1
        flag = row.get("critical")
        if type(flag) is not bool:
            raise ValueError("Не указан признак критической ошибки")
        critical = critical or flag
        cause = row.get("cause")
        if cause not in CAUSES | {"none"}:
            raise ValueError("Неизвестный класс причины")
        if flag or any(score[k] < 2 for k in CRITERIA):
            if cause == "none":
                cause = "unknown"
            issues.append({"id": qid, "cause": cause, "critical": flag,
                           "status": "requires_investigation"})
        # Резервный сигнал наставника не подменяет объективные критерии.
        reaction = row.get("reaction")
        if "reaction" in row:
            if not isinstance(reaction, str) or reaction not in {"like", "dislike"}:
                raise ValueError("Некорректная реакция наставника")
            if reaction == "dislike" and cause == "none":
                raise ValueError("Для дизлайка требуется причина или unknown")
            entry = {"id": qid, "answer_sha256": marker["answer_sha256"],
                     "reaction": reaction, "cause": cause}
            # Текст исправления — только данные для разбора, не инструкция runtime.
            for key, limit in (("reason", 1000), ("corrected_answer", 4000)):
                if key in row:
                    value = row[key]
                    if (reaction != "dislike" or not isinstance(value, str)
                            or not value.strip() or len(value) > limit):
                        raise ValueError("Некорректное пояснение или исправление наставника")
                    entry[key] = value.strip()
            reactions.append(entry)
            if reaction == "dislike" and not any(x["id"] == qid for x in issues):
                issues.append({"id": qid, "cause": cause, "critical": flag,
                               "status": "requires_investigation"})
        total += sum(score.values())
        model, revision = a.get("model"), a.get("revision")
        models.add(model if isinstance(model, str) else "not_confirmed")
        revisions.add(revision if isinstance(revision, str) else "not_confirmed")
    quality = total / (6 * len(rows))
    full = seen == set(qids)
    comparable = obj.get("comparable") is True
    model = next(iter(models)) if len(models) == 1 else "not_confirmed"
    revision = next(iter(revisions)) if len(revisions) == 1 else "not_confirmed"
    confirmed = model not in {"", "not_confirmed"} and revision not in {"", "not_confirmed"}
    summary = {"batch_id": bid, "review_sha256": expected, "benchmark_id": benchmark,
               "rubric_id": rubric, "quality": round(quality, 6), "reviewed": len(rows),
               "total": len(qids), "complete": full, "critical": critical,
               "model": model, "revision": revision, "comparable": comparable,
               "reactions": {"like": sum(x["reaction"] == "like" for x in reactions),
                             "dislike": sum(x["reaction"] == "dislike" for x in reactions),
                             "missing": len(rows) - len(reactions)},
               "timestamp": now()}
    history = state.get("quality_history", [])
    if not isinstance(history, list) or any(not isinstance(x, dict) for x in history):
        raise ValueError("Повреждена история сравнений")
    # Только полный сопоставимый пакет с ненулевой базой даёт процент.
    if full and comparable and reference_count == len(rows) and reference_total > 0:
        point = {"batch_id": bid, "timestamp": summary["timestamp"],
                 "benchmark_id": benchmark, "rubric_id": rubric,
                 "reference_model": reference_model, "model": model, "revision": revision,
                 "questions": len(rows), "leshy_percent": round(100 * quality, 2),
                 "reference_percent": round(100 * reference_total / (6 * len(rows)), 2),
                 "relative_percent": round(100 * total / reference_total, 2)}
        history = (history + [point])[-168:]
        summary["comparison"] = point
    previous = state.get("last_review") or {}
    matched = (isinstance(previous, dict) and comparable and previous.get("comparable") is True
               and full and previous.get("complete") is True
               and previous.get("batch_id") != bid
               and previous.get("benchmark_id") == benchmark and previous.get("rubric_id") == rubric
               and previous.get("model") == model and previous.get("revision") == revision
               and confirmed and isinstance(previous.get("quality"), (int, float)))
    # Не учитываем один пакет повторно даже с другим хешем.
    reviewed = state.get("reviewed_batches", [])
    if not isinstance(reviewed, list) or any(not isinstance(x, str) for x in reviewed):
        raise ValueError("Повреждён журнал пакетов")
    if bid in reviewed:
        raise ValueError("Повторная оценка пакета требует отдельного разбора")
    maximum = state.get("maximum_confirmed", 0)
    if type(maximum) is not int or not 0 <= maximum <= MAX_PART:
        raise ValueError("Некорректный подтверждённый предел")
    old_quota = quota
    if critical:
        quota = max(1, quota // 2)
        decision = "critical_investigation"
    elif matched and quality < previous["quality"] - 0.000001:
        quota = max(1, quota * 7 // 10)
        decision = "quality_drop"
    elif full and quality < 0.6:
        quota = max(1, quota * 7 // 10)
        decision = "low_quality_investigation"
    elif matched and quality > previous["quality"] + 0.000001 and not issues:
        # Качество само по себе не доказывает часовую пропускную способность.
        # Исполнитель фиксирует завершённые вызовы, а не оценки.
        perf = read_obj(root / "cycle-metrics.json") if (root / "cycle-metrics.json").exists() else {}
        if (perf.get("batch_id") == bid and perf.get("quota") == quota
                and type(perf.get("completed")) is int and perf["completed"] >= (quota * 9 + 9) // 10
                and perf.get("stop_reason") == "quota"):
            maximum = max(maximum, quota)
            quota = min(MAX_PART, max(quota + 1, (quota * 12 + 9) // 10))
            decision = "raise"
        else:
            decision = "hold_capacity_unconfirmed"
    else:
        decision = "hold_quality_unconfirmed"
    if quota < old_quota and not issues:
        issues.append({"id": "", "cause": "unknown", "critical": critical,
                       "status": "requires_investigation"})
    outstanding = state.get("investigation", [])
    if not isinstance(outstanding, list):
        raise ValueError("Повреждён журнал расследований")
    # Старые расследования остаются открытыми: оценка не доказывает исправление.
    outstanding = outstanding + [{"batch_id": bid, **x} for x in issues]
    if len(outstanding) > 4096:
        raise ValueError("Требуется архивирование расследований")
    state = {"quota": quota, "maximum_confirmed": maximum,
             "applied": applied + [expected], "reviewed_batches": reviewed + [bid],
             "last_review": summary, "decision": decision, "previous_quota": old_quota,
             "investigation": outstanding, "quality_history": history}
    # Отдельная запись с хешем утверждённого отчёта; повтор безопасен.
    if reactions:
        atomic(root / (bid + ".reactions-" + expected + ".json"), {
            "source": "AI-mentor", "batch_id": bid, "review_sha256": expected,
            "reactions": reactions,
        })
    atomic(state_path, state)
    return state
