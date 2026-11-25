from leshy.mentor_performance import capture_mentor_inference, observe_completion, model_descriptor, CyclePerformance


def test_capture_uses_real_backend_counters_and_never_text(tmp_path):
    body = {"usage": {"prompt_tokens": 30, "completion_tokens": 20},
            "timings": {"predicted_n": 20, "predicted_ms": 1000},
            "choices": [{"message": {"content": "PRIVATE TEST TEXT"}}]}
    observe_completion(body, backend="vulkan", model="Qwen3-8B-Q4_K_M.gguf", duration_seconds=2)
    with capture_mentor_inference() as rows:
        observe_completion(body, backend="vulkan", model="Qwen3-8B-Q4_K_M.gguf", duration_seconds=2)
    observe_completion(body, backend="vulkan", model="Qwen3-8B-Q4_K_M.gguf", duration_seconds=2)
    assert len(rows) == 1
    assert rows[0]["generation_tokens_per_second"] == 20
    assert rows[0]["request_output_tokens_per_second"] == 10
    assert rows[0]["model"]["quantization"] == "Q4_K_M"
    assert "PRIVATE TEST TEXT" not in str(rows)
    assert model_descriptor("/private/personal-alias.gguf")["family"] is None
    stats = CyclePerformance(tmp_path)
    stats.rows.extend(rows)
    assert stats.summary()["prompt_tokens"]["sum"] == 30


def test_missing_timing_is_not_zero():
    with capture_mentor_inference() as rows:
        observe_completion({"usage": {"completion_tokens": 10}},
                           backend="native-gguf", model="Qwen3-8B-Q4_K_M.gguf",
                           duration_seconds=2)
    assert rows[0]["prompt_tokens"] is None
    assert rows[0]["generation_tokens_per_second"] is None
    assert rows[0]["request_output_tokens_per_second"] == 5
