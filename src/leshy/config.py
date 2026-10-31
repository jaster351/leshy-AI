from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "да", "вкл"}


def _install_root() -> Path:
    override = os.getenv("LESHY_INSTALL_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    # src/leshy/config.py -> корень установки/репозитория
    return Path(__file__).resolve().parents[2]


def migrate_legacy_brain(legacy: Path, target: Path) -> bool:
    """Однократно переносит старое хранилище, не удаляя источник до проверки."""
    legacy = legacy.expanduser().resolve()
    target = target.expanduser().resolve()
    if legacy == target or not legacy.exists() or target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".migrating")
    if temporary.exists():
        shutil.rmtree(temporary)
    shutil.copytree(legacy, temporary)
    # Минимальная проверка целостности: каждый исходный файл существует и имеет тот же размер.
    for source in legacy.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(legacy)
        copied = temporary / relative
        if not copied.is_file() or copied.stat().st_size != source.stat().st_size:
            shutil.rmtree(temporary, ignore_errors=True)
            raise RuntimeError(f"Не удалось проверить перенос LeshyBrain: {relative}")
    temporary.replace(target)
    return True


@dataclass(slots=True)
class Settings:
    install_root: Path
    brain_dir: Path
    data_dir: Path
    database_path: Path
    native_models_dir: Path
    workspace_root: Path
    llm_backend: str = "native"
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:8b"
    embedding_model: str = "nomic-embed-text"
    native_chat_repo: str = "Qwen/Qwen3-8B-GGUF"
    native_chat_file: str = "Qwen3-8B-Q4_K_M.gguf"
    native_embedding_repo: str = "nomic-ai/nomic-embed-text-v1.5-GGUF"
    native_embedding_file: str = "nomic-embed-text-v1.5.Q4_K_M.gguf"
    native_context_size: int = 8192
    embeddings_enabled: bool = True
    idle_learning_enabled: bool = False
    min_trusted_confidence: float = 0.80
    search_provider: str = "auto"
    searxng_url: str = "http://127.0.0.1:8080"
    browser_reader_enabled: bool = True
    enable_shell: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        root = _install_root()
        brain = Path(os.getenv("LESHY_BRAIN_DIR", root / "LeshyBrain")).expanduser().resolve()
        legacy_value = os.getenv("LESHY_LEGACY_BRAIN_DIR")
        if legacy_value:
            migrate_legacy_brain(Path(legacy_value), brain)
        data = brain / "data"
        models = brain / "models"
        workspace = brain / "workspace"
        for directory in (brain, data, models, workspace):
            directory.mkdir(parents=True, exist_ok=True)
        return cls(
            install_root=root,
            brain_dir=brain,
            data_dir=data,
            database_path=data / "leshy.sqlite3",
            native_models_dir=models,
            workspace_root=workspace,
            llm_backend=os.getenv("LESHY_LLM_BACKEND", "native").strip().lower(),
            ollama_url=os.getenv("LESHY_OLLAMA_URL", "http://127.0.0.1:11434"),
            ollama_model=os.getenv("LESHY_OLLAMA_MODEL", "qwen3:8b"),
            embedding_model=os.getenv("LESHY_EMBEDDING_MODEL", "nomic-embed-text"),
            native_chat_repo=os.getenv("LESHY_NATIVE_CHAT_REPO", "Qwen/Qwen3-8B-GGUF"),
            native_chat_file=os.getenv("LESHY_NATIVE_CHAT_FILE", "Qwen3-8B-Q4_K_M.gguf"),
            native_embedding_repo=os.getenv("LESHY_NATIVE_EMBEDDING_REPO", "nomic-ai/nomic-embed-text-v1.5-GGUF"),
            native_embedding_file=os.getenv("LESHY_NATIVE_EMBEDDING_FILE", "nomic-embed-text-v1.5.Q4_K_M.gguf"),
            native_context_size=int(os.getenv("LESHY_NATIVE_CONTEXT_SIZE", "8192")),
            embeddings_enabled=_env_bool("LESHY_EMBEDDINGS_ENABLED", True),
            idle_learning_enabled=_env_bool("LESHY_IDLE_LEARNING", False),
            min_trusted_confidence=float(os.getenv("LESHY_MIN_TRUSTED_CONFIDENCE", "0.80")),
            search_provider=os.getenv("LESHY_SEARCH_PROVIDER", "auto").strip().lower(),
            searxng_url=os.getenv("LESHY_SEARXNG_URL", "http://127.0.0.1:8080"),
            browser_reader_enabled=_env_bool("LESHY_BROWSER_READER", True),
            enable_shell=_env_bool("LESHY_ENABLE_SHELL", False),
        )
