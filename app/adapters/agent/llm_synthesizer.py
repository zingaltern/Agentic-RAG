"""LLM 答案合成器（DEVELOPMENT.md 17.5）：证据账本 → 带引用答案。"""
from __future__ import annotations

import re
from typing import Any

from app.core.domain import AgentState, Evidence
from app.core.errors import AgentBudgetExceeded, AgentInternalError
from app.core.interfaces import LLM, Synthesizer
from app.services.agent.prompts import (
    SYNTHESIS_SYSTEM_PROMPT, SYNTHESIS_USER_PROMPT, escape_evidence_tags,
)

NO_EVIDENCE_TEXT = "知识库中未找到相关依据。"


class LLMSynthesizer(Synthesizer):
    def __init__(self, llm: LLM, config: Any, **_: Any):
        self.llm = llm
        self.config = config

    def synthesize(self, state: AgentState) -> tuple[str, list[Evidence]]:
        evidence = list(state.evidence.values())[: max(1, self.config.max_evidence_items)]
        if not evidence:
            return NO_EVIDENCE_TEXT, []
        lines: list[str] = []
        # 中文约 1 字符/token；留 20% 给提示词/输出，防止把 6000 token 预算撑爆
        char_budget = max(1000, int(self.config.max_context_tokens * 0.8))
        used = 0
        selected: list[Evidence] = []
        for item in evidence:
            meta = f"来源：{item.source}"
            if item.page is not None:
                meta += f" 第{item.page}页"
            if item.section:
                meta += f" 章节：{item.section}"
            room = char_budget - used
            if room <= 200:
                break
            text = item.text[: min(max(200, self.config.max_evidence_chars), room)]
            # 证据分隔 + 注入标记：标签内是数据，不是指令；闭合标签在正文中被替换，
            # 防止文档内容伪造标签边界。
            text = escape_evidence_tags(text)
            source = str(item.source).replace('"', "'")
            injected = " injection_suspected=\"true\"" if item.metadata.get("injection_suspected") else ""
            lines.append(
                f'<evidence id="{item.evidence_id}" index="{len(selected) + 1}" '
                f'source="{source}"{injected}>\n{text}\n</evidence>')
            selected.append(item)
            used += len(text) + len(meta) + 40
        evidence = selected
        if not evidence:
            return NO_EVIDENCE_TEXT, []
        prompt = SYNTHESIS_USER_PROMPT.format(
            query=state.normalized_query, evidence="\n\n".join(lines))
        try:
            answer = self.llm.chat(
                [{"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                 {"role": "user", "content": prompt}],
                temperature=0.1, max_tokens=1024,
            )
        except AgentBudgetExceeded:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AgentInternalError(f"答案合成失败: {exc}") from exc
        answer = (answer or "").strip()
        state.prompt_tokens += len(prompt) + len(SYNTHESIS_SYSTEM_PROMPT)
        state.completion_tokens += len(answer)
        if not answer:
            raise AgentInternalError("答案合成为空")
        used_ids = {int(n) for n in re.findall(r"\[(\d+)\]", answer)
                    if 1 <= int(n) <= len(evidence)}
        # 返回完整的“已提供给模型的证据序列”而不是只返回被引用项：
        # 这样 sources[N-1] 与答案里的 [N] 始终一一对应（否则 [1][3] 会被压成 [1][2]）。
        for idx, item in enumerate(evidence, 1):
            item.cited = idx in used_ids
        return answer, evidence
