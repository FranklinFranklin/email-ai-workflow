"""
FastAPI middleware stack — beveiliging, observeerbaarheid en limieten.

ISO27001 A.12.4 — Logging en monitoring.
ISO27001 A.14.1 — Beveiliging van netwerkkommunicatie.
SOC2 CC6.6      — Netwerkbeveiliging.
SOC2 CC7.2      — Detectie van beveiligingsincidenten.

Middleware-volgorde in main.py (buitenste = eerst uitgevoerd):
    1. SecurityHeadersMiddleware   ← HSTS, CSP, X-Frame-Options
    2. RequestIDMiddleware         ← correlatie-ID per verzoek
    3. AuditLoggingMiddleware      ← duur, status, path
    4. RateLimitMiddleware         ← Redis sliding-window
    5. CORSMiddleware              ← FastAPI ingebouwd
"""

import re
import time
import uuid
from typing import Callable

import redis.asyncio as aioredis
import structlog
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from config import settings
from security.audit import AuditAction, audit_log

logger = structlog.get_logger(__name__)


# ── 1. Security Headers ───────────────────────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Voeg beveiligingsheaders toe aan elk antwoord.

    Dekt OWASP Top-10 headeraanbevelingen.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        # Voorkom clickjacking
        response.headers["X-Frame-Options"] = "DENY"

        # Voorkom MIME-type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"

        # XSS-bescherming (legacy browsers)
        response.headers["X-XSS-Protection"] = "1; mode=block"

        # Referrer-informatie beperken
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

        # HTTPS verplichten (productie)
        if settings.APP_ENV == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )

        # Content Security Policy — strikte policy voor API
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
        )

        # Verberg servertechnologie — .pop() bestaat niet op MutableHeaders
        response.headers["Server"] = "emailai"
        if "X-Powered-By" in response.headers:
            del response.headers["X-Powered-By"]

        return response


# ── 2. Request-ID ─────────────────────────────────────────────────────────────

_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.IGNORECASE)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Injecteer een uniek X-Request-ID per verzoek.

    FIX M-4: Client-aangeleverde X-Request-ID wordt gevalideerd tegen
    UUID4-formaat. Een waarde met newlines of speciale tekens wordt
    genegeerd en vervangen door een nieuw UUID4 — log injection onmogelijk.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        candidate = request.headers.get("X-Request-ID", "")
        # Accepteer alleen geldige UUID4-waarden — weiger alles met newlines
        if candidate and _REQUEST_ID_RE.match(candidate):
            request_id = candidate
        else:
            request_id = str(uuid.uuid4())

        # Koppel het ID aan de structlog-context van dit verzoek
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id

        # Opruimen na afloop
        structlog.contextvars.unbind_contextvars("request_id")
        return response


# ── 3. Audit Logging ──────────────────────────────────────────────────────────

_SKIP_PATHS = frozenset({"/healthz", "/metrics", "/favicon.ico"})


class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """
    Log elk HTTP-verzoek voor audit en observeerbaarheid.

    Nooit de request-body loggen — die kan PII bevatten.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        response = await call_next(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)

        log = logger.bind(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            request_id=request.headers.get("X-Request-ID"),
            # Geen body, geen query-params — kunnen PII bevatten
        )

        if response.status_code >= 500:
            log.error("request_error")
        elif response.status_code >= 400:
            log.warning("request_client_error")
        else:
            log.info("request_ok")

        # Ongeautoriseerde pogingen apart flaggen
        if response.status_code == status.HTTP_401_UNAUTHORIZED:
            audit_log(
                AuditAction.SECURITY_UNAUTHORIZED_ACCESS,
                ip_address=_get_client_ip(request),
                request_id=request.headers.get("X-Request-ID"),
                metadata={"path": request.url.path, "method": request.method},
            )

        return response


# ── 4. Rate Limiting (Redis sliding window) ───────────────────────────────────

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding-window rate limiting via Redis.

    Standaard: RATE_LIMIT_PER_MINUTE verzoeken per IP per minuut.
    Authenticeerde gebruikers krijgen een hogere limiet.

    ISO27001 A.12.3 — Bescherming tegen denial-of-service.
    SOC2 CC6.6      — Beveiliging van netwerkkommunicatie.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def _get_redis(self) -> aioredis.Redis:
        return aioredis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in _SKIP_PATHS:
            return await call_next(request)

        client_ip = _get_client_ip(request)
        limit = settings.RATE_LIMIT_PER_MINUTE
        window = 60  # seconden

        try:
            redis = await self._get_redis()
            key = f"ratelimit:{client_ip}"
            current = await redis.incr(key)

            if current == 1:
                # Eerste verzoek in venster — stel TTL in
                await redis.expire(key, window)

            ttl = await redis.ttl(key)

            if current > limit:
                audit_log(
                    AuditAction.SECURITY_RATE_LIMIT_HIT,
                    ip_address=client_ip,
                    request_id=request.headers.get("X-Request-ID"),
                    metadata={"count": current, "limit": limit},
                )
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": "Te veel verzoeken. Probeer het later opnieuw."},
                    headers={
                        "Retry-After": str(ttl),
                        "X-RateLimit-Limit": str(limit),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(ttl),
                    },
                )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(max(0, limit - current))
            response.headers["X-RateLimit-Reset"] = str(ttl)
            return response

        except aioredis.RedisError:
            # Redis onbereikbaar — degrade gracefully, blokkeer niet
            logger.warning("rate_limit_redis_unavailable", ip=client_ip)
            return await call_next(request)


# ── Hulpfunctie ───────────────────────────────────────────────────────────────

import ipaddress as _ipaddress


def _is_trusted_proxy(ip: str) -> bool:
    """
    Controleer of een IP-adres binnen een geconfigureerde trusted-proxy CIDR valt.

    FIX H-4: X-Forwarded-For wordt ALLEEN gelezen als het verzoek afkomstig
    is van een vertrouwde proxy. Als TRUSTED_PROXY_CIDRS leeg is, wordt de
    header volledig genegeerd en is IP-spoofing onmogelijk.
    """
    if not settings.TRUSTED_PROXY_CIDRS:
        return False
    try:
        addr = _ipaddress.ip_address(ip)
        return any(
            addr in _ipaddress.ip_network(cidr, strict=False)
            for cidr in settings.TRUSTED_PROXY_CIDRS
        )
    except ValueError:
        return False


def _get_client_ip(request: Request) -> str:
    """
    Haal het echte client-IP op.

    X-Forwarded-For wordt alleen gelezen als het verzoek van een
    vertrouwde proxy-CIDR afkomstig is (TRUSTED_PROXY_CIDRS in settings).
    Standaard (lege lijst) wordt de directe verbinding gebruikt — geen spoofing mogelijk.
    """
    direct_ip = request.client.host if request.client else "unknown"

    if _is_trusted_proxy(direct_ip):
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            # Laatste IP toegevoegd door de vertrouwde proxy — niet het client-gecontroleerde eerste
            hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
            if hops:
                return hops[-1]

    return direct_ip
