# syntax=docker/dockerfile:1
# 多阶段构建：base（依赖）→ runtime（应用）
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    # 嵌入模型下载兜底（避免加载模型卡外网；容器内首次启动自动下载）
    HF_ENDPOINT=https://hf-mirror.com

# 系统依赖：pymupdf 需要 libjpeg/zlib 等；编译工具给 torch/sentence-transformers 兜底
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ libffi-dev libjpeg62-turbo zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/app

# 先拷依赖清单以利用层缓存
COPY pyproject.toml ./
COPY app ./app
COPY web ./web
COPY configs ./configs
COPY tests ./tests

RUN pip install --no-cache-dir -e ".[dev]"

# --- runtime ---
FROM base AS runtime

EXPOSE 8000

# 运行时数据卷（Chroma 持久化；.env 经 compose env_file 注入，不入镜像）
VOLUME ["/srv/app/data"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
