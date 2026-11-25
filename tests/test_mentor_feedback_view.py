"""Regression coverage for the read-only mentor feedback display."""
from leshy.mentor_batch import atomic, digest
from leshy.mentor_view import mentor_snapshot
from leshy.mentor_feedback_view import committed_reviews, committed_summary


def fixture(tmp_path, reaction="dislike"):
    root = tmp_path / "ai-mentor"
    root.mkdir()
    bid, rid, h = "mentor-batch-002", "mentor-batch-002-q01", "a" * 64
    answer = "Исходный ответ"
    ah = digest(answer.encode("utf-8"))
    atomic(root / "config.json", {"enabled": True})
    atomic(root / (rid + ".question.json"), {"request_id": rid, "question": "Вопрос"})
    atomic(root / (rid + ".json"), {"request_id": rid, "status": "answered",
                                    "source": "leshy_model", "answer": answer})
    atomic(root / (rid + ".published.json"), {"request_id": rid, "answer_sha256": ah})
    row = {"id": "q01", "answer_sha256": ah, "reaction": reaction,
           "cause": "context" if reaction == "dislike" else "none"}
    if reaction == "dislike":
        row.update(reason="Потерян контекст", corrected_answer="<b>Исправление</b>")
    journal = {"source": "AI-mentor", "batch_id": bid, "review_sha256": h, "reactions": [row]}
    journal_path = root / (bid + ".reactions-" + h + ".json")
    atomic(journal_path, journal)
    last = {"batch_id": bid, "review_sha256": h, "quality": 0.5,
            "reviewed": 1, "total": 1, "complete": True, "comparable": False,
            "reactions": {"like": int(reaction == "like"),
                          "dislike": int(reaction == "dislike"), "missing": 0},
            "reference": {"reference_percent": 100, "relative_percent": 50}}
    load = {"applied": [h], "reviewed_batches": [bid], "last_review": last}
    atomic(root / "load-policy.json", load)
    return root, rid, journal_path, journal, load


def test_applied_dislike_exposes_correction_as_data(tmp_path):
    fixture(tmp_path)
    result = mentor_snapshot(tmp_path, chat_offset=0)
    assert result["evaluation_status"] == "review_applied"
    assert result["applied_reviews_count"] == 1
    assert result["items"][0]["feedback"]["reaction"] == "dislike"
    assert result["items"][0]["feedback"]["reason"] == "Потерян контекст"
    assert result["items"][0]["feedback"]["corrected_answer"] == "<b>Исправление</b>"
    assert result["review_feedback"]["relative_percent"] == 50
    assert result["review_feedback"]["comparable"] is False
    assert result["quality_history"] == []


def test_applied_like_without_correction(tmp_path):
    fixture(tmp_path, "like")
    result = mentor_snapshot(tmp_path)
    feedback = result["items"][0]["feedback"]
    assert feedback["reaction"] == "like"
    assert "corrected_answer" not in feedback


def test_uncommitted_journal_is_not_feedback(tmp_path):
    root, _, _, _, load = fixture(tmp_path)
    load["applied"] = []
    atomic(root / "load-policy.json", load)
    result = mentor_snapshot(tmp_path)
    assert result["evaluation_status"] == "not_reviewed"
    assert result["review_quality"] is None
    assert "feedback" not in result["items"][0]


def test_changed_answer_or_missing_publication_hides_feedback(tmp_path):
    root, rid, _, _, _ = fixture(tmp_path)
    (root / (rid + ".published.json")).unlink()
    assert "feedback" not in mentor_snapshot(tmp_path)["items"][0]
    atomic(root / (rid + ".published.json"), {"request_id": rid,
                                            "answer_sha256": "b" * 64})
    assert "feedback" not in mentor_snapshot(tmp_path)["items"][0]


def test_wrong_journal_digest_and_duplicate_qid_fail_closed(tmp_path):
    _, _, path, journal, _ = fixture(tmp_path)
    journal["reactions"][0]["answer_sha256"] = "b" * 64
    atomic(path, journal)
    assert "feedback" not in mentor_snapshot(tmp_path)["items"][0]
    journal["reactions"].append(dict(journal["reactions"][0]))
    atomic(path, journal)
    assert "feedback" not in mentor_snapshot(tmp_path)["items"][0]


def test_partial_and_invalid_counts_are_not_paired_comparisons(tmp_path):
    _, _, _, _, load = fixture(tmp_path)
    load["last_review"].update(total=2, complete=False,
                               reactions={"like": 8, "dislike": 0, "missing": 0})
    summary = committed_summary(load, committed_reviews(load))
    assert summary["complete"] is False
    assert "relative_percent" not in summary
    assert "reactions" not in summary
