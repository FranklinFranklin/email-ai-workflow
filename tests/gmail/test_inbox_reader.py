"""
Gmail mock tests — services/gmail/inbox_reader.py

Test: berichten lezen, MIME-parsing, bijlage-detectie,
403/401-foutafhandeling, retry-gedrag.
Alle Gmail API-calls zijn gemockt — geen netwerk nodig.
"""

import base64
import pytest
from unittest.mock import MagicMock, patch
from googleapiclient.errors import HttpError


def _make_http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp=resp, content=b"error")


def _make_gmail_message(
    message_id: str = "msg-001",
    subject: str = "Test onderwerp",
    sender: str = "Jan Janssen <jan@bedrijf.nl>",
    body_plain: str = "Hallo, dit is een test.",
    body_html: str = "<p>Hallo, dit is een test.</p>",
    thread_id: str = "thread-001",
    has_attachment: bool = False,
) -> dict:
    """Bouw een nep Gmail API message-payload."""
    parts = [
        {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(body_plain.encode()).decode()},
        },
        {
            "mimeType": "text/html",
            "body": {"data": base64.urlsafe_b64encode(body_html.encode()).decode()},
        },
    ]
    if has_attachment:
        parts.append({
            "mimeType": "application/pdf",
            "filename": "bijlage.pdf",
            "body": {"attachmentId": "att-001"},
        })

    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": ["INBOX", "UNREAD"],
        "internalDate": "1735689600000",  # 2026-01-01 00:00:00 UTC
        "sizeEstimate": 1024,
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "ontvanger@bedrijf.nl"},
                {"name": "Date", "value": "Wed, 1 Jan 2026 00:00:00 +0000"},
            ],
            "parts": parts,
        },
    }


@pytest.fixture
def mock_service():
    return MagicMock()


@pytest.fixture
def reader(mock_service):
    from services.gmail.inbox_reader import GmailInboxReader
    with patch("googleapiclient.discovery.build", return_value=mock_service):
        r = GmailInboxReader(credentials=MagicMock())
        r._service = mock_service
        return r


class TestListUnread:
    def test_returns_list_of_messages(self, reader, mock_service):
        stub = {"messages": [{"id": "msg-001"}, {"id": "msg-002"}]}
        mock_service.users().messages().list().execute.return_value = stub

        msg_data = _make_gmail_message()
        mock_service.users().messages().get().execute.return_value = msg_data

        messages = reader.list_unread()
        assert len(messages) == 2

    def test_empty_inbox_returns_empty_list(self, reader, mock_service):
        mock_service.users().messages().list().execute.return_value = {"messages": []}
        result = reader.list_unread()
        assert result == []

    def test_one_failed_message_does_not_stop_batch(self, reader, mock_service):
        """Als één bericht mislukt, worden de rest alsnog verwerkt."""
        stub = {"messages": [{"id": "ok-msg"}, {"id": "fail-msg"}]}
        mock_service.users().messages().list().execute.return_value = stub

        def side_effect(*args, **kwargs):
            call_mock = MagicMock()
            msg_id = kwargs.get("id", "")
            if msg_id == "fail-msg":
                call_mock.execute.side_effect = _make_http_error(404)
            else:
                call_mock.execute.return_value = _make_gmail_message("ok-msg")
            return call_mock

        mock_service.users().messages().get.side_effect = side_effect

        messages = reader.list_unread()
        # Alleen het geslaagde bericht wordt teruggegeven
        assert len(messages) == 1
        assert messages[0].message_id == "ok-msg"


class TestGetMessage:
    def test_parses_subject(self, reader, mock_service):
        mock_service.users().messages().get().execute.return_value = (
            _make_gmail_message(subject="Offerteverzoek")
        )
        msg = reader.get_message("msg-001")
        assert msg.subject == "Offerteverzoek"

    def test_parses_sender_name_and_email(self, reader, mock_service):
        mock_service.users().messages().get().execute.return_value = (
            _make_gmail_message(sender="Jan Janssen <jan@bedrijf.nl>")
        )
        msg = reader.get_message("msg-001")
        assert msg.sender_name == "Jan Janssen"
        assert msg.sender_email == "jan@bedrijf.nl"

    def test_parses_plain_body(self, reader, mock_service):
        mock_service.users().messages().get().execute.return_value = (
            _make_gmail_message(body_plain="Dit is de plaintext body.")
        )
        msg = reader.get_message("msg-001")
        assert "Dit is de plaintext body." in msg.body_plain

    def test_detects_attachment(self, reader, mock_service):
        mock_service.users().messages().get().execute.return_value = (
            _make_gmail_message(has_attachment=True)
        )
        msg = reader.get_message("msg-001")
        assert msg.has_attachments is True
        assert "bijlage.pdf" in msg.attachment_names

    def test_no_attachment_flag_false(self, reader, mock_service):
        mock_service.users().messages().get().execute.return_value = (
            _make_gmail_message(has_attachment=False)
        )
        msg = reader.get_message("msg-001")
        assert msg.has_attachments is False
        assert msg.attachment_names == []


class TestErrorHandling:
    def test_401_raises_permission_error(self, reader, mock_service):
        mock_service.users().messages().get().execute.side_effect = (
            _make_http_error(401)
        )
        with pytest.raises(PermissionError, match="herauthorisatie"):
            reader.get_message("msg-001")

    def test_403_raises_permission_error(self, reader, mock_service):
        mock_service.users().messages().get().execute.side_effect = (
            _make_http_error(403)
        )
        with pytest.raises(PermissionError, match="permissies"):
            reader.get_message("msg-001")
