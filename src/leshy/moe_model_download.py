"""Stage the InferSSD-tested Qwen3.6 MoE GGUF on *this node's* local NVMe.

This is independent of original Inkling Safetensors, and never changes the
currently running Qwen backend. The selected GGUF is one of the checkpoints
listed as tested in wici-ai/inferssd, not a guessed Inkling conversion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

REPOSITORY = "unsloth/Qwen3.6-35B-A3B-GGUF"
FILENAME = "Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf"
FOLDER = "InferSSD-Qwen3.6-35B-A3B"
MANIFEST = ".leshy-moe-gguf.json"
MIN_RESERVE_BYTES = 12 * 1024**3

PROXY_FALLBACKS = (
    "http://vpn.nidr.cyberhost.biz:7777",
    "http://vpn.usa.cyberhost.biz:7777",
)


def model_directory(brain_dir: Path) -> Path:
    override = os.getenv("LESHY_INFERSSD_MODEL_DIR", "").strip()
    target = Path(override).expanduser() if override else Path(brain_dir) / "models" / FOLDER
    result = target.resolve()
    if os.name == "nt" and str(result).startswith("\\\\"):
        raise RuntimeError("InferSSD model must use this node's local NVMe, not a UNC share")
    return result


def model_file(brain_dir: Path) -> Path:
    return model_directory(brain_dir) / FILENAME


def model_status(brain_dir: Path) -> dict:
    file = model_file(brain_dir)
    result = {"repository": REPOSITORY, "file": str(file), "state": "missing"}
    manifest = file.parent / MANIFEST
    if not manifest.is_file():
        if file.exists():
            result["state"] = "incomplete"
        return result
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("repository") != REPOSITORY or data.get("filename") != FILENAME:
            raise ValueError("model manifest mismatch")
        if not file.is_file() or file.stat().st_size != int(data["bytes"]):
            raise ValueError("GGUF missing or size mismatch")
        with file.open("rb") as stream:
            if stream.read(4) != b"GGUF":
                raise ValueError("Invalid GGUF magic")
        result.update(
            state="downloaded",
            revision=data["revision"],
            bytes=int(data["bytes"]),
            sha256=data["sha256"],
            integrity="sha256 computed at staging",
        )
    except (KeyError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        result.update(state="incomplete", error=str(exc))
    return result


def _digest_file(file: Path) -> str:
    digest = hashlib.sha256()
    with file.open("rb") as stream:
        while True:
            block = stream.read(8 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def download_attempt(brain_dir: Path) -> dict:
    """Only called inside an isolated subprocess with fixed proxy environment."""
    from huggingface_hub import HfApi, hf_hub_download
    from .hf_settings import HuggingFaceSettings

    brain_dir = Path(brain_dir).expanduser().resolve()
    HuggingFaceSettings(brain_dir).apply()
    target = model_directory(brain_dir)
    target.mkdir(parents=True, exist_ok=True)

    info = HfApi().model_info(repo_id=REPOSITORY, files_metadata=True)
    revision = str(info.sha or "")
    if not revision:
        raise RuntimeError("Hugging Face did not provide the MoE model revision")
    asset = next((f for f in (info.siblings or []) if f.rfilename == FILENAME), None)
    if asset is None or not asset.size or int(asset.size) < 10 * 1024**3:
        raise RuntimeError("Expected official single-file GGUF is not published")
    size = int(asset.size)
    file = target / FILENAME
    existing = file.stat().st_size if file.exists() else 0
    free = shutil.disk_usage(target).free
    if free + existing < size + MIN_RESERVE_BYTES:
        raise RuntimeError(
            f"Not enough local NVMe space to stage MoE GGUF: free={free // 1024**3} GiB, "
            f"model={size // 1024**3} GiB, reserve={MIN_RESERVE_BYTES // 1024**3} GiB"
        )
    hf_hub_download(
        repo_id=REPOSITORY,
        filename=FILENAME,
        revision=revision,
        local_dir=str(target),
        token=os.getenv("HF_TOKEN") or None,
    )
    if not file.is_file() or file.stat().st_size != size:
        raise RuntimeError("GGUF download is incomplete")
    with file.open("rb") as stream:
        if stream.read(4) != b"GGUF":
            raise RuntimeError("Downloaded file has invalid GGUF header")
    local_sha = _digest_file(file)
    lfs = getattr(asset, "lfs", None)
    published_sha = (lfs.get("sha256") if isinstance(lfs, dict) else getattr(lfs, "sha256", None))
    if published_sha and str(published_sha).lower() != local_sha:
        raise RuntimeError("GGUF SHA-256 mismatch against Hugging Face metadata")
    marker = {
        "repository": REPOSITORY,
        "filename": FILENAME,
        "revision": revision,
        "bytes": size,
        "sha256": local_sha,
        "published_sha256_checked": bool(published_sha),
    }
    temporary = target / (MANIFEST + ".tmp")
    temporary.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    os.replace(temporary, target / MANIFEST)
    return model_status(brain_dir)


def _proxy_candidates() -> list[str]:
    candidates: list[str] = []
    for proxy in (
        os.getenv("LESHY_HF_PROXY_URL", ""),
        os.getenv("HTTPS_PROXY", ""),
        *PROXY_FALLBACKS,
    ):
        proxy = str(proxy).strip()
        parsed = urlsplit(proxy)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        try:
            if parsed.port is None:
                continue
        except ValueError:
            continue
        if proxy not in candidates:
            candidates.append(proxy)
    return candidates


def download(brain_dir: Path) -> dict:
    """Try direct HF first, then user-configured HTTP CONNECT proxy fallbacks."""
    from .inkling_original import _hf_retryable_error, _hf_proxy_label

    attempts = [None, *_proxy_candidates()]
    for index, proxy in enumerate(attempts):
        label = "direct" if proxy is None else "proxy " + _hf_proxy_label(proxy)
        print("InferSSD MoE GGUF: download route " + label, flush=True)
        env = os.environ.copy()
        for key in list(env):
            if key.lower() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
                env.pop(key, None)
        if proxy is None:
            env["NO_PROXY"] = "*"
        else:
            env["HTTP_PROXY"] = proxy
            env["HTTPS_PROXY"] = proxy
            env["NO_PROXY"] = "127.0.0.1,localhost,::1"
        env["PYTHONUNBUFFERED"] = "1"
        cmd = [
            sys.executable, "-m", "leshy.moe_model_download", "download-attempt",
            "--brain-dir", str(brain_dir),
        ]
        tail: list[str] = []
        process = subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace",
        )
        assert process.stdout is not None
        with process.stdout:
            for line in process.stdout:
                if proxy:
                    line = line.replace(proxy, _hf_proxy_label(proxy))
                tail.append(line)
                if len(tail) > 100:
                    tail.pop(0)
                print(line, end="", flush=True)
        if process.wait() == 0:
            return model_status(brain_dir)
        output = "".join(tail)
        if index == len(attempts) - 1 or not _hf_retryable_error(output):
            raise RuntimeError("InferSSD MoE GGUF could not be downloaded via " + label)
    raise RuntimeError("No usable Hugging Face route")


def main() -> int:
    parser = argparse.ArgumentParser(description="Leshy: tested MoE GGUF for local NVMe InferSSD")
    parser.add_argument("action", choices=("status", "download", "download-attempt"))
    parser.add_argument("--brain-dir", required=True)
    args = parser.parse_args()
    brain = Path(args.brain_dir).expanduser().resolve()
    if args.action == "status":
        result = model_status(brain)
    elif args.action == "download":
        result = download(brain)
    else:
        result = download_attempt(brain)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
