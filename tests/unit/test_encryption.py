"""
Unit tests — security/encryption.py

Test: encrypt/decrypt round-trip, lege strings,
manipulatie-detectie, bytes-variant.
"""

import pytest
from unittest.mock import patch
from cryptography.fernet import Fernet

# Genereer een geldige testsleutel zodat de module laadt zonder echte .env
_TEST_KEY = Fernet.generate_key().decode()


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch):
    monkeypatch.setenv("ENCRYPTION_KEY", _TEST_KEY)
    monkeypatch.setenv("APP_SECRET_KEY", "test-secret-key-minimaal-32-tekens!!")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.setenv("REDIS_PASSWORD", "test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setenv("GMAIL_CLIENT_ID", "test")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "test")
    monkeypatch.setenv("GMAIL_REDIRECT_URI", "http://localhost/callback")


class TestEncryptDecrypt:
    def test_round_trip_plain_string(self):
        from security.encryption import decrypt, encrypt
        original = "jan.jansen@bedrijf.nl"
        assert decrypt(encrypt(original)) == original

    def test_round_trip_unicode(self):
        from security.encryption import decrypt, encrypt
        original = "Ünïcödé strïng 🔒"
        assert decrypt(encrypt(original)) == original

    def test_empty_string_returns_empty(self):
        from security.encryption import decrypt, encrypt
        assert encrypt("") == ""
        assert decrypt("") == ""

    def test_ciphertext_differs_from_plaintext(self):
        from security.encryption import encrypt
        plaintext = "geheim"
        assert encrypt(plaintext) != plaintext

    def test_two_encryptions_differ(self):
        """Fernet gebruikt willekeurige IV — zelfde input geeft andere ciphertext."""
        from security.encryption import encrypt
        text = "zelfde tekst"
        assert encrypt(text) != encrypt(text)

    def test_tampered_ciphertext_raises(self):
        from security.encryption import decrypt, encrypt
        ciphertext = encrypt("geheim")
        tampered = ciphertext[:-4] + "XXXX"
        with pytest.raises(ValueError, match="Ontsleuteling mislukt"):
            decrypt(tampered)

    def test_random_string_raises(self):
        from security.encryption import decrypt
        with pytest.raises(ValueError):
            decrypt("dit-is-geen-geldige-ciphertext")


class TestEncryptBytes:
    def test_round_trip_bytes(self):
        from security.encryption import decrypt_bytes, encrypt_bytes
        data = b"\x00\x01\x02PDF-bijlage-inhoud"
        assert decrypt_bytes(encrypt_bytes(data)) == data

    def test_tampered_bytes_raises(self):
        from security.encryption import decrypt_bytes, encrypt_bytes
        encrypted = encrypt_bytes(b"geheim")
        with pytest.raises(ValueError):
            decrypt_bytes(encrypted[:-2] + b"ZZ")
