"""核心数据模型（DEVELOPMENT.md 第 2 章、第 17 章）。

P-D（2026-10-19）：新增 Agent 运行期模型（Plan/Evidence/ToolCall/ToolResult/AgentState 等）。
这些模型是纯数据对象，不依赖适配器/配置，便于单测与序列化。
"""
from dataclasses import dataclass, field
from enum import Enum
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
    error_message: str | None = None  # 用户可见失败原因（安装/导出指引；最长 500 字）
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


# ---------------------------------------------------------------------------
# P-D：Agent 运行期模型（DEVELOPMENT.md 17.3）
# ---------------------------------------------------------------------------


class AgentMode(str, Enum):
    """问答模式：reflect（旧链路）/ agentic（新路径）/ auto（规则路由）。"""

    REFLECT = "reflect"
    AGENTIC = "agentic"
    AUTO = "auto"


class TerminationReason(str, Enum):
    """Agent 终止原因（内部使用；对外仍映射到既有错误码）。"""

    ANSWERED = "answered"
    CLARIFY = "clarify"
    ABSTAIN_INSUFFICIENT = "abstain_insufficient"
    BUDGET_STEPS = "budget_steps"   # 步数/LLM 调用预算统一用此值（V1 不新增 budget_llm）
    BUDGET_TIME = "budget_time"
    POLICY_BLOCKED = "policy_blocked"
    PLAN_INVALID = "plan_invalid"
    TOOL_ERROR = "tool_error"
    PROTOCOL_ERROR = "protocol_error"
    FALLBACK_REFLECT = "fallback_reflect"
    ERROR = "error"


@dataclass
class ToolCall:
    """模型请求的一次工具调用（call_id 用于回填 tool 消息）。"""

    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """带工具调用的 LLM 响应（chat_with_tools 返回）。"""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=dict)
    reasoning: str | None = None   # 仅调试用；不向前端/日志暴露


@dataclass
class ToolContext:
    """工具执行的受控上下文：作用域/权限/预算由服务端注入，模型不可见。"""

    trace_id: str = ""
    user_id: str | None = None
    kb_id: str = "default"
    collection: str = "kb"
    filters: dict[str, Any] = field(default_factory=dict)
    deadline: float = 0.0
    allowed_tools: set[str] = field(default_factory=set)
    request_id: str = ""
    web_search: bool = False
    # 账本 evidence_id -> chunk_id；由 AgentRuntime 在每次工具调用前注入，
    # 供 kb_fetch 在模型误把 E1/E2 填进 chunk_id 时安全反查（17.4 工具契约）。
    evidence_index: dict[str, str] = field(default_factory=dict)


@dataclass
class Evidence:
    """证据账本条目：工具结果的唯一事实来源，引用与评测都消费它。"""

    evidence_id: str = ""          # E1/E2...（由账本分配；工具可先留空）
    chunk_id: str = ""
    document_id: str = ""
    text: str = ""
    source: str = ""               # 原始文件名 / 网页标题
    page: int | None = None
    section: str | None = None
    score: float = 0.0
    retrieval_query: str = ""
    tool_name: str = ""
    step: int = 0
    is_web: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    cited: bool = False

    def to_dict(self, text_limit: int | None = None) -> dict[str, Any]:
        text = self.text if text_limit is None else self.text[:text_limit]
        return {
            "evidence_id": self.evidence_id,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source": self.source,
            "page": self.page,
            "section": self.section,
            "score": round(self.score, 4),
            "tool": self.tool_name,
            "step": self.step,
            "is_web": self.is_web,
            "cited": self.cited,
            "text": text,
        }


@dataclass
class ToolResult:
    """工具执行结果：summary 给模型看，evidence 给账本/生成器看。"""

    call_id: str
    name: str
    ok: bool
    summary: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    latency_ms: int = 0
    truncated: bool = False
    retryable: bool = False

    def model_payload(self, evidence_limit: int = 5) -> dict[str, Any]:
        """给 LLM 的压缩观察：不含完整正文，避免 token 爆炸与注入面。"""
        return {
            "ok": self.ok,
            "tool": self.name,
            "summary": self.summary,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "data": self.data,
            "evidence": [
                {"id": e.evidence_id, "source": e.source, "page": e.page,
                 "section": e.section, "score": round(e.score, 4),
                 "snippet": e.text[:200]}
                for e in self.evidence[:evidence_limit]
            ],
        }


@dataclass
class AgentAction:
    """模型的一步决策：工具调用 / 最终答案 / 澄清。"""

    kind: str                      # tool | final | clarify
    tool_calls: list[ToolCall] = field(default_factory=list)
    content: str | None = None

    @property
    def is_final(self) -> bool:
        return self.kind == "final"


@dataclass
class SubGoal:
    """计划中的子目标；成功判据供反思判定。"""

    goal_id: str
    question: str
    success_criteria: str = ""
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"        # pending/running/satisfied/failed/skipped
    evidence_ids: list[str] = field(default_factory=list)
    attempts: int = 0

    def is_synthesis(self) -> bool:
        """是否为“依赖前序取证、自身不再检索”的对比/汇总子目标。

        这类目标由依赖子目标的证据满足，不应把证据从叶子目标手里抢走。
        """
        return bool(self.depends_on) and any(
            marker in self.question
            for marker in ("对比", "比较", "判断", "综合", "汇总", "总结",
                           "是否一致", "异同", "差异"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "question": self.question,
            "success_criteria": self.success_criteria,
            "depends_on": list(self.depends_on),
            "status": self.status,
            "evidence_ids": list(self.evidence_ids),
            "attempts": self.attempts,
        }


@dataclass
class Plan:
    """规划器的计划；只描述“做什么”，不包含答案或未检索到的事实。"""

    plan_id: str
    goal: str
    complexity: str = "simple"
    subgoals: list[SubGoal] = field(default_factory=list)
    initial_steps: list[dict[str, Any]] = field(default_factory=list)
    answer_requirements: dict[str, Any] = field(default_factory=dict)
    version: int = 1
    created_by: str = "llm"
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "complexity": self.complexity,
            "subgoals": [s.to_dict() for s in self.subgoals],
            "initial_steps": list(self.initial_steps),
            "answer_requirements": dict(self.answer_requirements),
            "version": self.version,
            "created_by": self.created_by,
            "rationale": self.rationale,
        }


@dataclass
class ReflectionResult:
    """反思结果：决定继续/重规划/收尾/澄清/拒答。"""

    step: int
    decision: str                  # continue/replan/answer/clarify/abstain
    subgoal_status: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "decision": self.decision,
            "subgoal_status": dict(self.subgoal_status),
            "missing": list(self.missing),
            "contradictions": list(self.contradictions),
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
        }


@dataclass
class Budget:
    """有界自主的硬预算（时间/步数/工具/LLM/上下文）。"""

    max_steps: int = 8
    max_llm_calls: int = 8
    max_tool_calls: int = 12
    max_replans: int = 2
    max_context_tokens: int = 6000
    deadline: float = 0.0          # time.time() 绝对截止时刻；0=不限


@dataclass
class TraceSpan:
    """结构化轨迹的一步；只记摘要，不记完整思维链/正文。"""

    span_id: str
    type: str                      # plan/tool/reflect/synthesize/verify/fallback
    step: int = 0
    name: str = ""
    duration_ms: int = 0
    input_summary: str = ""
    output_summary: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "type": self.type,
            "step": self.step,
            "name": self.name,
            "duration_ms": self.duration_ms,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "attributes": dict(self.attributes),
        }


@dataclass
class AgentState:
    """单次 Agent 运行的工作记忆；请求结束即释放，不跨请求/用户共享。"""

    run_id: str
    trace_id: str
    query: str
    normalized_query: str
    mode: AgentMode
    kb_id: str = "default"
    collection: str = "kb"
    filters: dict[str, Any] = field(default_factory=dict)
    user_id: str | None = None
    history_summary: str | None = None
    allowed_tools: set[str] = field(default_factory=set)
    web_search: bool = False

    plan: Plan | None = None
    subgoal_status: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, Evidence] = field(default_factory=dict)   # 保序（py3.7+ dict）
    observations: list[dict[str, Any]] = field(default_factory=list)
    reflections: list[ReflectionResult] = field(default_factory=list)

    step: int = 0
    llm_calls: int = 0
    tool_calls: int = 0
    replans: int = 0
    duplicate_steps: int = 0
    no_new_evidence_steps: int = 0
    llm_judgements: int = 0

    started_at: float = 0.0
    deadline: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    termination_reason: TerminationReason | None = None
    last_error: str | None = None
    trace: list[TraceSpan] = field(default_factory=list)

    def add_evidence(self, item: Evidence) -> tuple[Evidence, bool]:
        """按 chunk_id 去重入账；返回 (账本条目, 是否新增)。

        evidence_id 由账本统一分配（E1/E2...），保证一次请求内稳定。
        同一 chunk 重复命中时保留最高分并合并来源查询。
        """
        if not item.chunk_id:
            item.chunk_id = f"{item.document_id}:{item.source}:{item.step}"
        existing = next((e for e in self.evidence.values()
                         if e.chunk_id == item.chunk_id), None)
        if existing is not None:
            if item.score > existing.score:
                existing.score = item.score
            if len(item.text) > len(existing.text):
                existing.text = item.text
            for key in ("source", "page", "section"):
                if getattr(item, key) not in (None, ""):
                    setattr(existing, key, getattr(item, key))
            existing.metadata.update({k: v for k, v in item.metadata.items() if v is not None})
            if item.retrieval_query and item.retrieval_query not in existing.metadata.get("queries", []):
                existing.metadata.setdefault("queries", []).append(item.retrieval_query)
            # 重复命中仍要回填账本 ID：调用方（observations/agent_trace）读的是
            # result.evidence 里的原对象，不回填会得到空串 evidence_ids。
            item.evidence_id = existing.evidence_id
            return existing, False
        if item.retrieval_query:
            queries = item.metadata.setdefault("queries", [])
            if item.retrieval_query not in queries:
                queries.append(item.retrieval_query)
        item.evidence_id = f"E{len(self.evidence) + 1}"
        self.evidence[item.evidence_id] = item
        return item, True


@dataclass
class Answer:
    """编排结果（旧字段保持；P-D 新增字段均可选）。"""

    text: str
    sources: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    hallucination: DetectionResult | None = None
    debug_trace: list[dict[str, Any]] = field(default_factory=list)
    code: int = 0
    message: str = "ok"
    need_clarify: bool = False
    elapsed_ms: int = 0
    contexts: list[dict[str, Any]] = field(default_factory=list)
    # —— P-D 可选字段 ——
    mode: str = "reflect"
    agent_steps: int = 0
    plan: dict[str, Any] | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    termination_reason: str = "answered"
    usage: dict[str, Any] = field(default_factory=dict)
    agent_trace: list[dict[str, Any]] = field(default_factory=list)


# Chunk.metadata 保留字段（一致，勿改键名）
#   source: 原始文件名       page: 页码(int/None)
#   author: 作者(str/None)   kind: 文件类型后缀，如 "pdf"
