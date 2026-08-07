"""FastAPI 入口（uvicorn app.main:app）：装配 DI → 启动。"""
from app.application import create_app

app = create_app()  # pragma: no cover — uvicorn 入口（make run 手工验证，非单测目标）


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    from app.core.config import load_config

    cfg = load_config()
    uvicorn.run("app.main:app", host=cfg.api.host, port=cfg.api.port, reload=True)
