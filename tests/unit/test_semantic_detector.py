"""语义幻觉检测 v2 单测（DEVELOPMENT.md 13.4）：4 组规范用例。"""
from __future__ import annotations

import pytest

from app.adapters.detector.semantic_verifier import SemanticVerifierDetector
from app.core.domain import Chunk, ScoredChunk
from app.core.errors import EmbeddingError
from tests.unit.fakes import StubEmbedding


def _ctx(texts: list[str]) -> list[ScoredChunk]:
    return [ScoredChunk(
        chunk=Chunk(chunk_id=f"c{i}", document_id="d", text=t, order=i, parent_id=None, metadata={}),
        score=1.0, notion="rrf") for i, t in enumerate(texts, 1)]


def _detector(**kw):
    return SemanticVerifierDetector(embedding=StubEmbedding(dimension=128), **kw)


def test_true_support():
    """真支撑：句子与引用块文本一致 → 嵌入余弦≈1 ≥ 0.6 → verified。"""
    text = "年假需提前三个工作日通过OA系统提交申请[1]。"
    result = _detector().detect(text, _ctx([text.replace("[1]", "")]))
    assert result.verified is True
    assert result.suspected == []
    assert result.verified_rate == 1.0


def test_false_support():
    """假支撑：引用块内容完全不同 → suspected。"""
    text = "离职补偿金为六个月工资[1]。"
    result = _detector().detect(text, _ctx(["年假申请需提前三个工作日提交。"]))
    assert result.verified is False
    assert len(result.suspected) == 1
    assert result.verified_rate == 0.0


def test_no_citation_sentences_suspected():
    """无引用整句 → suspected（规则保留）；有引用且支撑的句子已证。"""
    text = "年假需提前三天申请。" + "加班可申请调休[1]。"
    result = _detector().detect(text, _ctx(["加班可申请调休。"]))
    assert result.verified is False
    assert len(result.suspected) == 1
    assert "年假需提前三天申请" in result.suspected[0]
    assert result.verified_rate == 0.5


def test_empty_answer():
    result = _detector().detect("", _ctx(["内容"]))
    assert result.verified is True
    assert result.verified_rate == 1.0


def test_threshold_sensitivity():
    """相似但不完全相同的句子：0.1 通过、0.99 不通过（stub 哈希下近似句余弦中等）。"""
    text = "年假需提前三天申请[1]。"
    ctx = _ctx(["年假需提前三天申请，需经部门负责人审批后生效。"])
    loose = _detector(semantic_similarity_threshold=0.1)
    strict = _detector(semantic_similarity_threshold=0.99)
    assert loose.detect(text, ctx).verified is True
    assert strict.detect(text, ctx).verified is False


_LONG_CTX = """# 员工手册（Demo 知识库）
## 年假
- 入职满 1 年：年假 5 天。
- 入职满 2 年及以上：年假 10 天；最高累计 20 天。
- 申请年假需提前 3 个工作日，通过 OA 系统提交，经部门负责人审批后生效。
## 请假
- 事假需提前 2 天向直属主管申请。
- 病假：返岗后 3 个工作日内补交医院诊断证明或医嘱；病假期间工资按公司薪酬制度发放。
## 报销
- 报销凭证：出差交通、住宿费用实报实销，需在出差结束后 3 个工作日内于 OA 系统提交发票。
- 普通费用（办公用品等）报销：财务审核通过后，5 个工作日内打款至员工工资卡。
## 出差补贴
- 出差至一线城市（北京、上海、广州、深圳、杭州）：伙食补贴 150 元/天。
- 其他城市：补贴 100 元/天。
- 住宿凭发票实报实销。
## 加班与调休
- 加班需提前登记，可选择申请调休；调休额度须在当季度内使用完毕。
## 培训
- 公司每年为每位员工提供不低于 20 小时内部培训课程，覆盖制度、技能与团队协作。"""


def test_trailing_citation_after_period_is_verified():
    """回答把 [1] 写在句号之后时，引用必须归入前句，不能切成独立 ``[1]`` 句。"""
    answer = "入职满 2 年及以上：年假 10 天；最高累计 20 天。[1]"
    result = _detector().detect(answer, _ctx([_LONG_CTX]))
    assert result.verified is True
    assert result.suspected == []
    assert result.verified_rate == 1.0


def test_sentence_level_context_avoids_long_chunk_dilution():
    """长父块不能让短句匹配失败：应命中块内对应句级片段。"""
    answer = "入职满 2 年及以上：年假 10 天；最高累计 20 天。[1]"
    result = _detector().detect(answer, _ctx([_LONG_CTX]))
    assert result.verified is True


class _HighSemanticLowLexicalEmbedding:
    """语义上把答案与无关片段都映射到同一向量，词面却几乎不重合。"""

    dimension = 2

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0] if ("编造" in t or "语义占位" in t) else [0.0, 1.0] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 0.0]

    def ready(self) -> bool:
        return True


def test_semantic_high_but_lexical_low_is_suspected():
    """语义余弦高但词面不支撑（如泛化模板）→ suspected，防假阳性。"""
    detector = SemanticVerifierDetector(
        embedding=_HighSemanticLowLexicalEmbedding(),
        semantic_similarity_threshold=0.6,
        lexical_overlap_min=0.3,
    )
    result = detector.detect("编造内容[1]。", _ctx(["语义占位内容。"]))
    assert result.verified is False
    assert result.verified_rate == 0.0


def test_missing_embedding_raises():
    detector = SemanticVerifierDetector(embedding=None)
    with pytest.raises(EmbeddingError, match="embedding"):
        detector.detect("年假[1]。", _ctx(["年假"]))
