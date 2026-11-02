from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Any

from .context_budget import apply_context_budget
from .diagnostics import event, exception
from .hardware import HardwareConfigStore
from .llm import ChatMessage, LLMUnavailable
from .persona import with_leshy_identity


@dataclass
class _BackendState:
    name: str
    provider: Any
    failed_until: float = 0.0
    failures: int = 0
    last_latency_ms: int | None = None
    quarantined: bool = False
    quarantine_reason: str = ""


def _hardware_kind(name: str) -> str:
    lowered = name.lower()
    if "gpu" in lowered or "vulkan" in lowered or "cuda" in lowered or "rocm" in lowered: return "gpu"
    if "npu" in lowered or "openvino" in lowered: return "npu"
    if "cpu" in lowered or "native" in lowered: return "cpu"
    return "other"


def _fatal_npu_failure(name: str, exc: BaseException) -> bool:
    if _hardware_kind(name) != "npu": return False
    text = str(exc).upper().replace("-", "_").replace(" ", "_")
    markers = ("HTTP_503", "HTTP503", "STATUS_503", "503_SERVICE_UNAVAILABLE", "SERVICE_UNAVAILABLE",
               "OUT_OF_HOST_MEMORY", "OUT_OF_DEVICE_MEMORY", "DEVICE_LOST", "DEVICE_NOT_AVAILABLE",
               "INFERENCE_ENGINE_ERROR", "OPENVINO_ERROR")
    return any(marker in text for marker in markers)


class AdaptiveLLM:
    def __init__(self, candidates: list[tuple[str, Any]], *, cooldown_seconds: float = 180.0,
                 priority: tuple[str, ...] = ("gpu", "npu", "cpu", "other")):
        if not candidates: raise ValueError("Нужен хотя бы один LLM backend")
        self._states = [_BackendState(name, provider) for name, provider in candidates]
        self.cooldown_seconds=max(5.0,float(cooldown_seconds)); self.priority=tuple(priority)
        self._priority_rank={kind:index for index,kind in enumerate(self.priority)}; self._preferred=self._states[0].name
        self._lock=threading.RLock(); self.model="adaptive:"+",".join(state.name for state in self._states)
        self._hardware_store=self._discover_hardware_store()
    def _discover_hardware_store(self):
        for state in self._states:
            manager=getattr(state.provider,"manager",None); brain_dir=getattr(manager,"brain_dir",None)
            if brain_dir is not None:
                try: return HardwareConfigStore(brain_dir)
                except Exception: return None
        return None
    def _hardware_mode(self):
        if self._hardware_store is None: return "auto"
        try: return str(self._hardware_store.load().mode or "auto").lower()
        except Exception: return "auto"
    def _routing_priority(self):
        routes={"cpu":("cpu",),"gpu":("gpu","cpu"),"npu":("npu","cpu"),"cpu_gpu":("gpu","cpu"),"cpu_npu":("npu","cpu"),"gpu_npu":("gpu","npu","cpu"),"cpu_gpu_npu":("gpu","npu","cpu")}
        return routes.get(self._hardware_mode(),self.priority)
    @staticmethod
    def _rank_for(state,priority):
        rank={kind:index for index,kind in enumerate(priority)}; return (rank.get(_hardware_kind(state.name),len(rank)),state.failures,state.name)
    def _ordered(self):
        now=time.monotonic(); routing=self._routing_priority(); allowed=set(routing)
        with self._lock:
            usable=[s for s in self._states if not s.quarantined and _hardware_kind(s.name) in allowed]
            if not usable: usable=[s for s in self._states if not s.quarantined and _hardware_kind(s.name)=="cpu"]
            healthy=[s for s in usable if s.failed_until<=now]; healthy.sort(key=lambda s:self._rank_for(s,routing)); return healthy
    def _mark_failure(self,state,exc,latency_ms):
        fatal=_fatal_npu_failure(state.name,exc)
        with self._lock:
            state.failures+=1; state.last_latency_ms=latency_ms
            if fatal: state.quarantined=True; state.quarantine_reason=str(exc); state.failed_until=float("inf")
            else: state.failed_until=time.monotonic()+self.cooldown_seconds
        return fatal
    def _mark_success(self,state,latency_ms):
        with self._lock:
            switched=self._preferred!=state.name; self._preferred=state.name; state.failures=0; state.failed_until=0.0; state.last_latency_ms=latency_ms
        return switched
    def warmup(self):
        for state in self._ordered():
            warmer=getattr(state.provider,"warmup",None)
            if not callable(warmer): continue
            started=time.monotonic()
            try: warmer()
            except Exception as exc:
                latency_ms=int((time.monotonic()-started)*1000); fatal=self._mark_failure(state,exc,latency_ms)
                exception("adaptive_llm_warmup_failed",exc,backend=state.name,latency_ms=latency_ms,cooldown_seconds=self.cooldown_seconds,quarantined=fatal); continue
            latency_ms=int((time.monotonic()-started)*1000); switched=self._mark_success(state,latency_ms)
            event("adaptive_llm_warmup_ok",backend=state.name,latency_ms=latency_ms,switched=switched); return state.name
        return None
    @staticmethod
    def _budget_for(state,messages):
        context_size=int(getattr(state.provider,"n_ctx",4096) or 4096); max_tokens=int(getattr(state.provider,"max_tokens",1024) or 1024)
        result=apply_context_budget(messages,context_size=context_size,reserve_tokens=max_tokens)
        if result.tokens_before>result.budget:
            event("llm_context_compacted",backend=state.name,context_size=context_size,reserve_tokens=max_tokens,tokens_before=result.tokens_before,tokens_after=result.tokens_after,budget=result.budget,dropped_messages=result.dropped_messages,truncated_messages=result.truncated_messages)
        return result.messages
    def chat(self,messages):
        prepared_messages=with_leshy_identity(messages); errors=[]; routing=self._routing_priority(); ordered=self._ordered()
        if not ordered:
            status=self.status(); cooling=[b for b in status["backends"] if b["selected"] and (b["cooldown_remaining_seconds"] or 0)>0]
            event("adaptive_llm_circuit_open",hardware_mode=self._hardware_mode(),routing=list(routing),cooling_backends=[b["name"] for b in cooling])
            raise LLMUnavailable("Все выбранные локальные LLM backend временно недоступны (circuit breaker/cooldown)")
        for state in ordered:
            started=time.monotonic()
            try: text=state.provider.chat(self._budget_for(state,prepared_messages))
            except Exception as exc:
                latency_ms=int((time.monotonic()-started)*1000); fatal=self._mark_failure(state,exc,latency_ms)
                exception("adaptive_llm_backend_failed",exc,backend=state.name,latency_ms=latency_ms,cooldown_seconds=self.cooldown_seconds,quarantined=fatal,hardware_mode=self._hardware_mode(),routing=list(routing)); errors.append(f"{state.name}: {exc}"); continue
            latency_ms=int((time.monotonic()-started)*1000); switched=self._mark_success(state,latency_ms)
            event("adaptive_llm_backend_ok",backend=state.name,latency_ms=latency_ms,switched=switched,hardware_mode=self._hardware_mode(),routing=list(routing)); return text
        raise LLMUnavailable("Все выбранные локальные LLM backend недоступны: "+"; ".join(errors[-3:]))
    def status(self):
        now=time.monotonic(); mode=self._hardware_mode(); routing=self._routing_priority()
        with self._lock:
            rows=[]
            for state in self._states:
                resident=None; resident_status=getattr(state.provider,"resident_status",None)
                if callable(resident_status):
                    try: resident=resident_status()
                    except Exception: resident=None
                rows.append({"name":state.name,"kind":_hardware_kind(state.name),"selected":_hardware_kind(state.name) in set(routing),"cooldown_remaining_seconds":None if state.quarantined else max(0.0,round(state.failed_until-now,1)),"failures":state.failures,"last_latency_ms":state.last_latency_ms,"quarantined":state.quarantined,"quarantine_reason":state.quarantine_reason,"resident":resident})
            return {"preferred":self._preferred,"priority":list(self.priority),"hardware_mode":mode,"effective_routing":list(routing),"backends":rows}
    def close(self):
        for state in self._states:
            closer=getattr(state.provider,"close",None)
            if callable(closer):
                try: closer()
                except Exception: pass


class AdaptiveEmbeddings:
    def __init__(self,candidates,*,cooldown_seconds=180.0,priority=("gpu","npu","cpu","other")):
        if not candidates: raise ValueError("Нужен хотя бы один embedding backend")
        self._states=[_BackendState(name,provider) for name,provider in candidates]; self.cooldown_seconds=max(5.0,float(cooldown_seconds)); self.priority=tuple(priority); self._priority_rank={kind:index for index,kind in enumerate(self.priority)}; self._preferred=self._states[0].name; self._lock=threading.RLock()
        provider_models=[str(getattr(s.provider,"model",s.name)) for s in self._states]; basenames=[v.split(":",1)[-1] for v in provider_models]; canonical=basenames[0] if basenames and all(n==basenames[0] for n in basenames) else "mixed"; self.model=f"adaptive:{canonical}"; self.compatible_models=tuple(dict.fromkeys([self.model,*provider_models]))
    def available(self):
        now=time.monotonic()
        for state in self._states:
            if state.quarantined or state.failed_until>now: continue
            available=getattr(state.provider,"available",None)
            try:
                if available is None or bool(available()): return True
            except Exception: continue
        return False
    def _rank(self,state): return (self._priority_rank.get(_hardware_kind(state.name),len(self._priority_rank)),state.failures,state.name)
    def _ordered(self):
        now=time.monotonic()
        with self._lock:
            healthy=[s for s in self._states if not s.quarantined and s.failed_until<=now]; healthy.sort(key=self._rank); return healthy
    def embed(self,text):
        errors=[]
        for state in self._ordered():
            started=time.monotonic()
            try: vector=state.provider.embed(text)
            except Exception as exc:
                latency_ms=int((time.monotonic()-started)*1000); fatal=_fatal_npu_failure(state.name,exc)
                with self._lock:
                    state.failures+=1; state.last_latency_ms=latency_ms
                    if fatal: state.quarantined=True; state.quarantine_reason=str(exc); state.failed_until=float("inf")
                    else: state.failed_until=time.monotonic()+self.cooldown_seconds
                exception("adaptive_embedding_backend_failed",exc,backend=state.name,latency_ms=latency_ms,cooldown_seconds=self.cooldown_seconds,quarantined=fatal); errors.append(f"{state.name}: {exc}"); continue
            latency_ms=int((time.monotonic()-started)*1000)
            with self._lock: switched=self._preferred!=state.name; self._preferred=state.name; state.failures=0; state.failed_until=0.0; state.last_latency_ms=latency_ms
            event("adaptive_embedding_backend_ok",backend=state.name,latency_ms=latency_ms,switched=switched); return vector
        raise RuntimeError("Все embedding backend недоступны: "+"; ".join(errors[-3:]))
    def status(self):
        now=time.monotonic()
        with self._lock:
            return {"preferred":self._preferred,"priority":list(self.priority),"model":self.model,"compatible_models":list(self.compatible_models),"backends":[{"name":s.name,"kind":_hardware_kind(s.name),"cooldown_remaining_seconds":None if s.quarantined else max(0.0,round(s.failed_until-now,1)),"failures":s.failures,"last_latency_ms":s.last_latency_ms,"quarantined":s.quarantined,"quarantine_reason":s.quarantine_reason} for s in self._states]}
    def close(self):
        for state in self._states:
            closer=getattr(state.provider,"close",None)
            if callable(closer):
                try: closer()
                except Exception: pass
