"""Provider 公共基类（DEVELOPMENT.md 13.11.2 复用性规范）。

LocalModelAdapter：本地模型"懒加载 + 探活 + 异常包装"的统一实现，
消除各 adapter 重复的 device 解析 / model 加载 / ready / is_loaded 样板。
- 子类实现 `_load_model()`（构造模型）与可选 `_post_load()`（加载后校验）；
- 模型工厂 `model_factory` 注入优先（测试/桩用）；
- 加载失败统一包装为 EmbeddingError；SystemConfigError 原样上抛（配置错误不包装）。
"""
from __future__ import annotations

from typing import Any

from app.core.errors import EmbeddingError, SystemConfigError


class LocalModelAdapter:
    def __init__(self, model_name: str, device: str = "auto",
                 batch_size: int = 32, model_factory: Any = None, **_: Any):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self._model_factory = model_factory
        self._model: Any = None

    # -- 设备与加载 ------------------------------------------------------------

    def _resolve_device(self) -> str:
        if self.device != "auto":
            return self.device
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _load_model(self) -> Any:
        """子类实现：返回真实模型实例（懒加载时首次调用）。"""
        raise NotImplementedError

    def _post_load(self) -> None:
        """子类可选：模型加载后校验（如维度一致性），失败抛 SystemConfigError。"""
        return None

    def _ensure_model(self) -> None:
        if self._model is not None:
            return
        try:
            if self._model_factory is not None:
                self._model = self._model_factory()
            else:
                self._model = self._load_model()
            self._post_load()
        except SystemConfigError:
            self._model = None
            raise
        except Exception as exc:
            self._model = None
            raise EmbeddingError(f"模型加载失败（{self.model_name}）: {exc}") from exc

    # -- 探活 ------------------------------------------------------------------

    def ready(self) -> bool:
        self._ensure_model()
        return self._model is not None

    @property
    def is_loaded(self) -> bool:
        """只读状态：是否已加载（不触发加载，供 /api/health 秒回）。"""
        return self._model is not None
