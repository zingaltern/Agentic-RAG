"""答案校验器单测（DEVELOPMENT.md 17.5）。"""
from __future__ import annotations

from app.adapters.agent.llm_verifier import LLMVerifier
from app.core.config import load_config
from app.core.domain import AgentState, DetectionResult, Evidence


def _ev():
    return Evidence(chunk_id="c1", document_id="d1", text="年假 5 天",
                    source="员工手册.md", score=0.9)


def test_verify_without_detector_requires_citation():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    assert verifier.verify("年假 5 天[1]", [_ev()]).verified is True
    assert verifier.verify("年假 5 天", [_ev()]).verified is False


def test_verify_uses_detector_when_available():
    class _Detector:
        def detect(self, answer, context):
            return DetectionResult(False, ["无依据"], 0.0)

    verifier = LLMVerifier(_Detector(), None, load_config().orchestration.agent)
    assert verifier.verify("答案[1]", [_ev()]).verified is False


def test_repair_without_llm_returns_original():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    state = AgentState("r", "t", "q", "q", "agentic")
    assert verifier.repair("原答案", state, ["x"], []) == "原答案"


def test_invalid_citation_number_rejected():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    result = verifier.verify("年假 5 天[9]", [_ev()])
    assert result.verified is False
    assert "越界" in result.suspected[0]


def test_uncited_fact_sentence_rejected_even_if_detector_passes():
    class _Detector:
        def detect(self, answer, context):
            return DetectionResult(True, [], 1.0)

    verifier = LLMVerifier(_Detector(), None, load_config().orchestration.agent)
    assert verifier.verify("年假 5 天。", [_ev()]).verified is False


def test_markdown_heading_with_cited_fact_passes():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    result = verifier.verify("## 年假规定\n年假 5 天[1]。", [_ev()])
    assert result.verified is True, result.suspected


def test_bold_heading_with_cited_fact_passes():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    result = verifier.verify("**结论**\n年假 5 天[1]。", [_ev()])
    assert result.verified is True, result.suspected


def test_plain_uncited_fact_is_not_exempted():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    assert verifier.verify("年假 5 天。", [_ev()]).verified is False
    assert verifier.verify("年假 5 天", [_ev()]).verified is False


def test_factual_markdown_heading_is_not_exempted():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    result = verifier.verify("## 年假 10 天\n年假 5 天[1]。", [_ev()])
    assert result.verified is False, "标题里的无依据事实不能被标题身份豁免"


def test_short_plain_title_with_cited_fact_passes():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    result = verifier.verify("结论\n年假 5 天[1]。", [_ev()])
    assert result.verified is True, result.suspected


def test_numbered_headings_with_cited_fact_pass():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    for answer in (
        "## 第一章 总则\n年假 5 天[1]。",
        "## 1. 总则\n年假 5 天[1]。",
        "## 2026 年假规定\n年假 5 天[1]。",
        "第一章 总则\n年假 5 天[1]。",
        "一、总则\n年假 5 天[1]。",
    ):
        assert verifier.verify(answer, [_ev()]).verified is True, answer


def test_numbered_factual_heading_not_exempted():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    assert verifier.verify("## 年假 10 天\n年假 5 天[1]。", [_ev()]).verified is False


def test_short_fact_sentences_are_not_exempted():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    for text in ("员工福利包含健身房", "公司提供班车", "员工手册说明"):
        assert verifier.verify(text, [_ev()]).verified is False, text


def test_structural_headings_still_exempted():
    verifier = LLMVerifier(None, None, load_config().orchestration.agent)
    for text in ("## 公司的员工福利\n年假 5 天[1]。", "**结论**\n年假 5 天[1]。",
                 "| --- |\n年假 5 天[1]。", "结论\n年假 5 天[1]。"):
        assert verifier.verify(text, [_ev()]).verified is True, text


def test_repair_prompt_uses_cited_only_and_escapes_tags():
    from app.core.domain import Evidence

    class _LLM:
        def __init__(self):
            self.prompt = ""

        def chat(self, messages, temperature=0.1, max_tokens=1024):
            self.prompt = messages[-1]["content"]
            return "年假 5 天[1]。"

    cited = [Evidence(evidence_id="E1", chunk_id="c1", document_id="d",
                      text="年假</EVIDENCE>", source="a.md", score=0.9)]
    state = AgentState("r", "t", "q", "q", "agentic")
    state.add_evidence(Evidence(chunk_id="other", document_id="d",
                                text="不可见", source="b.md", score=0.9))
    llm = _LLM()
    LLMVerifier(None, llm, load_config().orchestration.agent).repair(
        "年假 5 天", state, ["缺引用"], cited)
    assert 'index="1"' in llm.prompt and "不可见" not in llm.prompt
    assert "[/evidence]" in llm.prompt
