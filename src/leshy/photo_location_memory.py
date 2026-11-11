"""Owner-scoped learning from *explicit user corrections* about public photo landmarks.

This is durable retrieval memory, NOT online weight fine-tuning. Never record
photos, EXIF, coordinates or a vision model's unverified location guesses.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any


_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_PRIVATE = re.compile(
    r"(?iu)\b(?:мой\s+дом|частный\s+дом|квартир[аеыу]|подъезд[аеу]?|"
    r"домашн\w*\s+адрес|где\s+жив[её]т|прожива[ею]т|"
    r"номер\s+дома|место\s+жительства)\b"
)
# Require an actual correction or a teaching assertion, not a bare guess.
_TEACHING = re.compile(
    r"(?iu)^\s*(?:(?:нет|неверно|неправильно|ошибка|ошибся|"
    r"запомни|учись|точнее|на\s+(?:фото|фотографии|снимке)|"
    r"я\s+тебе\s+подскажу|"
    r"это|здесь|тут|правильно|верный\s+ответ)\b[\s,.!:—-]*)+"
)
_LANDMARK = re.compile(
    r"(?iu)\b(памятник|монумент|скульптура|бюст|статуя|"
    r"собор|храм|музей|вокзал|мост|башня|"
    r"площадь|стадион|парк|дворец)\s+"
    r"([А-ЯЁA-Z][А-Яа-яЁёA-Za-z0-9\-]+"
    r"(?:\s+[А-ЯЁA-Z][А-Яа-яЁёA-Za-z0-9\-]+){0,3})"
)
_CITY = re.compile(
    r"(?iu)(?:\bв\s+|\bгород(?:е|а)?\s+|\bгород\s*[:—-]\s*)"
    r"([А-ЯЁ][а-яё\-]+(?:[\s-][А-ЯЁ][а-яё\-]+){0,2})"
)
_CITY_DIRECT = re.compile(
    r"(?iu)^\s*(?:(?:нет|неверно|неправильно)[,!.:\s]+)?"
    r"(?:это\s+)?(?:город\s+)?([А-ЯЁ][а-яё\-]+)\s*[.!]?\s*$"
)
_STOP_WORDS = frozenset({
    "памятник", "монумент", "скульптура", "бюст", "статуя", "собор",
    "храм", "музей", "вокзал", "мост", "башня", "площадь",
    "стадион", "парк", "дворец", "игорь", "игорю", "александр",
    "владимир", "сергей", "иван", "имени", "здесь", "город",
})


def _city_name(value: str) -> str:
    """Normalize very common Russian prepositional suffixes conservatively."""
    city = " ".join(str(value).split()).strip(" .,!?:;—-")[:100]
    known = {
        "челябинске": "Челябинск", "москве": "Москва",
        "санкт-петербурге": "Санкт-Петербург",
        "екатеринбурге": "Екатеринбург", "казани": "Казань",
        "новосибирске": "Новосибирск", "красноярске": "Красноярск",
    }
    lower = city.casefold()
    return known.get(lower, city)


def parse_photo_correction(text: str, *, photo_context: bool) -> tuple[str, str] | None:
    """Require a recent photo and a clear user correction.

    A lone city-name correction is accepted only after a geo-photo dialogue
    has been started; user messages unrelated to the image must not train it.
    """
    value = " ".join(str(text or "").split()).strip()[:400]
    if not photo_context or not value or _PRIVATE.search(value):
        return None
    if value.startswith("/") or "?" in value or re.search(
        r"(?iu)\\b(?:наверное|кажется|возможно|может\\s+быть|похоже)\\b", value,
    ):
        return None
    taught = bool(_TEACHING.match(value))
    city_match = _CITY.search(value)
    # After a geolocation discussion, "в Челябинске" is an explicit
    # correction too, even when sent as a short follow-up clue.
    if city_match and re.match(r"(?iu)^\\s*(?:в\\s+|город(?:е)?\\s+|находится\\s+в\\s+|расположен\\s+в\\s+)", value):
        taught = True
    # A city mentioned after 'в' is not part of a monument's proper name.
    landmark_match = _LANDMARK.search(value[:city_match.start()] if city_match else value)
    if landmark_match and city_match:
        taught = True  # Explicit complete factual association.
    if not taught:
        return None
    landmark = (
        (landmark_match.group(1) + " " + landmark_match.group(2)).strip()
        if landmark_match else ""
    )
    city = _city_name(city_match.group(1)) if city_match else ""
    if not city and taught and not landmark:
        simple = _CITY_DIRECT.match(value)
        if simple:
            city = _city_name(simple.group(1))
    # Do not save pure chat acknowledgements or the assistant's guesses.
    if not city and not landmark:
        return None
    return landmark[:160], city[:100]


def _landmark_terms(landmark: str) -> set[str]:
    words = re.findall(r"(?u)[^\W\d_]{5,}", landmark.casefold().replace("ё", "е"))
    return {
        word[:6] for word in words
        if word not in _STOP_WORDS and not word.startswith(("памят", "скульп", "монум", "стату"))
    }


class PhotoLocationMemory:
    """Private, per-owner SQLite memory in the application's existing Storage."""

    def __init__(self, storage):
        self.storage = storage
        with self.storage.connection() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS photo_location_lessons ("
                "owner_id TEXT NOT NULL, image_sha256 TEXT NOT NULL, "
                "landmark TEXT NOT NULL DEFAULT '', city TEXT NOT NULL DEFAULT '', "
                "taught_at TEXT NOT NULL, PRIMARY KEY(owner_id,image_sha256))"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_photo_location_owner "
                "ON photo_location_lessons(owner_id,taught_at DESC)"
            )

    def remember(
        self, *, owner_id: str, image_sha256: str, landmark: str = "", city: str = "",
    ) -> bool:
        if not owner_id or not _DIGEST.fullmatch(image_sha256):
            return False
        landmark = " ".join(str(landmark).split()).strip()[:160]
        city = " ".join(str(city).split()).strip()[:100]
        if not landmark and not city:
            return False
        if _PRIVATE.search(" ".join((landmark, city))):
            return False
        with self.storage.connection() as conn:
            conn.execute(
                "INSERT INTO photo_location_lessons "
                "(owner_id,image_sha256,landmark,city,taught_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(owner_id,image_sha256) DO UPDATE SET "
                "landmark=CASE WHEN excluded.landmark!='' THEN excluded.landmark "
                "ELSE photo_location_lessons.landmark END, "
                "city=CASE WHEN excluded.city!='' THEN excluded.city "
                "ELSE photo_location_lessons.city END, "
                "taught_at=excluded.taught_at",
                (owner_id, image_sha256, landmark, city,
                 datetime.now(timezone.utc).isoformat()),
            )
        return True

    def exact(self, *, owner_id: str, image_sha256: str) -> dict[str, str] | None:
        if not owner_id or not _DIGEST.fullmatch(image_sha256):
            return None
        with self.storage.connection() as conn:
            item = conn.execute(
                "SELECT landmark,city FROM photo_location_lessons "
                "WHERE owner_id=? AND image_sha256=?",
                (owner_id, image_sha256),
            ).fetchone()
        return {"landmark": item["landmark"], "city": item["city"]} if item else None

    def matching_landmarks(self, *, owner_id: str, visual_description: str) -> list[dict[str, str]]:
        """Return prior owner corrections as *candidates*, not verified matches."""
        if not owner_id or not visual_description:
            return []
        tokens = set(
            word[:6] for word in re.findall(
                r"(?u)[^\W\d_]{5,}", visual_description.casefold().replace("ё", "е")
            )
        )
        output: list[dict[str, str]] = []
        with self.storage.connection() as conn:
            rows = conn.execute(
                "SELECT landmark,city FROM photo_location_lessons "
                "WHERE owner_id=? AND landmark!='' AND city!='' "
                "ORDER BY taught_at DESC LIMIT 300",
                (owner_id,),
            ).fetchall()
        seen: set[tuple[str, str]] = set()
        for row in rows:
            landmark, city = str(row["landmark"]), str(row["city"])
            terms = _landmark_terms(landmark)
            if not terms or not terms.intersection(tokens):
                continue
            key = landmark.casefold(), city.casefold()
            if key not in seen:
                output.append({"landmark": landmark, "city": city})
                seen.add(key)
            if len(output) >= 3:
                break
        return output


__all__ = ["PhotoLocationMemory", "parse_photo_correction"]
