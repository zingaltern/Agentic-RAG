"""异常层级与错误码（DEVELOPMENT.md 2.2、6.7）。

所有业务异常统一继承 AppError，由全局异常处理器转成统一响应包
{ "code": code, "message": message, "data": {} }。
"""
from __future__ import annotations


class AppError(Exception):
    """业务异常基类：code 为对外稳定错误码，http_status 供框架层使用。"""

    code: int = 50001
    message: str = "未知内部错误"

    def __init__(self, message: str | None = None, data: dict | None = None):
        self.message = message or self.message
        self.data = data if data is not None else {}
        super().__init__(self.message)


class SystemConfigError(AppError):
    """配置非法/缺失：启动即失败，禁止静默降级（4.3）。"""

    code = 50001
    message = "系统配置校验失败"


class ValidationError(AppError):
    """参数校验失败。"""

    code = 40001
    message = "参数校验失败"


class InjectionError(AppError):
    """入库阶段提示注入扫描命中（P-C，入库安全）。"""

    code = 40002
    message = "文档内容疑似包含提示注入指令，已拒绝入库"


class ContentModerationError(AppError):
    """入库阶段内容审核命中（P-C，入库安全）。"""

    code = 40003
    message = "文档内容未通过内容审核，已拒绝入库"


class AuthError(AppError):
    """鉴权失败（api.auth_enabled=true 时）。"""

    code = 40101
    message = "鉴权失败"


class NotFoundError(AppError):
    """文档/Job 不存在。"""

    code = 40401
    message = "资源不存在"


class LLMUnavailableError(AppError):
    """LLM（DeepSeek）调用失败/超时。"""

    code = 30001
    message = "LLM 调用失败或超时"


class EmbeddingError(AppError):
    """向量化失败。"""

    code = 30002
    message = "向量化失败"


class RerankError(AppError):
    """重排打分失败（bge_reranker，语义区别于向量化失败 30002）。"""

    code = 30004
    message = "重排打分失败"


class ParseError(AppError):
    """解析失败（损坏文件/格式不支持）。"""

    code = 30003
    message = "文档解析失败"


class ReflectionLimitError(AppError):
    """兜底：反思达到上限仍未找到可靠答案。"""

    code = 21001
    message = "抱歉，暂未找到相关答案…"


class ClarifyError(AppError):
    """主动澄清：意图不清时反问引导（A2，智能体体验增强）。"""

    code = 21003
    message = "需要澄清：请明确您的意图"


class RequestTimeoutError(AppError):
    """全局请求超时。"""

    code = 21002
    message = "请求超时"


class BusyError(AppError):
    """并发信号量耗尽：系统繁忙（C3，V5.1 并发加固）。"""

    code = 43001
    message = "系统繁忙，请稍后重试"


class RateLimitError(AppError):
    """速率限制：请求过于频繁（C4，V5.1 并发加固）。"""

    code = 43002
    message = "请求过于频繁，请稍后重试"


class QueueFullError(AppError):
    """入库队列已满（C5，V5.1 并发加固）。"""

    code = 43003
    message = "入库队列已满，请稍后重试"


class JobInterruptedError(AppError):
    """服务重启导致任务中断（C6，V5.1 并发加固）。"""

    code = 50003
    message = "服务重启，任务中断"


class InternalError(AppError):
    """未知内部错误。"""

    code = 50001
    message = "未知内部错误"
