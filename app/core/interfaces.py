"""模块抽象接口（DEVELOPMENT.md 第 3 章）。

所有实现位于 app/adapters/，接口是运行契约，实现必须满足签名与语义。
"""
from abc import ABC, abstractmethod
from typing import Any, Iterable

from app.core.domain import (
    AgentAction,
    AgentState,
    Budget,
    Chunk,
    DetectionResult,
    EvaluationResult,
    Evidence,
    LLMResponse,
    Plan,
    ReflectionResult,
    ScoredChunk,
    ToolCall,
    ToolContext,
    ToolResult,
)


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

    def count_tokens(self, text: str) -> int | None:
        """【可选能力 V5.2】精确 token 计数（分块目标测量用）。

        默认 None=不支持 → 分块回退 token_char_ratio 估算；实现可覆写为真实分词器计数。
        返回 None 或抛异常时调用方（chunker）一律回退估算，不阻塞入库。
        """
        return None


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
    def retrieve(self, query: str, k: int, filters: dict | None = None,
                 path: str = "hybrid", collection: str | None = None) -> list[ScoredChunk]:
        """返回降序排列的 k 条候选，且带 bm25/vector 混合评分（见 5.4）。
        filters: 检索过滤条件，形如 {"metadata": {"department": "HR"}}，
                 透传给 VectorStore.search（全文检索侧也按同条件过滤）。
        path: hybrid（默认）| bm25 | vector；collection: 多库作用域（缺省实现默认库）。
        两者为 P-D 工具化补齐的可选参，默认值保持旧行为。"""


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
    """查询改写器（3.6；P1-3.2/08 批次演进返回类型）。

    rewrite 允许返回：
    - str：单变体（rule 回退）；
    - list[str]：多变体（LLM 改写，13.3.2）；
    - list[dict]：带检索路径的多策略变体（{"text": str, "path": "hybrid|vector"}，
      08 批次：hyde 变体走 vector）。编排层按类型适配，生产者按需选择形式。
    """

    @abstractmethod
    def rewrite(self, query: str, feedback: EvaluationResult) -> str | list: ...


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
    V5.2：`kind` 为扩展名类型（如 code/json/csv），可选——实现可按类型豁免过滤；
    缺省 None 时行为与旧版一致。
    """

    @abstractmethod
    def clean(self, text: str, kind: str | None = None) -> str: ...


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


# ---------------------------------------------------------------------------
# P-D：真 Agentic 契约（DEVELOPMENT.md 17.3）
# ---------------------------------------------------------------------------


class ToolCallingLLM(LLM):
    """支持原生工具调用的 LLM（OpenAI 兼容 tools/tool_calls）。

    实现需把 `messages` 原样交给 provider（含 assistant.tool_calls / tool 消息），
    返回结构化 `LLMResponse`；JSON 解析失败由适配器抛 AgentProtocolError。
    """

    @abstractmethod
    def chat_with_tools(self, messages: list[dict[str, Any]],
                        tools: list[dict[str, Any]],
                        tool_choice: str = "auto",
                        temperature: float = 0.1,
                        max_tokens: int = 1024) -> LLMResponse: ...


class Tool(ABC):
    """只读工具契约；作用域/权限/预算由 ToolContext 注入。"""

    name: str = ""
    description: str = ""
    args_model: Any = None          # pydantic BaseModel 子类；registry 生成 JSON Schema
    timeout_ms: int = 3000
    max_result_chars: int = 4000
    side_effect: str = "none"      # none | external

    @abstractmethod
    def invoke(self, ctx: ToolContext, args: Any) -> ToolResult: ...


class Planner(ABC):
    """规划器：产出/修订 Plan，并决定每一步动作。"""

    @abstractmethod
    def plan(self, state: AgentState, budget: Budget) -> Plan | None: ...

    @abstractmethod
    def next_action(self, state: AgentState, budget: Budget) -> AgentAction: ...

    @abstractmethod
    def replan(self, state: AgentState, reflection: ReflectionResult) -> Plan | None: ...


class Reflector(ABC):
    """反思器：判断子目标是否充分、缺口与下一步。"""

    @abstractmethod
    def reflect(self, state: AgentState, result: ToolResult | None = None) -> ReflectionResult: ...

    @abstractmethod
    def sufficient(self, state: AgentState) -> bool: ...

    @abstractmethod
    def missing(self, state: AgentState) -> list[str]: ...


class Synthesizer(ABC):
    """答案合成：只使用证据账本，返回答案与实际引用证据。"""

    @abstractmethod
    def synthesize(self, state: AgentState) -> tuple[str, list[Evidence]]: ...


class Verifier(ABC):
    """答案校验：引用/幻觉检测与最小修复。"""

    @abstractmethod
    def verify(self, answer: str, cited: list[Evidence]) -> DetectionResult: ...

    @abstractmethod
    def repair(self, answer: str, state: AgentState, unsupported: list[str],
               cited: list[Evidence]) -> str: ...
