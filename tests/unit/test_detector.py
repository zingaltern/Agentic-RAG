"""幻觉检测单测（DEVELOPMENT.md 5.8、8.1：真支撑/假支撑/无引用/空串）。"""
from __future__ import annotations

from app.adapters.detector.citation_tracing import CitationTracingDetector, char_bigrams
from app.core.domain import Chunk, ScoredChunk


def _ctx(texts: list[str]) -> list[ScoredChunk]:
    return [ScoredChunk(
        chunk=Chunk(chunk_id=f"c{i}", document_id="d", text=t, order=i, parent_id=None, metadata={}),
        score=1.0, notion="rrf") for i, t in enumerate(texts, 1)]


# 长句使 [N] 引用噪音对 2-gram Jaccard 的影响被稀释（验证 sim≈0.87 ≥ 0.8）
_SUPPORTED_SENTENCE = "年假申请需提前三个工作日通过OA系统提交经部门负责人审批后生效[1]。"
_SUPPORTED_CTX = "年假申请需提前三个工作日通过OA系统提交经部门负责人审批后生效。"


def test_char_bigrams():
    assert char_bigrams("") == set()
    assert char_bigrams("ab") == {"ab"}
    assert char_bigrams("abc") == {"ab", "bc"}


def test_true_support():
    """真支撑：句子与引用块高度相似 → verified。"""
    detector = CitationTracingDetector(similarity_threshold=0.8)
    result = detector.detect(_SUPPORTED_SENTENCE, _ctx([_SUPPORTED_CTX]))
    assert result.verified is True
    assert result.suspected == []
    assert result.verified_rate == 1.0


def test_false_support():
    """假支撑：引用块内容完全不同 → suspected。"""
    text = "离职补偿金为六个月工资[1]。"
    detector = CitationTracingDetector(similarity_threshold=0.8)
    result = detector.detect(text, _ctx(["年假申请需提前三个工作日提交。"]))
    assert result.verified is False
    assert len(result.suspected) == 1
    assert "离职补偿金" in result.suspected[0]
    assert result.verified_rate == 0.0


def test_no_citation_sentences_suspected():
    """无引用整句 → suspected（5.8 第 3 条）；有引用且支撑的句子已证。"""
    text = "年假需提前三天申请。" + "加班可申请调休，调休额度需当季度内使用完毕[1]。"
    detector = CitationTracingDetector(similarity_threshold=0.8)
    result = detector.detect(text, _ctx(["加班可申请调休，调休额度需当季度内使用完毕。"]))
    assert result.verified is False
    assert len(result.suspected) == 1
    assert "年假需提前三天申请" in result.suspected[0]
    assert result.verified_rate == 0.5


def test_empty_answer():
    """空串：无待证句子 → verified（8.1 矩阵）。"""
    detector = CitationTracingDetector()
    result = detector.detect("", _ctx(["内容"]))
    assert result.verified is True
    assert result.suspected == []
    assert result.verified_rate == 1.0
    result2 = detector.detect("   ", _ctx(["内容"]))
    assert result2.verified is True


def test_partial_verified_rate():
    """两句一句已证一句未证 → verified_rate=0.5。"""
    text = _SUPPORTED_SENTENCE + "公司为员工提供免费健身房和咖啡机[2]。"
    detector = CitationTracingDetector(similarity_threshold=0.8)
    result = detector.detect(text, _ctx([_SUPPORTED_CTX, "其他无关内容。"]))
    assert result.verified is False
    assert result.verified_rate == 0.5
    assert len(result.suspected) == 1


def test_out_of_range_citation():
    """引用编号超出上下文范围 → 无法验证 → suspected。"""
    text = "员工手册第三条规定[9]。"
    detector = CitationTracingDetector()
    result = detector.detect(text, _ctx(["只有一块内容。"]))
    assert result.verified is False


def test_threshold_sensitivity():
    """阈值越高越严格：近全文一致（sim≈0.87）在 0.5 通过、0.9 不通过。"""
    ctx = _ctx([_SUPPORTED_CTX])
    loose = CitationTracingDetector(similarity_threshold=0.5)
    strict = CitationTracingDetector(similarity_threshold=0.9)
    assert loose.detect(_SUPPORTED_SENTENCE, ctx).verified is True
    assert strict.detect(_SUPPORTED_SENTENCE, ctx).verified is False
