"""
FastAPI dependencies — herbruikbare injecteerbare functies.

ISO27001 A.9.4  — Toegangsbeheer op applicatieniveau.
SOC2 CC6.3      — Autorisatie en toegangscontrole.

Gebruik in routes:
    @router.get("/drafts/{id}")
    async def get_draft(
        draft_id: UUID,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_session),
    ):
        ...
"""

from uuid import UUID

import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database.connection import get_session

logger = structlog.get_logger(__name__)

# ── Bearer-token extractor ────────────────────────────────────────────────────
_bearer = HTTPBearer(auto_error=True)


# ── JWT authenticatie ─────────────────────────────────────────────────────────

class AuthenticatedUser:
    """Lichte representatie van de ingelogde gebruiker uit het JWT-token."""

    def __init__(self, user_id: UUID, account_id: UUID, role: str) -> None:
        self.user_id    = user_id
        self.account_id = account_id
        self.role       = role


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> AuthenticatedUser:
    """
    Valideer het JWT-token en retourneer de ingelogde gebruiker.

    Gooit HTTP 401 bij een ongeldig, verlopen of gemanipuleerd token.
    Nooit de token-inhoud loggen.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ongeldige of verlopen authenticatietoken.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        # FIX H-1: Algoritme hardcoded — nooit configurable laten via settings.
        # algorithms=["none"] of een geïnjecteerde waarde maakt token-vervalsing mogelijk.
        _ALLOWED_JWT_ALGORITHMS = ["HS256"]
        payload = jwt.decode(
            credentials.credentials,
            settings.APP_SECRET_KEY,
            algorithms=_ALLOWED_JWT_ALGORITHMS,
        )
        user_id: str | None    = payload.get("sub")
        account_id: str | None = payload.get("account_id")
        role: str | None       = payload.get("role")

        if not user_id or not account_id or not role:
            raise credentials_exc

    except JWTError:
        raise credentials_exc

    return AuthenticatedUser(
        user_id=UUID(user_id),
        account_id=UUID(account_id),
        role=role,
    )


def require_role(*allowed_roles: str):
    """
    Factory die een dependency maakt die een specifieke rol vereist.

    Gebruik:
        @router.delete("/accounts/{id}")
        async def delete_account(
            _: AuthenticatedUser = Depends(require_role("admin")),
        ):
            ...
    """
    async def _check(
        current_user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if current_user.role not in allowed_roles:
            logger.warning(
                "authorization_denied",
                role=current_user.role,
                required=allowed_roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Onvoldoende rechten voor deze actie.",
            )
        return current_user

    return _check


# ── Request-validatie ─────────────────────────────────────────────────────────

async def validate_content_type(request: Request) -> None:
    """
    Vereis application/json voor POST en PATCH verzoeken.

    Voorkomt formulier-gebaseerde aanvallen en onverwachte parsers.
    """
    if request.method in ("POST", "PATCH"):
        content_type = request.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Content-Type moet application/json zijn.",
            )


async def validate_body_size(request: Request) -> None:
    """
    Weiger verzoeken die het maximum bodyformaat overschrijden.

    Beschermt tegen gigantische payloads en DoS via e-mailingestie.
    """
    content_length = request.headers.get("Content-Length")
    if content_length and int(content_length) > settings.MAX_EMAIL_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Verzoek te groot. Maximum is "
                f"{settings.MAX_EMAIL_SIZE_BYTES // 1024 // 1024} MB."
            ),
        )


# ── Database-sessie (herexport voor gemak) ────────────────────────────────────

async def get_db() -> AsyncSession:
    """Alias voor get_session — consistente naamgeving in routes."""
    async for session in get_session():
        yield session
