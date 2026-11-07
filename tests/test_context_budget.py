from __future__ import annotations

from leshy.adaptive_backend import AdaptiveLLM
from leshy.context_budget import apply_context_budget, estimate_tokens
from leshy.llm import ChatMessage


class CaptureBackend:
    def __init__(self, n_ctx: int, max_tokens: int = 1024, *, fail: bool = False):
        self.n_ctx=n_ctx; self.max_tokens=max_tokens; self.fail=fail; self.seen=[]
    def chat(self,messages):
        self.seen=list(messages)
        if self.fail: raise RuntimeError("backend failed")
        return "ok"


def test_hard_budget_keeps_latest_user_and_fits_4096_backend():
    messages=[ChatMessage("system","правила "*300)]
    for index in range(30):
        messages.append(ChatMessage("user",f"старый вопрос {index} "+"x"*500)); messages.append(ChatMessage("assistant",f"старый ответ {index} "+"y"*500))
    messages.append(ChatMessage("user","КРИТИЧЕСКАЯ ТЕКУЩАЯ ЦЕЛЬ "+"z"*1200))
    result=apply_context_budget(messages,context_size=4096,reserve_tokens=1024)
    assert result.tokens_before>result.budget; assert result.tokens_after<=result.budget
    assert "КРИТИЧЕСКАЯ ТЕКУЩАЯ ЦЕЛЬ" in result.messages[-1].content; assert result.dropped_messages>0


def test_budget_is_recomputed_for_smaller_failover_backend():
    gpu=CaptureBackend(4096,1024,fail=True); npu=CaptureBackend(2048,512)
    llm=AdaptiveLLM([("gpu-vulkan",gpu),("npu-openvino",npu)],cooldown_seconds=5)
    messages=[ChatMessage("system","system "*200)]
    messages += [ChatMessage("user","history "*300),ChatMessage("assistant","answer "*300)]*8
    messages.append(ChatMessage("user","reply target: СТАРОЕ СООБЩЕНИЕ — ответь именно на него"))
    assert llm.chat(messages)=="ok"
    assert estimate_tokens(gpu.seen)<=4096-1024
    assert estimate_tokens(npu.seen)<=2048-512
    assert "reply target" in npu.seen[-1].content


def test_tiny_budget_preserves_current_user_goal():
    messages=[ChatMessage("system","rules "*1000),ChatMessage("user","old "*1000),ChatMessage("assistant","old answer "*1000),ChatMessage("user","ВАЖНАЯ ЦЕЛЬ: ответить на выбранную Telegram reply quote "+"q"*4000)]
    result=apply_context_budget(messages,context_size=1024,reserve_tokens=512)
    assert result.tokens_after<=result.budget
    assert result.messages[-1].role=="user"
    assert "ВАЖНАЯ ЦЕЛЬ" in result.messages[-1].content or "Telegram reply quote" in result.messages[-1].content


def test_latest_user_keeps_instruction_and_quote_tail_after_shrinking():
    goal = "КРИТИЧЕСКАЯ ЦЕЛЬ: отвечать только на текущий вопрос. "
    quote = "цитата " * 1500 + "КОНЕЦ ЦИТАТЫ"
    messages = [
        ChatMessage("system", "политика " * 1500),
        ChatMessage("assistant", "предыдущее рассуждение " * 1500),
        ChatMessage("user", goal + quote),
    ]
    result = apply_context_budget(messages, context_size=1024, reserve_tokens=512)
    assert result.tokens_after <= result.budget
    assert result.messages[-1].role == "user"
    assert "КРИТИЧЕСКАЯ ЦЕЛЬ" in result.messages[-1].content
    assert "КОНЕЦ ЦИТАТЫ" in result.messages[-1].content

def test_non_ascii_context_estimate_is_conservative_for_4096():
    messages = [ChatMessage("system", "инструкции " * 700)]
    messages += [ChatMessage("user", "русский текст " * 700)]
    result = apply_context_budget(messages, context_size=4096, reserve_tokens=1024)
    assert result.tokens_before > result.budget
    assert result.tokens_after <= result.budget
    assert result.messages[-1].role == "user"
    assert "русский" in result.messages[-1].content


def test_current_telegram_reply_remains_after_multilingual_compaction():
    messages = [
        ChatMessage("system", "правила " * 1000),
        ChatMessage("assistant", "old context " * 800),
        ChatMessage("user", "Ответь именно на это Telegram Reply: ВОПРОС " + "цитата " * 2000 + "КОНЕЦ ЦИТАТЫ"),
    ]
    result = apply_context_budget(messages, context_size=4096, reserve_tokens=1024)
    assert result.tokens_after <= result.budget
    assert "Ответь именно на это" in result.messages[-1].content
    assert "КОНЕЦ ЦИТАТЫ" in result.messages[-1].content
