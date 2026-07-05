"""
Centrale applicatieconfiguratie via Pydantic BaseSettings.

Alle waarden komen uit omgevingsvariabelen of .env-bestand.
Nooit hardcoden — nooit in git committen.
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

    # ── Applicatie ────────────────────────────────────────────────────────────
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_SECRET_KEY: str = Field(min_length=32)
    APP_DEBUG: bool = False
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    ALLOWED_ORIGINS: list[str] = ["http://localhost:3000"]

    # ── Database ──────────────────────────────────────────────────────────────
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "emailai"
    POSTGRES_PASSWORD: str
    POSTGRES_DB: str = "emailai_db"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str
    REDIS_DB: int = 0

    @property
    def REDIS_URL(self) -> str:
        return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # ── Versleuteling ─────────────────────────────────────────────────────────
    ENCRYPTION_KEY: str  # Fernet-key, base64-encoded

    # ── LLM — provider-selectie ───────────────────────────────────────────────
    # "anthropic" = Anthropic Claude API (standaard, vereist API-sleutel)
    # "ollama"    = Lokaal model via Ollama (geen API-sleutel nodig)
    LLM_PROVIDER: Literal["anthropic", "ollama"] = "anthropic"

    # ── Anthropic ─────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = ""          # Verplicht als LLM_PROVIDER=anthropic
    ANTHROPIC_MODEL: str = "claude-opus-4-8"
    ANTHROPIC_MAX_TOKENS: int = 4096
    ANTHROPIC_TIMEOUT_SECONDS: int = 30

    # ── Ollama (lokaal) ───────────────────────────────────────────────────────
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "llama3.2"       # Elk model dat je lokaal hebt gedownload

    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    # ── Gmail ─────────────────────────────────────────────────────────────────
    GMAIL_CLIENT_ID: str
    GMAIL_CLIENT_SECRET: str
    GMAIL_REDIRECT_URI: str
    GMAIL_PUBSUB_TOPIC: str = ""
    # ⛔ Nooit gmail.send toevoegen
    GMAIL_SCOPES: list[str] = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.labels",
    ]

    # ── JWT ───────────────────────────────────────────────────────────────────
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── Worker ────────────────────────────────────────────────────────────────
    EMAIL_PROCESSING_QUEUE: str = "email_processing"
    EMAIL_PROCESSING_TIMEOUT: int = 300
    WORKER_CONCURRENCY: int = 4

    # ── Retentie ──────────────────────────────────────────────────────────────
    EMAIL_RETENTION_DAYS: int = 365
    DRAFT_RETENTION_DAYS: int = 90
    AUDIT_LOG_RETENTION_DAYS: int = 2555  # 7 jaar

    # ── Limieten ──────────────────────────────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = 60
    MAX_EMAIL_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB

    # ── FIX H-4: Vertrouwde proxy-CIDRs ──────────────────────────────────────
    # Lijst van CIDR-blokken waarvan X-Forwarded-For vertrouwd wordt.
    # Leeg = X-Forwarded-For volledig negeren (veilig standaard).
    # Voorbeeld: ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    TRUSTED_PROXY_CIDRS: list[str] = []

    # ── FIX H-5: Aparte Pub/Sub verificatietoken ──────────────────────────────
    # Onafhankelijk van APP_SECRET_KEY — nooit afleiden van JWT-sleutel.
    # Genereer met: python -c "import secrets; print(secrets.token_urlsafe(32))"
    PUBSUB_VERIFICATION_TOKEN: str = ""

    # ── Monitoring ────────────────────────────────────────────────────────────
    SENTRY_DSN: str = ""
    LOG_LEVEL: str = "INFO"

    @field_validator("APP_SECRET_KEY")
    @classmethod
    def secret_key_strength(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("APP_SECRET_KEY moet minimaal 32 tekens bevatten")
        return v


@lru_cache
def get_settings() -> Settings:
    """Gecachede singleton — één keer laden per proces."""
    return Settings()


settings = get_settings()
