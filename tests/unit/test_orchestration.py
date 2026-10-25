"""编排服务单测（DEVELOPMENT.md 5.9、8.1：Mock 驱动 3 次循环 / 超时 / 21001 兜底）。"""
from __future__ import annotations

import time

import pytest

from app.core.config import load_config
from app.core.domain import Chunk, DetectionResult, EvaluationResult, ScoredChunk
from app.core.errors import RequestTimeoutError
from app.services.orchestration import FALLBACK_TEXT, OrchestrationService


def _chunk(text: str, source: str = "员工手册.md", parent_id: str | None = None) -> Chunk:
    return Chunk(chunk_id=f"c_{hash(text) % 10000}", document_id="doc_x", text=text,
                 order=0, parent_id=parent_id, metadata={"source": source, "kind": "md"})


def _scored(text: str, source: str = "员工手册.md", parent_id: str | None = None) -> ScoredChunk:
    return ScoredChunk(chunk=_chunk(text, source, parent_id), score=0.8, notion="rrf")


def _build(retriever, evaluator, rewriter, detector, llm, config=None):
    config = config or load_config()

    class _Reranker:
        def rerank(self, query, chunks):
            return list(chunks)

    return OrchestrationService(
        config=config, retriever=retriever, reranker=_Reranker(),
        evaluator=evaluator, rewriter=rewriter, detector=detector, llm=llm,
        vector_store=None,
    )


class _Retriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
        self.calls.append({"query": query, "k": k, "filters": filters, "path": path,
                           "collection": collection})
        return self.chunks[:k]


class _Evaluator:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def evaluate(self, query, chunks):
        self.calls.append(query)
        return self.results.pop(0)


class _Rewriter:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def rewrite(self, query, feedback):
        self.calls.append(query)
        return self.results.pop(0)


class _Detector:
    def detect(self, answer, context):
        return DetectionResult(verified=True, suspected=[], verified_rate=1.0)


class _LLM:
    def __init__(self, answer="年假需提前 3 个工作日申请[1]。"):
        self.answer = answer
        self.messages = None

    def chat(self, messages, temperature=0.1, max_tokens=1024):
        self.messages = messages
        return self.answer


def test_reflection_loop_rewrites_then_answers():
    """评估前两轮不过 → 改写两次 → 第三轮通过 → 返回 answer（8.1：3 次循环）。"""
    retriever = _Retriever([_scored("年假申请流程说明")])
    evaluator = _Evaluator([
        EvaluationResult(False, 0.2, "below_threshold"),
        EvaluationResult(False, 0.3, "below_threshold"),
        EvaluationResult(True, 0.9, "ok"),
    ])
    rewriter = _Rewriter(["年假申请", "年假申请流程"])
    llm = _LLM()
    svc = _build(retriever, evaluator, rewriter, _Detector(), llm)
    answer = svc.run("咋样申请年假", debug=True)
    assert answer.code == 0
    assert answer.text == llm.answer
    assert answer.iterations == 2
    assert len(retriever.calls) == 3, "循环三轮各检索一次"
    assert rewriter.calls == ["咋样申请年假", "年假申请"], "每次改写以上一轮改写结果为输入"
    assert answer.hallucination.verified is True
    assert answer.debug_trace, "debug=true 应返回 debug_trace"
    # 6.4 示例：评估条 iter=当前轮；改写条 iter=下一轮编号 → [0(评估),1(改写),1(评估)]
    assert [t["iter"] for t in answer.debug_trace[:3]] == [0, 1, 1]
    eval_steps = [t for t in answer.debug_trace if "evaluator_score" in t]
    assert len(eval_steps) == 3, "三轮评估各一条记录"


def test_sources_filename_equals_metadata_source():
    """6.4 关键契约：sources[].filename = 入库时 Chunk.metadata.source 原始文件名。"""
    retriever = _Retriever([_scored("年假需提前申请", source="员工手册.md"),
                            _scored("IT 账号开通", source="IT支持手册.md")])
    evaluator = _Evaluator([EvaluationResult(True, 0.9, "ok")])
    svc = _build(retriever, evaluator, _Rewriter([]), _Detector(), _LLM())
    answer = svc.run("年假怎么申请")
    filenames = [s["filename"] for s in answer.sources]
    assert filenames == ["员工手册.md", "IT支持手册.md"], "必须用 metadata.source 而非其他键"


def test_fallback_21001_after_max_iterations():
    """检索空 + 始终 below → 反思达上限 → 21001 兜底（A2：无候选不澄清）。"""
    retriever = _Retriever([])   # 检索无候选
    config = load_config()
    n = config.orchestration.max_reflection_iterations
    evaluator = _Evaluator([EvaluationResult(False, 0.25, "below_threshold")] * (n + 1))
    svc = _build(retriever, evaluator, _Rewriter(["a"] * n), _Detector(), _LLM())
    answer = svc.run("离职补偿金怎么算")
    assert answer.code == 21001
    assert answer.text == FALLBACK_TEXT
    assert answer.iterations == n


def test_clarify_21003_when_low_scores_with_candidates():
    """A2：检索有候选但连续低分 → 21003 主动澄清（need_clarify=True）。"""
    retriever = _Retriever([_scored("不相关内容")])
    config = load_config()
    n = config.orchestration.max_reflection_iterations
    evaluator = _Evaluator([EvaluationResult(False, 0.25, "below_threshold")] * (n + 1))
    svc = _build(retriever, evaluator, _Rewriter(["a"] * n), _Detector(), _LLM())
    answer = svc.run("年假怎么申请")
    assert answer.code == 21003
    assert answer.need_clarify is True
    assert answer.text, "澄清分支必须返回澄清问句（Stub LLM 返回固定文本）"
    # 改写无变化后触发换路（13.3.3-1：BM25-only + 向量-only），检索次数随轮次增加
    assert len(retriever.calls) >= n + 1
    assert all(c["filters"] == {} for c in retriever.calls)


def test_request_timeout_raises_21002():
    """整体超时预算：检索过慢 → RequestTimeoutError（对外 21002）。"""
    config = load_config()
    config.orchestration.request_timeout_seconds = 0.05

    class _SlowRetriever(_Retriever):
        def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
            time.sleep(0.2)
            return self.chunks[:k]

    retriever = _SlowRetriever([_scored("年假申请")])
    svc = _build(retriever, _Evaluator([EvaluationResult(True, 0.9, "ok")]),
                 _Rewriter([]), _Detector(), _LLM(), config)
    with pytest.raises(RequestTimeoutError) as ei:
        svc.run("年假怎么申请")
    assert ei.value.code == 21002


def test_search_budget_per_iteration():
    """检索轮次预算 search_timeout_seconds（每轮独立）。"""
    config = load_config()
    config.orchestration.request_timeout_seconds = 30
    config.orchestration.search_timeout_seconds = 0.05

    class _SlowRetriever(_Retriever):
        def retrieve(self, query, k, filters=None, path="hybrid", collection=None):
            time.sleep(0.15)
            return self.chunks[:k]

    svc = _build(_SlowRetriever([_scored("x")]),
                 _Evaluator([EvaluationResult(True, 0.9, "ok")]),
                 _Rewriter([]), _Detector(), _LLM(), config)
    with pytest.raises(RequestTimeoutError) as ei:
        svc.run("年假怎么申请")
    assert ei.value.code == 21002


def test_system_and_user_prompt_shape():
    """5.7：system+user 双 prompt，上下文编号注入。"""
    retriever = _Retriever([_scored("年假需提前三天申请"), _scored("加班可调休")])
    evaluator = _Evaluator([EvaluationResult(True, 0.9, "ok")])
    llm = _LLM()
    svc = _build(retriever, evaluator, _Rewriter([]), _Detector(), llm)
    svc.run("年假怎么申请")
    roles = [m["role"] for m in llm.messages]
    assert roles == ["system", "user"]
    assert "1. 年假需提前三天申请" in llm.messages[1]["content"]
    assert "2. 加班可调休" in llm.messages[1]["content"]
    assert "问题：年假怎么申请" in llm.messages[1]["content"]


def test_llm_none_raises_30001():
    """llm 未装配 → LLMUnavailableError（30001）。"""
    retriever = _Retriever([_scored("年假申请")])
    evaluator = _Evaluator([EvaluationResult(True, 0.9, "ok")])
    svc = _build(retriever, evaluator, _Rewriter([]), _Detector(), llm=None)
    from app.core.errors import LLMUnavailableError

    with pytest.raises(LLMUnavailableError) as ei:
        svc.run("年假怎么申请")
    assert ei.value.code == 30001


def test_top_k_capped_by_retriever_config():
    """6.4：top_k 受 retriever.top_k 上限约束。"""
    retriever = _Retriever([_scored(f"内容{i}") for i in range(10)])
    evaluator = _Evaluator([EvaluationResult(True, 0.9, "ok")])
    config = load_config()
    config.retriever.top_k = 5
    svc = _build(retriever, evaluator, _Rewriter([]), _Detector(), _LLM(), config)
    svc.run("年假怎么申请", top_k=999)
    assert retriever.calls[-1]["k"] == 5, "top_k 超上限被裁剪到 retriever.top_k"


def test_filters_pass_through_loop():
    """filters 贯穿整个反思循环（改写后仍应用）。"""
    retriever = _Retriever([_scored("年假申请")])
    evaluator = _Evaluator([
        EvaluationResult(False, 0.2, "below_threshold"),
        EvaluationResult(True, 0.9, "ok"),
    ])
    svc = _build(retriever, evaluator, _Rewriter(["年假"]), _Detector(), _LLM())
    svc.run("年假怎么申请", filters={"metadata": {"kind": "md"}})
    assert all(c["filters"] == {"metadata": {"kind": "md"}} for c in retriever.calls)


def test_collection_passes_through():
    """13.5：collection 由路由按 kb_id 解析后贯穿检索（编排层不再限制单库）。"""
    retriever = _Retriever([_scored("年假")])
    evaluator = _Evaluator([EvaluationResult(True, 0.9, "ok")])
    svc = _build(retriever, evaluator, _Rewriter([]), _Detector(), _LLM())
    svc.run("年假怎么申请", collection="docs")
    assert retriever.calls, "必须执行检索"
    assert all(c["collection"] == "docs" for c in retriever.calls), "collection 必须贯穿检索"


def test_fallback_capture_contexts():
    """P0：兜底路径也应返回完整上下文（供质量评测判断检索证据）。"""
    cfg = load_config()
    cfg.orchestration.hopeless_shortcut = True
    retriever = _Retriever([_scored("无关内容", source="公告.md")])
    svc = _build(retriever, _Evaluator([EvaluationResult(False, 0.0, "below_threshold")]),
                 _Rewriter([]), _Detector(), _LLM(), config=cfg)
    answer = svc.run("域外问题", capture_contexts=True)
    assert answer.code == 21001
    assert answer.contexts and answer.contexts[0]["text"] == "无关内容"
    assert answer.contexts[0]["filename"] == "公告.md"


def test_reflection_limit_fallback_capture_contexts():
    """P0：反思上限兜底同样携带检索上下文（供质量评测判断检索证据）。"""
    cfg = load_config()
    cfg.orchestration.max_reflection_iterations = 1
    cfg.orchestration.hopeless_shortcut = False
    retriever = _Retriever([_scored("无关内容", source="公告.md")])
    svc = _build(retriever, _Evaluator([
        EvaluationResult(False, 0.1, "below_threshold"),
        EvaluationResult(False, 0.1, "below_threshold"),
    ]), _Rewriter(["年假申请"]), _Detector(), _LLM(), config=cfg)
    answer = svc.run("年假怎么申请", capture_contexts=True, allow_clarify=False)
    assert answer.code == 21001
    assert answer.contexts and answer.contexts[0]["filename"] == "公告.md"


def test_capture_contexts_opt_in():
    """P0：默认不返回完整上下文；capture_contexts=True 时返回不截断的评测上下文。"""
    retriever = _Retriever([_scored("年假需提前申请", source="员工手册.md")])
    svc = _build(retriever, _Evaluator([EvaluationResult(True, 0.9, "ok")]),
                 _Rewriter([]), _Detector(), _LLM())
    plain = svc.run("年假怎么申请")
    assert plain.contexts == [], "默认不填充，避免响应膨胀"

    svc = _build(retriever, _Evaluator([EvaluationResult(True, 0.9, "ok")]),
                 _Rewriter([]), _Detector(), _LLM())
    captured = svc.run("年假怎么申请", capture_contexts=True)
    assert captured.contexts[0]["text"] == "年假需提前申请"
    assert captured.contexts[0]["filename"] == "员工手册.md"
    assert captured.contexts[0]["chunk_id"]
def test_capture_contexts_export():
    """评测出口回归（13.6）：capture_contexts=True 回传完整上下文；默认关、不影响响应体积。

    背景：基线快照的 rageval 已按该参数取评测上下文，但 run() 曾缺此形参，
    导致干净检出后 make eval 直接 TypeError —— 此用例锁死该契约。
    """
    retriever = _Retriever([_scored("年假需提前 3 个工作日申请", source="员工手册.md")])
    evaluator = _Evaluator([EvaluationResult(True, 0.9, "ok"),
                            EvaluationResult(True, 0.9, "ok")])   # 两次 run 各评估一次
    svc = _build(retriever, evaluator, _Rewriter([]), _Detector(), _LLM())

    default_answer = svc.run("年假怎么申请")
    assert default_answer.contexts == [], "默认不导出上下文（HTTP 响应体积不变）"

    export_answer = svc.run("年假怎么申请", capture_contexts=True)
    assert export_answer.code == 0
    assert len(export_answer.contexts) == 1
    item = export_answer.contexts[0]
    assert item["chunk_id"], "评测上下文需带 chunk_id 便于归因"
    assert item["filename"] == "员工手册.md"
    assert item["text"] == "年假需提前 3 个工作日申请"
    assert 0.0 <= item["score"] <= 1.0
