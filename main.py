"""
Applicatie-entrypoint — geen businesslogica hier.

Verantwoordelijkheden:
- FastAPI-instantie aanmaken
- Middleware registreren
- Routers koppelen
- Lifecycle (startup / shutdown) beheren
"""

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.middleware import (
    AuditLoggingMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
    SecurityHeadersMiddleware,
)
from api.v1.routes.webhooks import router as webhooks_router
from config import settings

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup en shutdown hooks."""
    logger.info("startup", env=settings.APP_ENV)
    yield
    logger.info("shutdown")


app = FastAPI(
    title="AI E-mail Workflow",
    description=(
        "Draft-only zakelijke e-mail AI. "
        "Concepten worden nooit automatisch verzonden."
    ),
    version="0.1.0",
    # Swagger uitschakelen in productie
    docs_url="/docs" if settings.APP_ENV != "production" else None,
    redoc_url=None,
    lifespan=lifespan,
)

# ── CORS ─────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)

app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuditLoggingMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# ── Routers (worden later geregistreerd per module) ──────────
# from api.v1.routes import emails, drafts, health
# app.include_router(health.router,   prefix="/api/v1", tags=["health"])
# app.include_router(emails.router,   prefix="/api/v1", tags=["emails"])
# app.include_router(drafts.router,   prefix="/api/v1", tags=["drafts"])
app.include_router(webhooks_router, prefix="/api/v1", tags=["webhooks"])


@app.get("/healthz", tags=["health"])
async def health_check() -> dict:
    """
    Liveness probe voor Docker / Kubernetes.

    FIX L-1: APP_ENV verwijderd — lekt deployment-omgeving aan
    ongeauthenticeerde aanroepers en vergemakkelijkt gerichte aanvallen.
    """
    return {"status": "ok"}
