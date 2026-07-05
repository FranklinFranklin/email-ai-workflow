"""
Gmail mock tests — services/gmail/draft_creator.py

Test: draft aanmaken, updaten, ophalen, foutafhandeling.
KRITIEKE test: geen send()-methode aanwezig.
Alle Gmail API-calls gemockt.
"""

import pytest
from unittest.mock import MagicMock
from googleapiclient.errors import HttpError


def _make_http_error(status: int) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp=resp, content=b"error")


def _mock_draft_response(draft_id: str = "draft-001") -> dict:
    return {
        "id": draft_id,
        "message": {"id": "msg-001", "threadId": "thread-001"},
    }


@pytest.fixture
def mock_service():
    return MagicMock()


@pytest.fixture
def creator(mock_service):
    from services.gmail.draft_creator import GmailDraftCreator
    from unittest.mock import patch
    with patch("googleapiclient.discovery.build", return_value=mock_service):
        c = GmailDraftCreator(credentials=MagicMock())
        c._service = mock_service
        return c


class TestCreateDraft:
    def test_returns_draft_result(self, creator, mock_service):
        mock_service.users().drafts().create().execute.return_value = (
            _mock_draft_response("draft-abc")
        )
        result = creator.create_draft(
            to="klant@bedrijf.nl",
            subject="Re: Uw vraag",
            body_plain="Geachte heer/mevrouw...",
        )
        assert result.draft_id == "draft-abc"

    def test_draft_id_populated(self, creator, mock_service):
        mock_service.users().drafts().create().execute.return_value = (
            _mock_draft_response("draft-xyz")
        )
        result = creator.create_draft(
            to="a@b.nl", subject="Test", body_plain="Body."
        )
        assert result.draft_id == "draft-xyz"
        assert result.recipient == "a@b.nl"
        assert result.subject == "Test"

    def test_thread_id_included_in_request(self, creator, mock_service):
        mock_service.users().drafts().create().execute.return_value = (
            _mock_draft_response()
        )
        creator.create_draft(
            to="a@b.nl",
            subject="Re: Thread",
            body_plain="Antwoord.",
            thread_id="thread-999",
        )
        call_args = mock_service.users().drafts().create.call_args
        body = call_args[1]["body"]
        assert body["message"]["threadId"] == "thread-999"

    def test_html_body_included_when_provided(self, creator, mock_service):
        mock_service.users().drafts().create().execute.return_value = (
            _mock_draft_response()
        )
        # Mag geen exception gooien bij HTML
        result = creator.create_draft(
            to="a@b.nl",
            subject="Test",
            body_plain="Platte tekst.",
            body_html="<p>HTML versie.</p>",
        )
        assert result.draft_id is not None


class TestUpdateDraft:
    def test_update_returns_result(self, creator, mock_service):
        mock_service.users().drafts().update().execute.return_value = (
            _mock_draft_response("draft-updated")
        )
        result = creator.update_draft(
            draft_id="draft-001",
            to="a@b.nl",
            subject="Bijgewerkt onderwerp",
            body_plain="Bijgewerkte tekst.",
        )
        assert result.draft_id == "draft-updated"

    def test_update_calls_correct_draft_id(self, creator, mock_service):
        mock_service.users().drafts().update().execute.return_value = (
            _mock_draft_response()
        )
        creator.update_draft(
            draft_id="draft-to-update",
            to="a@b.nl",
            subject="Test",
            body_plain="Body.",
        )
        call_args = mock_service.users().drafts().update.call_args
        assert call_args[1]["id"] == "draft-to-update"


class TestErrorHandling:
    def test_401_raises_permission_error(self, creator, mock_service):
        mock_service.users().drafts().create().execute.side_effect = (
            _make_http_error(401)
        )
        with pytest.raises(PermissionError, match="herauthorisatie"):
            creator.create_draft(to="a@b.nl", subject="T", body_plain="B.")

    def test_403_raises_permission_error(self, creator, mock_service):
        mock_service.users().drafts().create().execute.side_effect = (
            _make_http_error(403)
        )
        with pytest.raises(PermissionError, match="permissies"):
            creator.create_draft(to="a@b.nl", subject="T", body_plain="B.")


class TestNoSendMethod:
    """
    ARCHITECTURELE VEILIGHEIDSTEST.
    DraftCreator mag NOOIT een send()-methode hebben.
    """

    def test_send_method_absent(self):
        from services.gmail.draft_creator import GmailDraftCreator
        assert not hasattr(GmailDraftCreator, "send"), (
            "KRITIEKE BEVEILIGINGSFOUT: GmailDraftCreator.send() bestaat! "
            "Verwijder deze methode onmiddellijk."
        )

    def test_no_smtp_import_in_module(self):
        import ast, pathlib
        source = pathlib.Path("services/gmail/draft_creator.py").read_text()
        tree = ast.parse(source)
        smtp_imports = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            and any("smtp" in alias.name.lower() for alias in node.names)
        ]
        assert len(smtp_imports) == 0, "SMTP-import gevonden in draft_creator.py"

    def test_no_send_calls_in_ast(self):
        import ast, pathlib
        source = pathlib.Path("services/gmail/draft_creator.py").read_text()
        tree = ast.parse(source)
        send_method_defs = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "send"
        ]
        assert len(send_method_defs) == 0, (
            "Functiedefinitie 'send' gevonden in draft_creator.py"
        )
