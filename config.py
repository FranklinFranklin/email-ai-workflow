"""
Applicatieconfiguratie via Pydantic BaseSettings.

Alle waarden komen uit omgevingsvariabelen of .env.
Nooit secrets hardcoden of committen.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Applicatie ────────────────────────────────────────────
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_SECRET_KEY: str = Field(min_length=32)
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000

    # ── Database ──────────────────────────────────────────────
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "emailai"
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str = "emailai_db"

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ── Redis ─────────────────────────────────────────────────
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str

    @property
    def REDIS_URL(self) -> str:
        return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # ── Versleuteling (PII kolomniveau) ───────────────────────
    ENCRYPTION_KEY: str  # Fernet-key — zie README voor generatie

    # ── LLM ───────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str
    ANTHROPIC_MODEL: str = "claude-opus-4-8"

    # ── Gmail (NOOIT send-scope toevoegen) ────────────────────
    GMAIL_CLIENT_ID: str
    GMAIL_CLIENT_SECRET: str
    GMAIL_REDIRECT_URI: str = "http://localhost:8000/api/v1/auth/gmail/callback"

    # ── Beveiliging ───────────────────────────────────────────
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]
    RATE_LIMIT_PER_MINUTE: int = 60

    # ── Logging ───────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"

    @field_validator("APP_SECRET_KEY")
    @classmethod
    def validate_secret_strength(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("APP_SECRET_KEY moet minimaal 32 tekens bevatten")
        return v


@lru_cache
def get_settings() -> Settings:
    """Gecachede singleton — één keer ingeladen per proces."""
    return Settings()


settings = get_settings()
