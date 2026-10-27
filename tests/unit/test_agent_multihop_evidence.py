"""P6 D02 回归：比较/多跳计划下，证据必须归到叶子子目标，合成目标由依赖满足。

旧实现按字符重合把《员工手册》证据贪心归给“对比”目标 g3，导致 g1 永远为空；
两轮无新证据后整体 21001 弃答。本测试用两文档桩复现该形状，要求正常 answered。
"""
from __future__ import annotations

import json

from app.core.config import load_config
from app.core.domain import (
    Chunk, DetectionResult, LLMResponse, ScoredChunk, ToolCall,
)
from app.services.agent.runtime import AgentRuntime


class _Retriever:
    def __init__(self):
        self.calls = []

    def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
        self.calls.append(query)
        if "员工手册" in query:
            chunk = Chunk("c1", "doc1", "入职满 2 年及以上：年假 10 天；最高累计 20 天。",
                          0, None, {"source": "员工手册.md", "page": 3, "section": "年假"})
        else:
            chunk = Chunk("c2", "doc2", "一线城市（北京、上海、广州、深圳、杭州）：500 元/晚。",
                          0, None, {"source": "差旅政策.md", "page": 2, "section": "住宿标准"})
        return [ScoredChunk(chunk=chunk, score=0.9, notion="rrf")][:k]


class _Reranker:
    def rerank(self, query, chunks):
        return list(chunks)


class _Detector:
    def detect(self, answer, context):
        return DetectionResult(verified=True, suspected=[], verified_rate=1.0)


PLAN = {
    "goal": "对比员工手册年假与差旅政策住宿标准",
    "complexity": "comparison",
    "subgoals": [
        {"goal_id": "g1", "question": "在《员工手册》中查年假天数",
         "success_criteria": "找到《员工手册》年假条款与数字"},
        {"goal_id": "g2", "question": "在《差旅政策》中查一线城市住宿标准",
         "success_criteria": "找到《差旅政策》住宿金额"},
        {"goal_id": "g3", "question": "对比两者是否一致",
         "success_criteria": "基于 g1/g2 给出对比", "depends_on": ["g1", "g2"]},
    ],
}


class _LLM:
    def __init__(self):
        self.chat_calls = 0
        self.tool_calls = 0

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        self.chat_calls += 1
        if self.chat_calls == 1:
            return json.dumps(PLAN, ensure_ascii=False)
        return "员工手册：入职满 2 年年假 10 天[1]；差旅政策：一线城市住宿 500 元/晚[2]。"

    def chat_with_tools(self, messages, tools, tool_choice="auto",
                        temperature=0.1, max_tokens=1024):
        self.tool_calls += 1
        if self.tool_calls == 1:
            return LLMResponse(tool_calls=[ToolCall(
                "call_1", "kb_search", {"query": "员工手册 年假", "k": 2})])
        if self.tool_calls == 2:
            return LLMResponse(tool_calls=[ToolCall(
                "call_2", "kb_search", {"query": "差旅政策 住宿标准", "k": 2})])
        return LLMResponse(content="员工手册：年假 10 天[1]；差旅政策：住宿 500 元/晚[2]。")


def _runtime():
    config = load_config()
    config.orchestration.agent.enabled = True
    config.llm.tool_calling = "native"
    config.orchestration.agent.tools.web_search.enabled = False
    return AgentRuntime(
        config=config, llm=_LLM(), retriever=_Retriever(),
        reranker=_Reranker(), vector_store=None, documents=None,
        detector=_Detector(),
    )


def test_comparison_evidence_goes_to_leaf_goals_and_answers():
    runtime = _runtime()
    answer = runtime.run("对比《员工手册》年假与《差旅政策》住宿标准，是否一致？",
                         collection="rag_default", kb_id="default", debug=True)
    assert answer.code == 0, (answer.code, answer.message)
    assert answer.mode == "agentic"
    assert answer.termination_reason == "answered"
    assert len(answer.sources) == 2
    goals = {g["goal_id"]: g for g in answer.plan["subgoals"]}
    assert goals["g1"]["evidence_ids"] == ["E1"]
    assert goals["g2"]["evidence_ids"] == ["E2"]
    # 合成目标不抢证据；由依赖满足即可
    assert goals["g3"]["evidence_ids"] == []
    assert all(step["evidence_ids"] for step in answer.steps if step["action"] == "kb_search")
