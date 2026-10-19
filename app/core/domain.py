"""核心数据模型（DEVELOPMENT.md 第 2 章）。"""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Chunk:
    """分块单元：父块 parent_id=None；子块 parent_id=父块 chunk_id。"""

    chunk_id: str                 # sha256(document_id + ":" + str(order))[:16]
    document_id: str
    text: str
    order: int
    parent_id: str | None         # 子块 → 父块；父块为 None
    metadata: dict[str, Any]      # 保留字段见下，勿改键名


@dataclass
class ScoredChunk:
    """带来源标注的检索结果。"""

    chunk: Chunk
    score: float
    notion: str = "rrf"           # "bm25"/"vector"/"rrf"/"rerank"


@dataclass
class Document:
    """解析产物：text 为归一化 UTF-8 纯文本（5.1），metadata 见保留字段。"""

    document_id: str
    filename: str
    title: str
    content_hash: str             # sha256(文件原始字节)
    text: str = ""                # 解析出的归一化 UTF-8 纯文本（5.1 需要）
    metadata: dict[str, Any] = field(default_factory=dict)  # source/kind/page/author 等
    chunks: list[Chunk] = field(default_factory=list)


@dataclass
class Job:
    """上传入库任务（6.3）。"""

    job_id: str
    filename: str
    status: str                   # pending | running | success | failed
    document_id: str | None = None
    chunk_count: int = 0
    error_code: str | None = None
    kb_id: str | None = None      # 13.5：所属知识库（缺省 default）
    created_at: float = 0.0
    updated_at: float = 0.0
    stage_timings: dict = field(default_factory=dict)   # P2-7：阶段耗时 ms（parse/clean/chunk/embed/index）
    replaced_document_id: str | None = None  # V5.2：增量更新替换掉的旧版本 document_id


@dataclass
class EvaluationResult:
    """可达性评估结果（5.5）。"""

    passed: bool
    score: float
    reason: str                   # ok | below_threshold | empty_context | query_too_long


@dataclass
class DetectionResult:
    """幻觉检测结果（5.8）。"""

    verified: bool
    suspected: list[str]          # 未找到支撑的句子
    verified_rate: float          # 已证句子 / 总句子，0~1


# Chunk.metadata 保留字段（一致，勿改键名）
#   source: 原始文件名       page: 页码(int/None)
#   author: 作者(str/None)   kind: 文件类型后缀，如 "pdf"
