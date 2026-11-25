from leshy.mentor_answer_evidence import response_evidence, validate_evidence, diagnostic_rows
from leshy.mentor_batch import atomic, read_obj, digest


def context():
    return {"prior_turns": 4, "kept_user_turns": 4, "kept_assistant_turns": 2,
            "omitted_user_turns": 0, "omitted_assistant_turns": 2,
            "estimated_tokens": 750, "budget": 1000, "generation_limit": 200}


def test_actual_context_and_backend():
    obj = response_evidence([context()], [{"backend": "vulkan", "prompt_tokens": 720,
                                            "completion_tokens": 40, "generation_tokens": 40}])
    assert obj["context"]["omitted_assistant_turns"] == 2
    assert obj["backend"]["prompt_tokens"] == 720
    assert obj["model_identity"] == "not_confirmed"
    assert validate_evidence(obj) == obj


def test_missing_samples_not_zero_or_model_failure():
    obj = response_evidence([], [])
    assert obj["context"] is None and obj["backend"] is None


def test_bad_context_and_untrusted_fields_discarded():
    bad = dict(context(), omitted_user_turns=4, arbitrary="private")
    obj = response_evidence([bad], [{"backend": "custom/private", "prompt_tokens": 12}])
    assert obj["context"] is None and obj["backend"] is None


def test_validation_drops_arbitrary_fields():
    obj = response_evidence([context()], [{"backend": "cpu", "prompt_tokens": 22}])
    obj["context"]["private"] = "not exported"
    obj["backend"]["path"] = "not exported"
    obj["model_identity"] = "untrusted"
    validated = validate_evidence(obj)
    assert "private" not in validated["context"]
    assert "path" not in validated["backend"]
    assert validated["model_identity"] == "not_confirmed"


def test_saved_answer_binding_and_legacy_missing(tmp_path):
    bid = "mentor-batch-009"
    bh = "a" * 64
    base = {"request_id": bid + "-q01", "batch_sha256": bh,
            "question_sha256": digest(b"test"), "status": "answered",
            "source": "leshy_model", "answer": "real answer"}
    path = tmp_path / (bid + "-q01.json")
    atomic(path, base)
    args = (tmp_path, bid, bh, [("q01", "test")], read_obj, digest)
    assert diagnostic_rows(*args) == []
    atomic(path, dict(base, diagnostic_evidence=response_evidence([context()], [])))
    rows = diagnostic_rows(*args)
    assert rows[0]["answer_sha256"] == digest(b"real answer")
    assert rows[0]["evidence"]["context"]["prior_turns"] == 4
    atomic(path, dict(base, batch_sha256="b" * 64,
                      diagnostic_evidence=response_evidence([context()], [])))
    assert diagnostic_rows(*args) == []
