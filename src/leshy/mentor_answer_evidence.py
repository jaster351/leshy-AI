"""Bounded evidence about a real synthetic mentor response, not a diagnosis."""
from __future__ import annotations

import re

_BACKENDS = {"vulkan", "openvino", "cpu", "native-gguf", "cpu-native", "cpu-llama-server", "other"}
_TURNS = ("prior_turns", "kept_user_turns", "kept_assistant_turns",
          "omitted_user_turns", "omitted_assistant_turns")
_TOKENS = ("estimated_tokens", "budget", "generation_limit")
_USAGE = ("prompt_tokens", "completion_tokens", "generation_tokens")


def _int(value, maximum):
    return value if type(value) is int and 0 <= value <= maximum else None


def _sample(rows):
    if isinstance(rows, list) and rows and isinstance(rows[-1], dict):
        return rows[-1]
    return {}


def response_evidence(context_samples, measured):
    context = _sample(context_samples)
    backend = _sample(measured)
    result = {"schema": "leshy.mentor-answer-evidence.v1",
              "context": None, "backend": None, "model_identity": "not_confirmed"}
    turns = {k: _int(context.get(k), 8) for k in _TURNS}
    tokens = {k: _int(context.get(k), 10_000_000) for k in _TOKENS}
    if (all(v is not None for v in turns.values()) and all(v is not None for v in tokens.values())
            and turns["kept_user_turns"] + turns["omitted_user_turns"] == turns["prior_turns"]
            and turns["kept_assistant_turns"] + turns["omitted_assistant_turns"] == turns["prior_turns"]
            and turns["kept_assistant_turns"] <= turns["kept_user_turns"]
            and tokens["estimated_tokens"] <= tokens["budget"]
            and tokens["generation_limit"] > 0):
        result["context"] = {**turns, **tokens}
    name = backend.get("backend")
    if isinstance(name, str) and name in _BACKENDS:
        result["backend"] = {"name": name,
                             **{k: _int(backend.get(k), 10_000_000) for k in _USAGE}}
    return result


def validate_evidence(obj):
    # Reconstruct from allowlisted fields; arbitrary values, local paths,
    # filenames, prompts and extra keys cannot enter telemetry.
    if not isinstance(obj, dict) or obj.get("schema") != "leshy.mentor-answer-evidence.v1":
        return None
    context, backend = obj.get("context"), obj.get("backend")
    rows = [dict(backend, backend=backend.get("name"))] if isinstance(backend, dict) else []
    return response_evidence([context], rows)


def diagnostic_rows(root, batch_id, batch_sha, questions, read_obj, digest):
    if not re.fullmatch(r"mentor-batch-[a-z0-9-]{1,55}", batch_id):
        raise ValueError("Invalid batch identifier")
    rows = []
    for qid, question in questions[:64]:
        if not re.fullmatch(r"q[0-9]{1,6}", qid):
            continue
        rid = batch_id + "-" + qid
        try:
            obj = read_obj(root / (rid + ".json"))
            if (obj.get("request_id") != rid or obj.get("batch_sha256") != batch_sha
                    or obj.get("question_sha256") != digest(question.encode("utf-8"))
                    or obj.get("status") != "answered" or obj.get("source") != "leshy_model"
                    or not isinstance(obj.get("answer"), str) or not obj["answer"]):
                continue
            evidence = validate_evidence(obj.get("diagnostic_evidence"))
            if evidence is not None:
                rows.append({"id": qid, "answer_sha256": digest(obj["answer"].encode("utf-8")),
                             "evidence": evidence})
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return rows
