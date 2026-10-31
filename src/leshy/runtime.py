from __future__ import annotations

from . import __version__
from .accelerator_embeddings import LlamaServerEmbeddings
from .accelerator_runtime import LlamaCppRuntimeManager, LlamaServerLLM
from .activity import LearningActivity
from .adaptive_backend import AdaptiveEmbeddings, AdaptiveLLM
from .agent import LeshyAgent
from .approvals import ApprovalStore
from .config import Settings
from .curiosity import CuriosityEngine
from .diagnostics import configure_diagnostics, event
from .embeddings import OllamaEmbeddings, VectorIndex
from .experience import ExperienceLearner
from .hardware import HardwareConfigStore, detect_hardware, resolve_execution_plan
from .knowledge import KnowledgeBase
from .learning_control import LearningControl
from .llm import OllamaLLM
from .memory import MemoryStore
from .model_repair import ResilientNativeLlamaEmbeddings, ResilientNativeLlamaLLM, ResilientNativeModelManager
from .models import ModelRegistry
from .native_inference import NativeModelSpec
from .permissions import PermissionPolicy
from .privacy import PrivacyService
from .research import CompliantBrowserFetcher, DdgsSearch, FallbackSearch, ResearchEngine, SearxngSearch
from .safety import LearningSafetyGate
from .storage import Storage
from .sync18 import BrainKnowledgeSyncManager
from .tools import build_default_tools
from .telegram import TelegramBridge
from .verifier import SemanticVerifier
from .workspace import Workspace


def build_search_provider(settings: Settings):
    if settings.search_provider == "searxng":
        return SearxngSearch(settings.searxng_url)
    if settings.search_provider == "ddgs":
        return DdgsSearch()
    if settings.search_provider not in {"auto", "fallback"}:
        raise ValueError(f"Неизвестный LESHY_SEARCH_PROVIDER: {settings.search_provider}")
    return FallbackSearch(SearxngSearch(settings.searxng_url, timeout=3), DdgsSearch())


def build_native_model_manager(settings: Settings) -> ResilientNativeModelManager:
    return ResilientNativeModelManager(
        settings.native_models_dir,
        NativeModelSpec(settings.native_chat_repo, settings.native_chat_file, "chat", "Apache-2.0"),
        NativeModelSpec(settings.native_embedding_repo, settings.native_embedding_file, "embedding", "MIT"),
    )


class Runtime:
    def __init__(self, settings: Settings):
        self.settings = settings
        configure_diagnostics(settings.brain_dir, __version__)
        self.storage = Storage(settings.database_path)
        self.activity = LearningActivity(self.storage)
        self.learning_control = LearningControl(self.storage, settings.idle_learning_enabled)
        self.models = ModelRegistry(self.storage)
        self.native_models = build_native_model_manager(settings)
        self.hardware_store = HardwareConfigStore(settings.brain_dir)
        self.hardware_config = self.hardware_store.load()
        self.hardware_capabilities = detect_hardware()
        self.execution_plan = resolve_execution_plan(self.hardware_config, self.hardware_capabilities)
        self.accelerator_runtime = LlamaCppRuntimeManager(settings.brain_dir)

        event(
            "hardware_execution_plan",
            requested_mode=self.execution_plan.requested_mode,
            effective_mode=self.execution_plan.effective_mode,
            chat_device=self.execution_plan.chat_device,
            chat_backend=self.execution_plan.chat_backend,
            embedding_backend=self.execution_plan.embedding_backend,
            background_devices=list(self.execution_plan.background_devices),
            gpu_names=list(self.hardware_capabilities.gpu_names),
            gpu_count=len(self.hardware_capabilities.gpu_names),
            npu_names=list(self.hardware_capabilities.npu_names),
            warnings=list(self.execution_plan.warnings),
        )

        self.background_llm = None
        self.embedding_provider = None

        if settings.llm_backend == "native":
            self.model_name = settings.native_chat_file
            effective = set(self.execution_plan.effective_mode.split("_"))
            gpu_count = max(1, len(self.hardware_capabilities.gpu_names))

            cpu_llm = ResilientNativeLlamaLLM(
                self.native_models.chat_path,
                n_ctx=settings.native_context_size,
                n_gpu_layers=0,
                ensure_model=self.native_models.ensure_chat,
            )
            gpu_llm = None
            npu_llm = None

            if "gpu" in effective and self.hardware_capabilities.gpu_offload_runtime:
                gpu_llm = LlamaServerLLM(
                    self.accelerator_runtime,
                    "vulkan",
                    self.native_models.chat_path,
                    ensure_model=self.native_models.ensure_chat,
                    n_ctx=settings.native_context_size,
                    n_gpu_layers=-1,
                    gpu_count=gpu_count,
                    startup_timeout=30,
                    request_timeout=90,
                )
            if "npu" in effective and self.hardware_capabilities.openvino_npu:
                npu_llm = LlamaServerLLM(
                    self.accelerator_runtime,
                    "openvino",
                    self.native_models.chat_path,
                    ensure_model=self.native_models.ensure_chat,
                    n_ctx=min(settings.native_context_size, 2048),
                    n_gpu_layers=0,
                    device="NPU",
                    startup_timeout=15,
                    request_timeout=60,
                )

            # Интерактивный ответ: всегда GPU → NPU → CPU.
            chat_candidates = []
            if gpu_llm is not None:
                chat_candidates.append(("gpu-vulkan", gpu_llm))
            if npu_llm is not None:
                chat_candidates.append(("npu-openvino", npu_llm))
            chat_candidates.append(("cpu-native", cpu_llm))
            self.llm = AdaptiveLLM(
                chat_candidates,
                cooldown_seconds=180,
                priority=("gpu", "npu", "cpu", "other"),
            )

            # Фон не должен отнимать GPU у чата: NPU → CPU → GPU.
            background_candidates = []
            if npu_llm is not None:
                background_candidates.append(("npu-openvino", npu_llm))
            background_candidates.append(("cpu-native", cpu_llm))
            if gpu_llm is not None:
                background_candidates.append(("gpu-vulkan", gpu_llm))
            self.background_llm = AdaptiveLLM(
                background_candidates,
                cooldown_seconds=300,
                priority=("npu", "cpu", "gpu", "other"),
            )

            cpu_embeddings = ResilientNativeLlamaEmbeddings(
                self.native_models.embedding_path,
                n_gpu_layers=0,
                ensure_model=self.native_models.ensure_embedding,
            )
            gpu_embeddings = None
            npu_embeddings = None
            if "gpu" in effective and self.hardware_capabilities.gpu_offload_runtime:
                gpu_embeddings = LlamaServerEmbeddings(
                    self.accelerator_runtime,
                    "vulkan",
                    self.native_models.embedding_path,
                    ensure_model=self.native_models.ensure_embedding,
                    n_ctx=min(settings.native_context_size, 2048),
                    n_gpu_layers=-1,
                    gpu_count=gpu_count,
                    startup_timeout=15,
                    request_timeout=30,
                )
            if "npu" in effective and self.hardware_capabilities.openvino_npu:
                npu_embeddings = LlamaServerEmbeddings(
                    self.accelerator_runtime,
                    "openvino",
                    self.native_models.embedding_path,
                    ensure_model=self.native_models.ensure_embedding,
                    n_ctx=1024,
                    n_gpu_layers=0,
                    device="NPU",
                    startup_timeout=10,
                    request_timeout=20,
                )

            embedding_candidates = []
            if gpu_embeddings is not None:
                embedding_candidates.append(("gpu-vulkan", gpu_embeddings))
            if npu_embeddings is not None:
                embedding_candidates.append(("npu-openvino", npu_embeddings))
            embedding_candidates.append(("cpu-native", cpu_embeddings))
            self.embedding_provider = AdaptiveEmbeddings(
                embedding_candidates,
                cooldown_seconds=300,
                priority=("gpu", "npu", "cpu", "other"),
            )
        else:
            self.model_name = self.models.active_name() or settings.ollama_model
            self.llm = OllamaLLM(settings.ollama_url, self.model_name)
            self.background_llm = self.llm
            self.embedding_provider = OllamaEmbeddings(settings.ollama_url, settings.embedding_model, timeout=8)

        self.vector_index = VectorIndex(self.storage, self.embedding_provider) if settings.embeddings_enabled else None
        self.knowledge = KnowledgeBase(self.storage, settings.min_trusted_confidence, self.vector_index)
        self.memory = MemoryStore(self.storage, self.vector_index)
        background_llm = self.background_llm or self.llm
        self.semantic_verifier = SemanticVerifier(self.storage, background_llm)
        self.safety_gate = LearningSafetyGate(background_llm)
        browser_fetcher = CompliantBrowserFetcher() if settings.browser_reader_enabled else None
        self.research = ResearchEngine(
            self.storage,
            self.knowledge,
            build_search_provider(settings),
            semantic_verifier=self.semantic_verifier,
            activity=self.activity,
            safety_gate=self.safety_gate,
            browser_fetcher=browser_fetcher,
        )
        self.workspace = Workspace(settings.workspace_root)
        self.permissions = PermissionPolicy.safe_default(allow_exec=settings.enable_shell)
        self.approvals = ApprovalStore(self.storage)
        self.privacy = PrivacyService(self.storage, self.approvals)
        self.tools = build_default_tools(
            self.storage,
            self.knowledge,
            self.memory,
            self.research,
            self.workspace,
            self.permissions,
            self.approvals,
            enable_shell=settings.enable_shell,
        )
        self.experience = ExperienceLearner(self.storage, background_llm, self.memory)
        self.curiosity = CuriosityEngine(self.storage, background_llm, self.research)
        self.agent = LeshyAgent(
            self.storage,
            self.llm,
            self.knowledge,
            self.memory,
            tools=self.tools,
            experience=self.experience,
        )
        self.sync = BrainKnowledgeSyncManager(self.storage, self.knowledge, settings.data_dir, self.agent.instance_id)
        self.telegram = TelegramBridge(self, settings.brain_dir, legacy_data_dir=settings.data_dir)

    def hardware_status(self) -> dict:
        from dataclasses import asdict
        result = {
            "config": asdict(self.hardware_config),
            "capabilities": asdict(self.hardware_capabilities),
            "plan": asdict(self.execution_plan),
        }
        status = getattr(self.llm, "status", None)
        if callable(status):
            result["chat_router"] = status()
        background_status = getattr(self.background_llm, "status", None)
        if callable(background_status):
            result["background_router"] = background_status()
        embedding_status = getattr(self.embedding_provider, "status", None)
        if callable(embedding_status):
            result["embedding_router"] = embedding_status()
        return result
