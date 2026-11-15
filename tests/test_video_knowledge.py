from pathlib import Path

from leshy.knowledge import KnowledgeBase
from leshy.storage import Storage
from leshy.video_knowledge import VideoKnowledgeIngestor, VideoSegment, VideoSource


def test_video_fragments_are_saved_with_timecodes(tmp_path: Path):
    storage = Storage(tmp_path / "leshy.db")
    knowledge = KnowledgeBase(storage)
    ingestor = VideoKnowledgeIngestor(knowledge)

    result = ingestor.ingest(
        topic="двигатель",
        source=VideoSource(
            source_id="video-1",
            source_url="https://example.test/video/1",
            title="Устройство двигателя",
        ),
        segments=[
            VideoSegment(
                start_seconds=12.5,
                end_seconds=25.0,
                transcript="Четырёхтактный двигатель выполняет впуск, сжатие, рабочий ход и выпуск.",
                ocr_text="Порядок работы цилиндров зависит от конструкции конкретного двигателя.",
                visual_description="На схеме показаны поршень, цилиндр, впускной и выпускной клапаны.",
            )
        ],
        verify=False,
    )

    assert result["segments"] == 1
    assert result["accepted_claims"] == 3

    with storage.connection() as conn:
        rows = conn.execute(
            "SELECT source_type,metadata_json FROM knowledge ORDER BY id"
        ).fetchall()

    assert {row["source_type"] for row in rows} == {
        "video-speech",
        "video-ocr",
        "video-vision",
    }
    assert all('"start_seconds": 12.5' in row["metadata_json"] for row in rows)
    assert all('"end_seconds": 25.0' in row["metadata_json"] for row in rows)
