"""
Gmail-integratiepackage.

Toegestane scopes (ingesteld in config.py):
    ✅ gmail.readonly   — e-mails lezen
    ✅ gmail.compose    — concepten aanmaken
    ✅ gmail.labels     — labels beheren

    ⛔ gmail.send       — NOOIT opnemen
    ⛔ gmail.modify     — niet nodig
    ⛔ gmail.admin      — NOOIT opnemen
"""

from services.gmail.oauth import GmailOAuth
from services.gmail.inbox_reader import GmailInboxReader
from services.gmail.draft_creator import GmailDraftCreator
from services.gmail.webhook import GmailWebhookHandler

__all__ = [
    "GmailOAuth",
    "GmailInboxReader",
    "GmailDraftCreator",
    "GmailWebhookHandler",
]
