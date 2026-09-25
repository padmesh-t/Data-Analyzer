from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "Agentic Analytics API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = True

    DATABASE_URL: str = "postgresql://user:pass@localhost:5432/agentic"
    REDIS_URL: str = "redis://localhost:6379/0"

    SECRET_KEY: str = "super-secret-key-change-in-production-abc123"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    SMTP_HOST: str = ""
    SMTP_PORT: int = 1025
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@agentic-analytics.com"
    SMTP_FROM_NAME: str = "Agentic Analytics"
    APP_URL: str = "http://localhost:3000"

    VLLM_API_URL: str = "http://localhost:11434/v1"
    VLLM_API_KEY: str = ""
    LLM_USE_MOCK: bool = False
    LLM_MODEL: str = "qwen3:8b"

    MCP_API_KEY: str = ""
    MCP_ENABLED: bool = True

    class Config:
        env_file = ".env"

settings = Settings()
