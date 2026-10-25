"""P0 统一 schema 单测：旧字段兼容、加载、校验（app/eval/schema.py）。"""
from __future__ import annotations

import json
from pathlib import Path

from app.eval.schema import case_from_dict, load_cases, validate_case, validate_cases


def test_case_from_legacy_fields():
    case = case_from_dict({
        "id": "D001", "type": "direct", "query": "交通费怎么报销",
        "expected_doc": ["报销制度.md"], "golden_answer": "凭发票实报实销",
        "expected_behavior": "answer_found", "variant": "spoken",
    })
    assert case.expected_sources == ["报销制度.md"]
    assert case.reference_answer == "凭发票实报实销"
    assert case.variant == "spoken"
    assert case.expected_behavior == "answer"


def test_legacy_type_aliases():
    assert case_from_dict({"id": "A", "type": "口语", "query": "q"}).type == "spoken"
    assert case_from_dict({"id": "B", "type": "契约", "query": ""}).type == "contract"
    assert case_from_dict({"id": "C", "type": "隔离", "query": "q"}).type == "isolation"


def test_legacy_behavior_aliases():
    assert case_from_dict({"id": "A", "query": "q", "expected_behavior": "fallback_21001"}).expected_behavior == "fallback"
    assert case_from_dict({"id": "B", "query": "q", "expected_behavior": "no_invention"}).expected_behavior == "reject"


def test_case_prefers_unified_fields():
    case = case_from_dict({
        "id": "Q1", "query": "q", "golden": "legacy", "golden_answer": "legacy2",
        "reference_answer": "new", "reference_evidence": [{"chunk_id": "c1", "text": "证据"}],
    })
    assert case.reference_answer == "new"
    assert case.gold_evidence_ids == ["c1"]


def test_validate_case_rules():
    assert validate_case(case_from_dict({"id": "A", "type": "contract", "query": ""})) == []
    errors = validate_case(case_from_dict({"id": "B", "type": "direct", "query": "q"}))
    assert any("expected_sources" in e for e in errors)
    assert not any("reference_answer" in e for e in errors), "默认不强制参考答案"
    strict = validate_case(case_from_dict({"id": "B", "type": "direct", "query": "q"}),
                           require_reference=True)
    assert any("reference_answer" in e for e in strict)
    assert any("未知 type" in e for e in validate_case(
        case_from_dict({"id": "C", "type": "unknown", "query": "q", "expected_sources": ["a"], "golden_answer": "x"})))


def test_external_sets_without_reference_are_valid():
    """review/complex 本来无标准答案：默认校验不应报 226 条错误。"""
    cases = load_cases(Path(__file__).resolve().parent.parent / "review" / "qa_pairs" / "cmrc" / "qa_pairs.jsonl")
    assert validate_cases(cases) == []


def test_load_and_validate_jsonl(tmp_path):
    path = tmp_path / "pairs.jsonl"
    rows = [
        {"id": "A", "type": "direct", "query": "q", "expected_sources": ["a.md"], "golden_answer": "x"},
        {"id": "A", "type": "direct", "query": "q2", "expected_sources": ["a.md"], "golden_answer": "y"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    cases = load_cases(path)
    assert len(cases) == 2
    errors = validate_cases(cases)
    assert any("重复 id" in e for e in errors)
