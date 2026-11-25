"""Explicit offline PEFT training of a candidate adapter, never activation.

Run in a separate environment with a locally provisioned safetensors base.
No downloads, remote code, chat history, server control or weight replacement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
from pathlib import Path

from .mentor_dataset import clean_text

MAX_DATA = 8 * 1024 * 1024


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def checkpoint_manifest(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("A local HF checkpoint directory is required, not GGUF")
    files = []
    for path in sorted(root.iterdir()):
        if path.is_file() and (path.suffix in {".json", ".safetensors", ".model", ".txt", ".jinja"}):
            if path.is_symlink():
                raise ValueError("Use a resolved, immutable local checkpoint copy")
            files.append({"name": path.name, "size": path.stat().st_size,
                          "sha256": file_hash(path)})
    names = {x["name"] for x in files}
    if "config.json" not in names or not any(n.endswith(".safetensors") for n in names):
        raise ValueError("HF config and safetensors weights are required")
    return files


def read_dataset(path, expected):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_DATA:
        raise ValueError("Invalid local dataset file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Dataset SHA-256 must match explicit approval")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("schema") != "leshy.mentor-sft.v1":
        raise ValueError("Invalid dataset schema")
    keys = {}
    for name in ("train", "holdout"):
        rows = data.get(name)
        if not isinstance(rows, list) or not 1 <= len(rows) <= 512:
            raise ValueError("Invalid split")
        seen = set()
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("messages"), list):
                raise ValueError("Invalid example")
            ms = row["messages"]
            if (not 3 <= len(ms) <= 19 or len(ms) % 2 != 1
                    or any(not isinstance(m, dict) for m in ms)
                    or ms[0].get("role") != "system"
                    or any(m.get("role") != ("user" if i % 2 else "assistant")
                           for i, m in enumerate(ms[1:], 1))
                    or any(not clean_text(m.get("content")) for m in ms)):
                raise ValueError("Invalid or unsafe dialogue")
            qkey = hashlib.sha256(" ".join(ms[-2]["content"].casefold().split()).encode()).hexdigest()
            if row.get("question_sha256") != qkey:
                raise ValueError("Question digest mismatch")
            seen.update(hashlib.sha256(" ".join(m["content"].casefold().split()).encode()).hexdigest()
                        for m in ms if m["role"] == "user")
        keys[name] = seen
    if keys["train"] & keys["holdout"]:
        raise ValueError("Train/holdout question overlap")
    return data


def tokenize_example(tokenizer, messages, max_length):
    """Mask everything except the final answer. Fail on incompatible templates."""
    kwargs = {"tokenize": False, "enable_thinking": False}
    prompt = tokenizer.apply_chat_template(messages[:-1], add_generation_prompt=True, **kwargs)
    complete = tokenizer.apply_chat_template(messages, add_generation_prompt=False, **kwargs)
    if not isinstance(prompt, str) or not isinstance(complete, str) or not complete.startswith(prompt):
        raise ValueError("Template cannot establish the final answer boundary")
    prefix = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    full = tokenizer(complete, add_special_tokens=False)["input_ids"]
    if full[:len(prefix)] != prefix or not prefix or len(full) <= len(prefix):
        raise ValueError("Token boundary is not exact")
    if len(full) > max_length:
        return None
    return {"input_ids": full, "labels": [-100] * len(prefix) + full[len(prefix):]}


def train_candidate(args):
    # Load third-party libraries only in the explicitly started trainer.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    data = read_dataset(args.dataset, args.dataset_sha256)
    base = args.base.resolve(strict=True)
    output = args.output.resolve()
    if output == base or base in output.parents or output.exists():
        raise ValueError("Candidate output must be a new directory outside base")
    manifest = checkpoint_manifest(base)
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from safetensors import safe_open

    if not torch.cuda.is_available():
        raise RuntimeError("This trainer requires an explicitly provisioned CUDA GPU")
    if not 0 <= args.gpu < torch.cuda.device_count():
        raise ValueError("Invalid CUDA device")
    torch.cuda.set_device(args.gpu)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(str(base), local_files_only=True,
                                               trust_remote_code=False)
    splits = {}
    for name in ("train", "holdout"):
        rows = [tokenize_example(tokenizer, x["messages"], args.max_length)
                for x in data[name]]
        if any(r is None for r in rows):
            raise ValueError("An example exceeds max_length; prepare a shorter dataset")
        splits[name] = rows
    if len(splits["train"]) < 16 or len(splits["holdout"]) < 8:
        raise ValueError("Need at least 16 training and 8 holdout examples for a pilot")

    kw = {"local_files_only": True, "trust_remote_code": False,
          "use_safetensors": True, "torch_dtype": dtype, "device_map": {"": args.gpu}}
    if args.qlora:
        kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
    model = AutoModelForCausalLM.from_pretrained(str(base), **kw)
    model.config.use_cache = False
    if args.qlora:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
    else:
        model.gradient_checkpointing_enable()
        model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(r=args.rank, lora_alpha=2 * args.rank,
        lora_dropout=0.05, target_modules=["q_proj", "v_proj"], bias="none",
        task_type="CAUSAL_LM"))
    trainable = [(n, p) for n, p in model.named_parameters() if p.requires_grad]
    if not trainable or any("lora_" not in n for n, _ in trainable):
        raise RuntimeError("Base weights must remain frozen")
    device = torch.device("cuda", args.gpu)
    optimizer = torch.optim.AdamW([p for _, p in trainable], lr=args.learning_rate)
    scaler = torch.amp.GradScaler("cuda", enabled=dtype == torch.float16)

    def forward(row):
        inputs = torch.tensor([row["input_ids"]], device=device, dtype=torch.long)
        labels = torch.tensor([row["labels"]], device=device, dtype=torch.long)
        with torch.autocast("cuda", dtype=dtype):
            loss = model(input_ids=inputs, attention_mask=torch.ones_like(inputs), labels=labels).loss
        if not torch.isfinite(loss).item():
            raise RuntimeError("Nonfinite training or evaluation loss")
        return loss

    def evaluate():
        model.eval()
        total, count = 0.0, 0
        with torch.no_grad():
            for row in splits["holdout"]:
                tokens = sum(v != -100 for v in row["labels"])
                total += float(forward(row).item()) * tokens
                count += tokens
        return total / count

    with model.disable_adapter():
        before = evaluate()
    steps = 0
    train_loss = []
    model.train()
    optimizer.zero_grad(set_to_none=True)
    for epoch in range(args.epochs):
        rows = list(splits["train"])
        random.shuffle(rows)
        for start in range(0, len(rows), args.accumulation):
            group = rows[start:start + args.accumulation]
            for row in group:
                loss = forward(row)
                train_loss.append(float(loss.item()))
                scaler.scale(loss / len(group)).backward()
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_([p for _, p in trainable], 1.0, error_if_nonfinite=True)
            scaler.step(optimizer)
            scaler.update()
            optimizer.zero_grad(set_to_none=True)
            steps += 1
    after = evaluate()
    if not math.isfinite(before) or not math.isfinite(after) or not steps:
        raise RuntimeError("Incomplete training")
    # Verify the base did not change on disk. No base weights are saved.
    if checkpoint_manifest(base) != manifest:
        raise RuntimeError("Base checkpoint changed during training")
    output.mkdir(parents=False, exist_ok=False)
    model.save_pretrained(str(output), safe_serialization=True, save_embedding_layers=False)
    adapter = output / "adapter_model.safetensors"
    if not adapter.is_file():
        raise RuntimeError("No adapter weights saved")
    with safe_open(str(adapter), framework="pt", device="cpu") as f:
        changed = any("lora_B" in k and bool(torch.count_nonzero(f.get_tensor(k)).item())
                      for k in f.keys())
    if not changed:
        raise RuntimeError("No nonzero LoRA delta found")
    record = {"schema": "leshy.mentor-lora-candidate.v1",
              "status": "candidate_unvalidated", "dataset_sha256": args.dataset_sha256,
              "base_files": manifest, "adapter_sha256": file_hash(adapter),
              "adapter_config_sha256": file_hash(output / "adapter_config.json"),
              "train_examples": len(splits["train"]), "holdout_examples": len(splits["holdout"]),
              "optimizer_steps": steps, "trainable_parameters": sum(p.numel() for _, p in trainable),
              "train_loss_mean": sum(train_loss) / len(train_loss),
              "holdout_loss_base": before, "holdout_loss_candidate": after,
              "qlora": args.qlora, "rank": args.rank, "seed": args.seed,
              "epochs": args.epochs, "learning_rate": args.learning_rate,
              "max_length": args.max_length, "accumulation": args.accumulation}
    with (output / "candidate.json").open("x", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in record.items() if k != "base_files"}))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", required=True, type=Path)
    p.add_argument("--dataset-sha256", required=True)
    p.add_argument("--base", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--gpu", type=int, default=0)
    p.add_argument("--qlora", action="store_true")
    p.add_argument("--rank", type=int, choices=(4, 8, 16), default=8)
    p.add_argument("--epochs", type=int, choices=(1, 2, 3), default=1)
    p.add_argument("--max-length", type=int, choices=(1024, 2048, 4096), default=2048)
    p.add_argument("--accumulation", type=int, choices=(1, 2, 4, 8, 16), default=4)
    p.add_argument("--learning-rate", type=float, default=0.0001)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--confirm-dedicated-gpu", action="store_true", required=True)
    args = p.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{64}", args.dataset_sha256) or not 0 < args.learning_rate <= 0.0005:
        p.error("Invalid dataset digest or learning rate")
    train_candidate(args)


if __name__ == "__main__":
    main()
