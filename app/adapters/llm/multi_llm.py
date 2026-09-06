"""多模型候选 + 熔断降级（A6，智能体体验增强）。

- 按优先级依次尝试候选 LLM；单候选连续失败达阈值 → 熔断（冷却期后自动恢复）。
- 全部候选不可用 → 抛 LLMUnavailableError（调用方维持 30001/llm=None 语义）。
- 线程安全（熔断状态加锁）。
"""
from __future__ import annotations

import threading
import time
from typing import Any

from app.core.errors import LLMUnavailableError
from app.core.interfaces import LLM
from app.core.logging import get_logger

logger = get_logger("llm")


class _Circuit:
    def __init__(self, fail_threshold: int, cooldown_seconds: int):
        self.fail_threshold = fail_threshold
        self.cooldown_seconds = cooldown_seconds
        self.fails = 0
        self.open_until = 0.0
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            if self.open_until and time.time() < self.open_until:
                return False
            if self.open_until:
                self.open_until = 0.0   # 冷却期结束自动恢复
            return True

    def record_success(self) -> None:
        with self.lock:
            self.fails = 0
            self.open_until = 0.0

    def record_failure(self) -> None:
        with self.lock:
            self.fails += 1
            if self.fails >= self.fail_threshold:
                self.open_until = time.time() + self.cooldown_seconds
                self.fails = 0


class MultiCandidateLLM(LLM):
    def __init__(self, candidates: list[LLM], fail_threshold: int = 3,
                 cooldown_seconds: int = 60, **_: Any):
        if not candidates:
            raise ValueError("candidates 不能为空")
        self._candidates = list(candidates)
        self._circuits = [_Circuit(fail_threshold, cooldown_seconds) for _ in self._candidates]

    def chat(self, messages: list[dict[str, str]],
             temperature: float = 0.1, max_tokens: int = 1024) -> str:
        last_exc: Exception | None = None
        for idx, llm in enumerate(self._candidates):
            if not self._circuits[idx].allow():
                logger.info("候选 %s 处于熔断冷却期，跳过", idx)
                continue
            try:
                out = llm.chat(messages, temperature=temperature, max_tokens=max_tokens)
                self._circuits[idx].record_success()
                return out
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self._circuits[idx].record_failure()
                logger.warning("候选 %s 调用失败（累计熔断阈值 %s）: %s",
                               idx, self._circuits[idx].fail_threshold, exc)
        raise LLMUnavailableError(f"全部模型候选不可用: {last_exc}")
