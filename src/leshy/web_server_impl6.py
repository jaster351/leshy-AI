from __future__ import annotations

from . import web_server_impl5 as previous
from .app import LeshyApp
from .freshness_cache_runtime import install_freshness_cache_runtime
from .freshness_memory_runtime import install_freshness_memory_runtime
from .identity_runtime import install_creator_identity_routing
from .learning_command_runtime import install_explicit_learning_routing
from .reliable_web_runtime import install_reliable_web_runtime
from .runtime_latency_fix import install_runtime_latency_fixes
from .voice_tts_ru import RussianLocalVoiceTTS


def _install_russian_voice_tts() -> None:
    """Подменить TTS-класс во всей совместимой цепочке Web-server.

    Старые impl-модули оставляем нетронутыми: это сохраняет проверенный startup,
    streaming и inference scheduler. Меняется только создаваемый TTS manager.
    """
    module = previous
    seen: set[int] = set()
    for _ in range(10):
        if module is None or id(module) in seen:
            break
        seen.add(id(module))
        if hasattr(module, "LocalVoiceTTS"):
            setattr(module, "LocalVoiceTTS", RussianLocalVoiceTTS)
        module = getattr(module, "previous", None)


def make_handler(app, telegram, settings, model_catalog):
    install_explicit_learning_routing(LeshyApp)
    install_reliable_web_runtime(LeshyApp, app)
    install_freshness_memory_runtime(app)
    install_freshness_cache_runtime()
    install_runtime_latency_fixes(app)
    install_creator_identity_routing(LeshyApp)
    _install_russian_voice_tts()
    return previous.make_handler(app, telegram, settings, model_catalog)


def serve(host: str | None = None, port: int | None = None) -> None:
    # Общие runtime-политики ставятся до создания LeshyApp/Telegram, поэтому
    # Web, голос, Telegram и startup recovery используют один и тот же контур.
    install_explicit_learning_routing(LeshyApp)
    install_reliable_web_runtime(LeshyApp)
    # Freshness/cache ставятся до latency-layer: stale fallback и fresh cache
    # попадают в окончательную компактизацию prompt и не переполняют 4096 токенов.
    install_freshness_memory_runtime()
    install_freshness_cache_runtime()
    install_runtime_latency_fixes()
    install_creator_identity_routing(LeshyApp)
    _install_russian_voice_tts()
    previous.serve(host=host, port=port)


def main() -> None:
    serve()


__all__ = ["serve", "main", "make_handler"]
