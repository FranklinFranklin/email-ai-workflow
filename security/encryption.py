"""
Kolomniveau-versleuteling via Fernet (AES-128-CBC + HMAC-SHA256).

ISO27001 A.8.2 — Classificatie en bescherming van informatie.
SOC2 CC6.1     — Logische toegangsbeveiliging.

SECURITY FIX (C-1): Statische PBKDF2-zout-fallback verwijderd.
Alleen geldige Fernet-keys (base64url-encoded, 44 tekens) worden
geaccepteerd. Genereer een sleutel met:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Gebruik:
    ciphertext = encrypt("jan.jansen@bedrijf.nl")
    plaintext  = decrypt(ciphertext)

De sleutel (ENCRYPTION_KEY) komt uitsluitend uit omgevingsvariabelen.
Nooit opslaan in code of logs.
"""

from functools import lru_cache
from cryptography.fernet import Fernet, InvalidToken

from config import settings


@lru_cache(maxsize=1)
def _get_fernet() -> Fernet:
    """
    Retourneer een gecachede Fernet-instantie op basis van ENCRYPTION_KEY.

    Alleen geldige base64url-encoded Fernet-keys worden geaccepteerd.
    Gooit ValueError bij een ongeldige sleutelwaarde — faalt expliciet
    bij startup zodat misconfiguratie onmiddellijk opvalt.

    SECURITY: Geen PBKDF2-fallback met statisch zout — dat zou de
    sleutelsterkte verlagen en cross-omgeving aanvallen mogelijk maken.
    """
    key = settings.ENCRYPTION_KEY.strip()
    try:
        return Fernet(key.encode())
    except Exception as exc:
        raise ValueError(
            "ENCRYPTION_KEY is geen geldige Fernet-sleutel. "
            "Genereer een nieuwe met: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from exc


def encrypt(plaintext: str) -> str:
    """
    Versleutel een UTF-8 string. Geeft base64url-gecodeerde ciphertext terug.

    Args:
        plaintext: Te versleutelen tekst (mag leeg zijn).

    Returns:
        Versleutelde string, veilig om op te slaan in de database.
    """
    if not plaintext:
        return plaintext
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """
    Ontsleutel een eerder versleutelde string.

    Args:
        ciphertext: Base64url-gecodeerde versleutelde tekst.

    Returns:
        Oorspronkelijke plaintext.

    Raises:
        ValueError: Als de ciphertext ongeldig of gemanipuleerd is.
    """
    if not ciphertext:
        return ciphertext
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # Geen details loggen — ciphertext kan gevoelige info bevatten
        raise ValueError("Ontsleuteling mislukt: ongeldige of gemanipuleerde waarde") from exc


def encrypt_bytes(data: bytes) -> bytes:
    """Versleutel binaire data (bijv. bijlagen)."""
    return _get_fernet().encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    """Ontsleutel binaire data."""
    try:
        return _get_fernet().decrypt(data)
    except InvalidToken as exc:
        raise ValueError("Ontsleuteling van bytes mislukt") from exc
