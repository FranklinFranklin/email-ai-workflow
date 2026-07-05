"""
PII-detectie en tokenisatie via Microsoft Presidio.

ISO27001 A.8.2  — Bescherming van persoonsgegevens.
GDPR Art. 25    — Privacy by design.
SOC2 CC6.1      — Dataclassificatie en toegangsbeveiliging.

Strategie:
    1. Detecteer PII-entiteiten in tekst (namen, e-mails, BSN, IBAN, ...)
    2. Vervang door tokens: [NAAM_001], [EMAIL_001], [BSN_001]
    3. Stuur getokeniseerde tekst naar LLM — nooit echte PII
    4. Na LLM-respons: vervang tokens terug (lokaal, nooit via API)

De tokenmap verlaat nooit de eigen infrastructuur.
"""

import re
from dataclasses import dataclass, field
from typing import TypeAlias

import structlog
from presidio_analyzer import AnalyzerEngine, RecognizerResult
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

logger = structlog.get_logger(__name__)

# Type alias voor leesbaarheid
TokenMap: TypeAlias = dict[str, str]  # {"[NAAM_001]": "Jan Janssen", ...}

# Entiteitstypes die we detecteren — volgorde bepaalt prioriteit bij overlap
ENTITY_TYPES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IBAN_CODE",
    "NL_BSN",           # Burgerservicenummer
    "CREDIT_CARD",
    "IP_ADDRESS",
    "LOCATION",
    "DATE_TIME",
    "NRP",              # Nationaliteit / religie / politiek
]

# Korte labels voor tokens (leesbaar in logs en LLM-context)
_ENTITY_LABEL: dict[str, str] = {
    "PERSON":        "NAAM",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER":  "TEL",
    "IBAN_CODE":     "IBAN",
    "NL_BSN":        "BSN",
    "CREDIT_CARD":   "KAART",
    "IP_ADDRESS":    "IP",
    "LOCATION":      "LOCATIE",
    "DATE_TIME":     "DATUM",
    "NRP":           "NRP",
}

_DEFAULT_LABEL = "PII"


@dataclass
class PIIEntity:
    """Gedetecteerde PII-entiteit met positie en score."""
    entity_type: str
    text: str
    start: int
    end: int
    score: float


@dataclass
class TokenizationResult:
    """Resultaat van tokenisatie — gebruik `anonymized_text` voor LLM."""
    anonymized_text: str
    token_map: TokenMap = field(default_factory=dict)
    entity_count: int = 0


class PIIDetector:
    """
    Thread-safe PII-detector. Initialiseer éénmalig als singleton.

    Gebruik:
        detector = PIIDetector()
        result = detector.tokenize("Bel Jan Janssen op 06-12345678")
        # result.anonymized_text == "Bel [NAAM_001] op [TEL_001]"
        # result.token_map == {"[NAAM_001]": "Jan Janssen", "[TEL_001]": "06-12345678"}
    """

    def __init__(self, language: str = "nl") -> None:
        self.language = language
        self._analyzer = AnalyzerEngine()
        self._anonymizer = AnonymizerEngine()
        self._counters: dict[str, int] = {}

    def detect(self, text: str) -> list[PIIEntity]:
        """Detecteer PII-entiteiten zonder te anonimiseren."""
        results: list[RecognizerResult] = self._analyzer.analyze(
            text=text,
            entities=ENTITY_TYPES,
            language=self.language,
            score_threshold=0.6,
        )
        return [
            PIIEntity(
                entity_type=r.entity_type,
                text=text[r.start:r.end],
                start=r.start,
                end=r.end,
                score=r.score,
            )
            for r in results
        ]

    def tokenize(self, text: str) -> TokenizationResult:
        """
        Vervang PII door tokens. Geeft tokenmap terug voor detokenisatie.

        De tokenmap bevat de mapping token → originele waarde.
        Sla de tokenmap alleen lokaal op — stuur nooit naar externe diensten.

        FIX H-7: _counters is nu een lokale variabele (was instance-attribuut).
        Gelijktijdige aanroepen op de gedeelde singleton kunnen elkaars tellers
        niet meer overschrijven → geen verkeerde PII-mapping onder concurrency.
        """
        if not text or not text.strip():
            return TokenizationResult(anonymized_text=text)

        # Lokale tellers — thread- en coroutine-veilig
        counters: dict[str, int] = {}
        token_map: TokenMap = {}

        results = self._analyzer.analyze(
            text=text,
            entities=ENTITY_TYPES,
            language=self.language,
            score_threshold=0.6,
        )

        if not results:
            return TokenizationResult(anonymized_text=text)

        # Bouw operators per entiteitstype
        operators: dict[str, OperatorConfig] = {}
        for result in results:
            entity = result.entity_type
            label = _ENTITY_LABEL.get(entity, _DEFAULT_LABEL)
            counters[entity] = counters.get(entity, 0) + 1
            token = f"[{label}_{counters[entity]:03d}]"
            original = text[result.start:result.end]
            token_map[token] = original

            operators[entity] = OperatorConfig(
                "replace", {"new_value": token}
            )

        anonymized = self._anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators=operators,
        )

        logger.debug(
            "pii_tokenized",
            entity_count=len(results),
            # Nooit de originele tekst of tokenmap loggen
        )

        return TokenizationResult(
            anonymized_text=anonymized.text,
            token_map=token_map,
            entity_count=len(results),
        )

    def detokenize(self, text: str, token_map: TokenMap) -> str:
        """
        Vervang tokens terug door originele waarden.

        Doe dit ALTIJD lokaal — nooit via een externe API.
        """
        result = text
        for token, original in token_map.items():
            result = result.replace(token, original)
        return result


# ── Module-level convenience-functies ────────────────────────────────────────
_default_detector: PIIDetector | None = None


def _get_detector() -> PIIDetector:
    global _default_detector
    if _default_detector is None:
        _default_detector = PIIDetector()
    return _default_detector


def mask_pii(text: str) -> str:
    """
    Maskeer PII permanent (voor logging). Tokens worden NIET opgeslagen.
    Gebruik tokenize_pii() als je de originele waarden later nodig hebt.
    """
    return _get_detector().tokenize(text).anonymized_text


def tokenize_pii(text: str) -> TokenizationResult:
    """Tokeniseer PII met tokenmap voor latere detokenisatie."""
    return _get_detector().tokenize(text)


def detokenize_pii(text: str, token_map: TokenMap) -> str:
    """Vervang tokens terug door originele waarden (altijd lokaal)."""
    return _get_detector().detokenize(text, token_map)
