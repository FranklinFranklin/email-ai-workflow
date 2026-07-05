"""
Root-level conftest — zet testomgevingsvariabelen vóór élke module-import.

Waarom hier en niet in tests/conftest.py:
    `config/settings.py` initialiseert `settings = get_settings()` op module-niveau.
    Pytest laadt conftest.py-bestanden vóór testmodules, maar ná de root-conftest.
    Module-niveau code in de te testen bestanden draait bij eerste import —
    dat kan eerder zijn dan een fixture in tests/conftest.py.

    Door `os.environ` hier op module-niveau te zetten (buiten een fixture)
    zijn de waarden beschikbaar vóór de eerste import van config/settings.py.
"""

import os
from cryptography.fernet import Fernet

# ── Verplichte settings — minimale testwaarden ────────────────────────────────
os.environ.setdefault("APP_SECRET_KEY",     "test-secret-key-minimaal-32-tekens!!")
os.environ.setdefault("POSTGRES_PASSWORD",  "testpassword")
os.environ.setdefault("REDIS_PASSWORD",     "testpassword")
os.environ.setdefault("ENCRYPTION_KEY",     Fernet.generate_key().decode())
os.environ.setdefault("ANTHROPIC_API_KEY",  "sk-ant-test-key")
os.environ.setdefault("GMAIL_CLIENT_ID",    "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GMAIL_CLIENT_SECRET","test-client-secret")
os.environ.setdefault("GMAIL_REDIRECT_URI", "http://localhost:8000/api/v1/auth/gmail/callback")
os.environ.setdefault("PUBSUB_VERIFICATION_TOKEN", "test-pubsub-token-32-tekens-lang!!")
