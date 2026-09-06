"""模块抽象接口（DEVELOPMENT.md 第 3 章）。

所有实现位于 app/adapters/，接口是运行契约，实现必须满足签名与语义。
"""
from abc import ABC, abstractmethod
from typing import Iterable

from app.core.domain import Chunk, DetectionResult, EvaluationResult, ScoredChunk


class VectorEngine(ABC):
    """嵌入引擎（3.1）。"""

    @property
    @abstractmethod
    def dimension(self) -> int: ...

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文档文本（无查询指令前缀）。必须保持 texts 与返回长度一致。"""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """向量化查询（嵌入模型如需要，需自动加查询指令前缀，见 5.3）。"""


class VectorStore(ABC):
    """向量库（3.2）。"""

    @abstractmethod
    def add_chunks(self, chunks: Iterable[tuple[Chunk, list[float]]],
                   collection: str = "kb") -> None:
        """批量写入 (块, 向量)，collection 存在则追加。"""

    @abstractmethod
    def search(self, query_vector: list[float], k: int,
               filters: dict | None = None, collection: str = "kb") -> list[ScoredChunk]:
        """余弦相似度检索 Top-k，按 score 降序。filter 支持 metadata[key]==value 等值过滤。"""

    @abstractmethod
    def delete_document(self, document_id: str, collection: str = "kb") -> int:
        """级联删除该文档所有块；返回删除条数。"""

    @abstractmethod
    def count(self, collection: str = "kb") -> int: ...

    @abstractmethod
    def drop_collection(self, collection: str = "kb") -> None: ...


class Retriever(ABC):
    """混合检索器（3.3）。"""

    @abstractmethod
    def retrieve(self, query: str, k: int, filters: dict | None = None) -> list[ScoredChunk]:
        """返回降序排列的 k 条候选，且带 bm25/vector 混合评分（见 5.4）。
        filters: 检索过滤条件，形如 {"metadata": {"department": "HR"}}，
                 透传给 VectorStore.search（全文检索侧也按同条件过滤）。"""


class Reranker(ABC):
    """精排器（3.4）。"""

    @abstractmethod
    def rerank(self, query: str, chunks: list[ScoredChunk]) -> list[ScoredChunk]:
        """对已召回块重排序（可返回相同列表）。"""


class Evaluator(ABC):
    """可达性评估器（3.5）。"""

    @abstractmethod
    def evaluate(self, query: str, chunks: list[ScoredChunk]) -> EvaluationResult:
        """纯函数、无副作用，返回是否通过及原因。"""


class QueryRewriter(ABC):
    """查询改写器（3.6）。"""

    @abstractmethod
    def rewrite(self, query: str, feedback: EvaluationResult) -> str: ...


class QueryNormalizer(ABC):
    """查询输入标准化器（在线侧输入处理.md L1；13.12）。

    纯规则、确定性、无 LLM；输入输出均为文本，异常由实现内部消化（不得上抛）。
    """

    @abstractmethod
    def normalize(self, query: str) -> str: ...


class BoundaryDetector(ABC):
    """会话边界判定器（在线侧输入处理.md L2 4.3.2；13.12）。

    判断当前 Query 相对上一轮 Query 是否为「新话题」；
    两维判定均不可用时应保守返回 False（不丢上下文，降级不误伤）。
    """

    @abstractmethod
    def is_new_topic(self, current: str, previous: str) -> bool: ...


class Cleaner(ABC):
    """文档清洗器（解析后、分块前）：去噪/归一/质量过滤/段落级近重复去重。

    输入输出均为纯文本（Document.text），无副作用、无状态；
    由 DefaultCleaner 提供规则实现，可扩展第三方清洗器（如 OCR 前置预处理）。
    """

    @abstractmethod
    def clean(self, text: str) -> str: ...


class HallucinationDetector(ABC):
    """幻觉检测器（3.7）。"""

    @abstractmethod
    def detect(self, answer: str, context: list[ScoredChunk]) -> DetectionResult: ...


class LLM(ABC):
    """大模型客户端（3.8）。"""

    @abstractmethod
    def chat(self, messages: list[dict[str, str]],
             temperature: float = 0.1, max_tokens: int = 1024) -> str:
        """messages: [{"role":"system"...},{"role":"user"...}...]；返回 assistant 文本。"""
