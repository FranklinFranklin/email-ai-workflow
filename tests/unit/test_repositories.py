"""
Unit tests voor database repositories en ORM modellen.
"""

from unittest.mock import AsyncMock, MagicMock
import uuid
import pytest

from database.repositories.email_repo import EmailRepository
from database.repositories.draft_repo import DraftRepository
from database.repositories.audit_repo import AuditRepository
from models.db import Email, Draft, DraftStatus, AuditLog, AuditAction


class TestRepositories:
    def test_repository_models_instantiation(self):
        email = Email(
            id=uuid.uuid4(),
            account_id=uuid.uuid4(),
            message_id="msg-123",
            thread_id="th-123",
            classified=False,
            received_at=None,
        )
        assert email.message_id == "msg-123"

        draft = Draft(
            id=uuid.uuid4(),
            email_id=email.id,
            version=1,
            status=DraftStatus.DRAFT,
            draft_text_enc="enc_text",
            confidence=0.95,
        )
        assert draft.status == DraftStatus.DRAFT
        assert draft.version == 1

        audit = AuditLog(
            id=uuid.uuid4(),
            action=AuditAction.DRAFT_CREATED.value,
            actor_id=None,
            entity_type="draft",
            entity_id=draft.id,
            metadata_={"key": "value"},
        )
        assert audit.entity_type == "draft"

    def test_repository_classes_can_be_instantiated(self):
        session = AsyncMock()
        email_repo = EmailRepository(session)
        assert email_repo.model == Email

        draft_repo = DraftRepository(session)
        assert draft_repo.model == Draft

        audit_repo = AuditRepository(session)
        assert audit_repo.session == session
