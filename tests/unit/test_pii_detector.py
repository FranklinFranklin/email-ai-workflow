"""
Unit tests — security/pii_detector.py

Test: tokenisatie, detokenisatie, lege input, mask_pii.
Presidio-aanroepen worden gemockt zodat geen spaCy-model nodig is.
"""

import pytest
from unittest.mock import MagicMock, patch

from security.pii_detector import (
    PIIDetector,
    TokenizationResult,
    detokenize_pii,
    mask_pii,
    tokenize_pii,
)


@pytest.fixture
def mock_presidio(monkeypatch):
    """Mock AnalyzerEngine en AnonymizerEngine."""
    fake_result = MagicMock()
    fake_result.entity_type = "PERSON"
    fake_result.start = 6
    fake_result.end = 17
    fake_result.score = 0.85

    mock_analyzer = MagicMock()
    mock_analyzer.analyze.return_value = [fake_result]

    mock_anon_result = MagicMock()
    mock_anon_result.text = "Beste [NAAM_001], welkom."

    mock_anonymizer = MagicMock()
    mock_anonymizer.anonymize.return_value = mock_anon_result

    monkeypatch.setattr("security.pii_detector._default_detector", None)

    with (
        patch("security.pii_detector.AnalyzerEngine", return_value=mock_analyzer),
        patch("security.pii_detector.AnonymizerEngine", return_value=mock_anonymizer),
    ):
        yield mock_analyzer, mock_anonymizer

    monkeypatch.setattr("security.pii_detector._default_detector", None)


class TestPIIDetector:
    def test_empty_string_returns_unchanged(self):
        detector = PIIDetector.__new__(PIIDetector)
        detector._analyzer = MagicMock(analyze=MagicMock(return_value=[]))
        detector._anonymizer = MagicMock()
        detector._counters = {}
        result = detector.tokenize("")
        assert result.anonymized_text == ""
        assert result.token_map == {}

    def test_tokenize_replaces_pii(self, mock_presidio):
        detector = PIIDetector()
        result = detector.tokenize("Beste Jan Janssen, welkom.")
        assert "[NAAM_001]" in result.anonymized_text
        assert "Jan Janssen" not in result.anonymized_text

    def test_token_map_contains_original(self, mock_presidio):
        detector = PIIDetector()
        result = detector.tokenize("Beste Jan Janssen, welkom.")
        # Token map moet de originele waarde bevatten
        assert any("Jan Janssen" in v for v in result.token_map.values())

    def test_detokenize_restores_original(self, mock_presidio):
        detector = PIIDetector()
        token_map = {"[NAAM_001]": "Jan Janssen"}
        restored = detector.detokenize("Beste [NAAM_001], bedankt.", token_map)
        assert restored == "Beste Jan Janssen, bedankt."

    def test_detokenize_empty_map(self):
        detector = PIIDetector.__new__(PIIDetector)
        result = detector.detokenize("Geen tokens hier.", {})
        assert result == "Geen tokens hier."

    def test_detokenize_multiple_tokens(self):
        detector = PIIDetector.__new__(PIIDetector)
        token_map = {
            "[NAAM_001]": "Jan Janssen",
            "[EMAIL_001]": "jan@bedrijf.nl",
        }
        text = "Bericht van [NAAM_001] via [EMAIL_001]."
        result = detector.detokenize(text, token_map)
        assert result == "Bericht van Jan Janssen via jan@bedrijf.nl."

    def test_tokenize_multiple_entities_of_same_type(self):
        detector = PIIDetector()
        r1 = MagicMock(entity_type="PERSON", start=6, end=17, score=0.9)
        r2 = MagicMock(entity_type="PERSON", start=21, end=36, score=0.9)
        detector._analyzer.analyze = MagicMock(return_value=[r1, r2])

        text = "Beste Jan Janssen en Pieter de Vries."
        result = detector.tokenize(text)
        assert "[NAAM_001]" in result.anonymized_text
        assert "[NAAM_002]" in result.anonymized_text
        assert result.token_map["[NAAM_001]"] == "Jan Janssen"
        assert result.token_map["[NAAM_002]"] == "Pieter de Vries"
        assert "Jan Janssen" not in result.anonymized_text
        assert "Pieter de Vries" not in result.anonymized_text

        restored = detector.detokenize(result.anonymized_text, result.token_map)
        assert restored == text


class TestModuleFunctions:
    def test_mask_pii_returns_string(self, mock_presidio):
        result = mask_pii("Beste Jan Janssen.")
        assert isinstance(result, str)
        assert "Jan Janssen" not in result

    def test_tokenize_pii_returns_result_object(self, mock_presidio):
        result = tokenize_pii("Beste Jan Janssen.")
        assert isinstance(result, TokenizationResult)
        assert hasattr(result, "anonymized_text")
        assert hasattr(result, "token_map")

    def test_detokenize_pii_roundtrip(self, mock_presidio):
        result = tokenize_pii("Beste Jan Janssen.")
        restored = detokenize_pii(result.anonymized_text, result.token_map)
        assert "Jan Janssen" in restored
