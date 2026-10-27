"""Hugging Face 本地缓存探测（模型懒加载/启动预热共用）。

背景：`SentenceTransformer(...)` / `CrossEncoder(...)` 默认 `local_files_only=False`，
即使 snapshot 已完整落在 HF 缓存，也会先向 `HF_ENDPOINT` 发 HEAD 检查 etag/PEFT 配置。
镜像不可达时会重试数秒到数十秒，表现为“向量模型一直在启动中”。本模块在加载前
仅做**本地只读**探测：命中缓存就传 `local_files_only=True`，完全跳过网络。
"""
from __future__ import annotations

from pathlib import Path


def has_local_snapshot(model_name: str) -> bool:
    """判断模型是否已完整落在本地 HF 缓存（或本身就是本地目录）。

    只读、不联网；缓存缺失/不完整返回 False，调用方仍可按原逻辑联网下载。
    """
    name = (model_name or "").strip()
    if not name:
        return False
    # 配置可直接写本地目录（CI/离线包），不经过 HF hub。
    local = Path(name).expanduser()
    if local.is_dir():
        return True
    try:
        from huggingface_hub import snapshot_download

        path = snapshot_download(name, local_files_only=True)
        return bool(path) and Path(path).is_dir()
    except Exception:  # noqa: BLE001 — 缓存探测失败一律回退在线加载
        return False
