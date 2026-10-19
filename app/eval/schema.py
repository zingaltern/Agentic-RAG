"""评测数据集统一 schema（P0）：加载、校验、旧字段兼容。

目标：让 `tests/eval` / `tests/review` / `tests/eval_suite` / 新质量集
能用同一套代码消费；旧字段（golden/golden_answer/expected_doc）自动归一。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# 允许的用例类型（对外部集也保留 direct/no_answer 子集）
CASE_TYPES = {"direct", "rewrite", "table", "multi_doc", "no_answer", "no_invent",
              "spoken", "multi_turn", "injection", "contract", "isolation", "performance"}
# 旧集中文 type → 统一英文 type（tests/eval_suite）
TYPE_ALIASES = {
    "口语": "spoken", "表格": "table", "多文档": "multi_doc",
    "契约": "contract", "隔离": "isolation", "注入": "injection", "性能": "performance",
}
EXPECTED_BEHAVIORS = {"answer", "fallback", "clarify", "reject", ""}
# 旧集取值 → 统一取值（兼容 tests/eval 的 answer_found/fallback_21001/no_invention）
BEHAVIOR_ALIASES = {
    "answer_found": "answer",
    "fallback_21001": "fallback",
    "no_answer": "fallback",
    "no_invention": "reject",
}


@dataclass
class EvalCase:
    """统一评测用例。"""

    id: str
    query: str
    type: str = "direct"
    variant: str = ""
    kb_id: str = "default"
    split: str = "dev"
    source: str = "synthetic"
    expected_sources: list[str] = field(default_factory=list)
    reference_answer: str = ""
    reference_claims: list[str] = field(default_factory=list)
    reference_evidence: list[dict] = field(default_factory=list)
    expected_behavior: str = "answer"
    must_include: list[str] = field(default_factory=list)
    must_not_include: list[str] = field(default_factory=list)
    notes: str = ""

    @property
    def gold_evidence_ids(self) -> list[str]:
        """chunk 级证据标识：只有明确 chunk_id（或字符串 id）才返回。

        只有 filename/text 的旧证据不冒充 chunk_id——那时 IR 指标回退到
        `expected_sources` 文件名级，避免与检索返回的 chunk_id 错误比较。
        """
        ids: list[str] = []
        for item in self.reference_evidence:
            if isinstance(item, dict):
                cid = item.get("chunk_id") or item.get("id")
                if cid:
                    ids.append(str(cid))
            elif isinstance(item, str):
                ids.append(item)
        return ids


def _as_list(value: object) -> list:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def case_from_dict(raw: dict) -> EvalCase:
    """旧 schema 兼容映射：golden/golden_answer → reference_answer。"""
    reference = raw.get("reference_answer") or raw.get("golden_answer") or raw.get("golden") or ""
    evidence = _as_list(raw.get("reference_evidence") or raw.get("evidence"))
    return EvalCase(
        id=str(raw.get("id", "")),
        query=str(raw.get("query", "")),
        type=TYPE_ALIASES.get(str(raw.get("type", "direct")), str(raw.get("type", "direct"))),
        variant=str(raw.get("variant", "")),
        kb_id=str(raw.get("kb_id", "default") or "default"),
        split=str(raw.get("split", "dev")),
        source=str(raw.get("source", "synthetic")),
        expected_sources=[str(x) for x in _as_list(raw.get("expected_sources") or raw.get("expected_doc"))],
        reference_answer=str(reference),
        reference_claims=[str(x) for x in _as_list(raw.get("reference_claims"))],
        reference_evidence=evidence,
        expected_behavior=BEHAVIOR_ALIASES.get(
            str(raw.get("expected_behavior", "answer") or "answer"),
            str(raw.get("expected_behavior", "answer") or "answer")),
        must_include=[str(x) for x in _as_list(raw.get("must_include") or raw.get("keywords"))],
        must_not_include=[str(x) for x in _as_list(raw.get("must_not_include") or raw.get("forbidden"))],
        notes=str(raw.get("note") or raw.get("notes") or ""),
    )


def load_cases(path: Path | str) -> list[EvalCase]:
    """加载 JSONL（每行一条）或 JSON（数组 / {qa_pairs: []}）。"""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        raw = json.loads(text)
        rows = raw.get("qa_pairs") if isinstance(raw, dict) else raw
    if not isinstance(rows, list):
        raise ValueError(f"qa_pairs 格式不符：{p}")
    return [case_from_dict(row) for row in rows]


def validate_case(case: EvalCase, require_reference: bool = False) -> list[str]:
    """单条校验：返回错误信息列表（空列表=合法）。

    `require_reference=False`（默认）允许 review/complex 这类无标准答案的外部/压力集；
    质量门禁数据集应显式传 True，或由调用方单独统计缺失数。
    """
    errors: list[str] = []
    if not case.id:
        errors.append("缺少 id")
    if not case.query and case.type not in ("contract",):
        errors.append(f"{case.id or '<no-id>'} 缺少 query")
    if case.type not in CASE_TYPES:
        errors.append(f"{case.id} 未知 type: {case.type}")
    if case.expected_behavior not in EXPECTED_BEHAVIORS:
        errors.append(f"{case.id} 未知 expected_behavior: {case.expected_behavior}")
    if case.type in ("direct", "rewrite", "table", "multi_doc"):
        if not case.expected_sources:
            errors.append(f"{case.id} 有答案类用例缺少 expected_sources")
        if require_reference and not case.reference_answer:
            errors.append(f"{case.id} 有答案类用例缺少 reference_answer/golden_answer")
    return errors


def validate_cases(cases: list[EvalCase], require_reference: bool = False) -> list[str]:
    """批量校验：返回错误列表；重复 id 也报错。默认不强制参考答案。"""
    errors: list[str] = []
    seen: set[str] = set()
    for case in cases:
        errors.extend(validate_case(case, require_reference=require_reference))
        if case.id in seen:
            errors.append(f"重复 id: {case.id}")
        seen.add(case.id)
    return errors
