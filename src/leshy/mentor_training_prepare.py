"""Local, bounded preparation of synthetic mentor datasets; never trains a model."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path

from .mentor_batch import atomic, digest, encode, now, read_bytes, read_obj
from .mentor_dataset import _questions, build_dataset
from .mentor_feedback_view import committed_reviews

_BATCH = re.compile(r"mentor-batch-[a-z0-9-]{1,55}")
_MAX_DATA = 8 * 1024 * 1024
_MAX_ARTIFACTS = 32
_PLAN = "training-plan.json"


def validate_plan(plan):
    keys = {"schema", "train_batches", "holdout_batches", "protected_batches"}
    if set(plan) != keys or plan.get("schema") != "leshy.mentor-training-plan.v1":
        raise ValueError("Invalid local training plan")
    names = []
    for key in ("train_batches", "holdout_batches", "protected_batches"):
        ids = plan[key]
        if (not isinstance(ids, list) or not ids or len(ids) > 32
                or any(not isinstance(b, str) or not _BATCH.fullmatch(b) for b in ids)):
            raise ValueError("Invalid batch assignment")
        names.extend(ids)
    if len(names) > 32 or len(set(names)) != len(names):
        raise ValueError("Batch assignments must be disjoint")
    return plan


def _save_dataset(root, raw):
    if len(raw) > _MAX_DATA:
        raise ValueError("Dataset too large")
    directory = root / "training-datasets"
    if directory.is_symlink():
        raise ValueError("Unsafe dataset directory")
    directory.mkdir(exist_ok=True)
    sha = digest(raw)
    target = directory / (sha + ".json")
    if target.is_symlink():
        raise ValueError("Unsafe dataset path")
    if target.exists():
        if read_bytes(target, _MAX_DATA) != raw:
            raise ValueError("Existing dataset digest mismatch")
        return sha
    # Keep previous artifacts for review and rollback; never silently delete them.
    if sum(1 for _ in directory.iterdir()) >= _MAX_ARTIFACTS:
        raise ValueError("Dataset retention limit")
    fd, tmp = tempfile.mkstemp(prefix=".prepare-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        # Atomic no-overwrite publication; another preparer may have won.
        try:
            os.link(tmp, target)
        except FileExistsError:
            if read_bytes(target, _MAX_DATA) != raw:
                raise ValueError("Concurrent dataset digest mismatch")
    finally:
        Path(tmp).unlink(missing_ok=True)
    return sha


def prepare(root, *, allowed=lambda: True):
    """Inventory committed examples and build only explicitly assigned splits.

    An unassigned or protected batch is never implicitly admitted to training.
    Return only bounded numeric/status metadata, never prompts or local paths.
    """
    root = Path(root)
    if root.is_symlink() or not root.is_dir() or not allowed():
        return {"status": "disabled"}
    if not (root / "load-policy.json").is_file():
        return {"status": "awaiting_reviews"}
    committed = committed_reviews(read_obj(root / "load-policy.json"))
    result = {"status": "awaiting_plan", "committed_batches": len(committed),
              "inspected_batches": 0, "eligible_batches": 0, "eligible_examples": 0,
              "rejected_batches": 0}
    plan_path = root / _PLAN
    plan_raw = None
    if plan_path.exists() or plan_path.is_symlink():
        plan_raw = read_bytes(plan_path, 32 * 1024)
        plan = validate_plan(json.loads(plan_raw))
        wanted = plan["train_batches"] + plan["holdout_batches"]
    else:
        plan = None
        wanted = list(committed)[-16:]
    # Keep the hourly job bounded. Protected audit examples are never read.
    for bid in wanted:
        if not allowed():
            return {"status": "disabled"}
        if bid not in committed:
            continue
        result["inspected_batches"] += 1
        try:
            rows, _ = _questions(root, bid, committed[bid])
        except (OSError, ValueError, TypeError, KeyError):
            result["rejected_batches"] += 1
            continue
        if rows:
            result["eligible_batches"] += 1
            result["eligible_examples"] += len(rows)
    if plan is not None:
        if any(b not in committed for b in wanted):
            result["status"] = "awaiting_reviews"
        elif result["rejected_batches"] or result["eligible_batches"] != len(wanted):
            result["status"] = "invalid_sources"
        else:
            try:
                dataset = build_dataset(root, plan["train_batches"], plan["holdout_batches"])
                train, holdout = len(dataset["train"]), len(dataset["holdout"])
                result.update(train_examples=train, holdout_examples=holdout)
                if train < 16 or not 8 <= holdout <= 64:
                    result["status"] = "insufficient_examples"
                elif not allowed() or read_bytes(plan_path, 32 * 1024) != plan_raw:
                    return {"status": "disabled"}
                elif committed_reviews(read_obj(root / "load-policy.json")) != committed:
                    result["status"] = "reviews_changed"
                else:
                    # Only local immutable candidate data. Explicit approval of
                    # its SHA is still required by mentor_lora; no training here.
                    dataset["plan_sha256"] = digest(plan_raw)
                    dataset["protected_batches"] = plan["protected_batches"]
                    sha = _save_dataset(root, encode(dataset))
                    result.update(status="prepared", dataset_sha256=sha)
            except (OSError, ValueError, TypeError, KeyError):
                result["status"] = "preparation_rejected"
    if allowed():
        atomic(root / "training-preparation.json", dict(result, timestamp=now()))
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mentor-root", type=Path, required=True)
    p.add_argument("--train-batch", action="append")
    p.add_argument("--holdout-batch", action="append")
    p.add_argument("--protected-batch", action="append")
    p.add_argument("--write-plan", action="store_true")
    args = p.parse_args(argv)
    root = args.mentor_root
    if root.is_symlink() or not root.is_dir():
        p.error("Invalid mentor root")
    if args.write_plan:
        plan = validate_plan({"schema": "leshy.mentor-training-plan.v1",
                              "train_batches": args.train_batch,
                              "holdout_batches": args.holdout_batch,
                              "protected_batches": args.protected_batch})
        # Never overwrite an existing local approval without a separate
        # explicit operator edit; this command cannot install remote policy.
        with (root / _PLAN).open("xb") as handle:
            handle.write(encode(plan))
    elif any((args.train_batch, args.holdout_batch, args.protected_batch)):
        p.error("Batch assignments require --write-plan")
    print(json.dumps(prepare(root), ensure_ascii=False))


if __name__ == "__main__":
    main()
