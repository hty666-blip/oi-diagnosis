"""集中配置：只从环境变量读，不在代码里写默认密钥。"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_env: str = "dev"
    app_debug: bool = True
    app_host: str = "0.0.0.0"
    app_port: int = 8000

    # 数据库
    database_url: str = "postgresql+asyncpg://oiuser:oipass@localhost:5432/oidiag"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # MinIO
    minio_endpoint: str = "localhost:9000"
    minio_root_user: str = "minioadmin"
    minio_root_password: str = "minioadmin"
    minio_bucket: str = "oi-diag"
    minio_secure: bool = False

    # 沙箱
    judge_backend: str = "docker"   # docker（本机兜底）| gojudge（生产）
    judge_url: str = "http://localhost:5050"
    judge_token: str = ""
    judge_timeout_s: float = 30.0

    # 模型
    llm_enabled: bool = True
    llm_api_base: str = ""
    llm_api_key: str = ""
    llm_model_fast: str = ""
    llm_model_reasoning: str = ""

    # 安全
    jwt_secret: str = "dev-only-change-me"
    jwt_alg: str = "HS256"
    access_token_expire_minutes: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


