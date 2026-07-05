"""
E-mailclassificatieagent — provider-agnostisch via LLMClient.

Werkt met Anthropic Claude én Ollama (lokaal model).
Selecteer de provider via LLM_PROVIDER in .env.

Pipeline per e-mail:
    1. PII tokeniseren (lokaal)
    2. Injection guard checken
    3. LLM-call met strict JSON-schema
    4. Output valideren
    5. requires_human_review bepalen op basis van confidence

Geen hallucinaties:
    - Structured output via JSON-mode
    - Schema-validatie op elke LLM-respons
    - Fallback naar Overig bij parse-fouten, nooit raden
    - Aparte LLM-call van draft-generatie (geen cross-contamination)
"""

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from agents.llm_client import LLMClient, LLMProviderError
from agents.prompts.classification import (
    CLASSIFICATION_INSTRUCTION,
    FEW_SHOT_EXAMPLES,
    SYSTEM_PROMPT,
)
from config import settings
from security.audit import AuditAction, audit_log
from security.injection_guard import InjectionGuard, RiskLevel
from security.pii_detector import TokenizationResult, tokenize_pii

logger = structlog.get_logger(__name__)

# Confidence-drempel voor menselijke review (configureerbaar)
HUMAN_REVIEW_THRESHOLD = 0.80

# Minimale confidence die de LLM mag retourneren
MIN_CONFIDENCE = 0.50

# Maximale tekst die naar de LLM gaat (tokens beschermen)
MAX_INPUT_CHARS = 8_000


class EmailCategory(str, Enum):
    SALES     = "Sales"
    SUPPORT   = "Support"
    FACTUUR   = "Factuur"
    HR        = "HR"
    JURIDISCH = "Juridisch"
    KLACHT    = "Klacht"
    SPAM      = "Spam"
    OVERIG    = "Overig"


@dataclass
class ClassificationResult:
    """Resultaat van de classificatieagent."""

    # ── Kernresultaat ─────────────────────────────────────────
    category: EmailCategory
    confidence: float                    # 0.50 – 1.00
    motivation: str                      # max 2 zinnen, geen PII
    requires_human_review: bool          # True als confidence < drempel

    # ── Extra context ─────────────────────────────────────────
    secondary_category: EmailCategory | None = None
    detected_language: str = "nl"

    # ── Kwaliteits- en traceerbaarheidsinfo ───────────────────
    model_id: str = ""
    prompt_version: str = "v1"
    processing_time_ms: int = 0
    injection_risk: str = "safe"         # safe | suspicious | blocked

    # ── Fallback-vlag ─────────────────────────────────────────
    used_fallback: bool = False          # True als JSON-parsing mislukte


@dataclass
class ClassificationInput:
    """Invoer voor de classificatieagent."""
    subject: str
    body: str
    sender_email: str = ""              # Optioneel — wordt getokeniseerd
    email_id: str = ""                  # Voor audit-logging


class EmailClassifierAgent:
    """
    Classificeert e-mails via Anthropic Claude.

    Thread-safe — één instantie per applicatie.

    Gebruik:
        agent = EmailClassifierAgent()
        result = agent.classify(ClassificationInput(
            subject="Kunnen jullie een offerte sturen?",
            body="...",
            email_id="uuid-hier",
        ))
    """

    def __init__(self) -> None:
        self._client = LLMClient()   # provider bepaald door LLM_PROVIDER in .env
        self._guard  = InjectionGuard()

    def classify(self, input_data: ClassificationInput) -> ClassificationResult:
        """
        Classificeer een e-mail.

        Args:
            input_data: Onderwerp, body en optionele metadata.

        Returns:
            ClassificationResult met categorie, confidence en motivatie.
            Gooit nooit een exception — bij fouten wordt Overig teruggegeven.
        """
        start = time.perf_counter()

        # ── Stap 1: Samengestelde tekst klaarmaken ────────────
        combined = self._combine_input(input_data)

        # ── Stap 2: Injection guard ───────────────────────────
        guard_result = self._guard.check(combined)
        if guard_result.risk_level == RiskLevel.BLOCKED:
            audit_log(
                AuditAction.SECURITY_INJECTION_DETECTED,
                entity_type="email",
                entity_id=input_data.email_id or None,
                metadata={
                    "patterns": guard_result.matched_patterns,
                    "action": "classification_blocked",
                },
            )
            return self._blocked_result(
                processing_ms=self._elapsed(start),
                risk=guard_result.risk_level.value,
            )

        # ── Stap 3: PII tokeniseren ───────────────────────────
        tokenization: TokenizationResult = tokenize_pii(guard_result.sanitized_text)
        safe_text = tokenization.anonymized_text

        # ── Stap 4: LLM-call ─────────────────────────────────
        try:
            raw = self._call_llm(safe_text)
        except Exception as exc:
            logger.error("classifier_llm_failed", error=type(exc).__name__)
            return self._fallback_result(
                processing_ms=self._elapsed(start),
                reason="llm_error",
            )

        # ── Stap 5: Valideren en mappen ───────────────────────
        try:
            result = self._parse_and_validate(raw)
        except (ValueError, KeyError) as exc:
            logger.warning("classifier_parse_failed", error=str(exc))
            return self._fallback_result(
                processing_ms=self._elapsed(start),
                reason="parse_error",
            )

        # ── Stap 6: PII verwijderen uit motivatie ─────────────
        # Tokens in de motivatie terugzetten zou PII blootstellen in de DB.
        # We houden de getokeniseerde versie — alleen de agent ziet tokens.
        result.processing_time_ms = self._elapsed(start)
        result.model_id = self._client._model  # provider-agnostisch via LLMClient
        result.injection_risk = guard_result.risk_level.value

        # ── Audit logging ─────────────────────────────────────
        audit_log(
            AuditAction.CLASSIFICATION_DONE,
            entity_type="email",
            entity_id=input_data.email_id or None,
            metadata={
                "category":             result.category.value,
                "confidence":           result.confidence,
                "requires_human_review": result.requires_human_review,
                "processing_ms":         result.processing_time_ms,
            },
        )

        logger.info(
            "email_classified",
            category=result.category.value,
            confidence=result.confidence,
            requires_human_review=result.requires_human_review,
        )

        return result

    # ── Privémethoden ─────────────────────────────────────────────────────────

    @staticmethod
    def _combine_input(data: ClassificationInput) -> str:
        """Combineer onderwerp en body tot één beoordelbare tekst."""
        parts: list[str] = []
        if data.subject.strip():
            parts.append(f"Onderwerp: {data.subject.strip()}")
        if data.body.strip():
            parts.append(data.body.strip())
        return "\n\n".join(parts)[:MAX_INPUT_CHARS]

    def _call_llm(self, safe_text: str) -> dict[str, Any]:
        """
        Roep de LLM aan via LLMClient (Anthropic of Ollama).

        Retry-logica zit in LLMClient — geen @retry decorator nodig hier.
        E-mailinhoud staat in XML-tags — nooit los in de prompt.
        """
        # Bouw few-shot berichten op voor confidence-kalibratie
        messages: list[dict[str, Any]] = []
        for example in FEW_SHOT_EXAMPLES[:3]:
            messages.append({
                "role": "user",
                "content": (
                    f"{CLASSIFICATION_INSTRUCTION}\n"
                    f"<email_content>\n{example['input']}\n</email_content>"
                ),
            })
            messages.append({
                "role": "assistant",
                "content": json.dumps(example["output"], ensure_ascii=False),
            })

        # Voeg het echte verzoek toe
        messages.append({
            "role": "user",
            "content": (
                f"{CLASSIFICATION_INSTRUCTION}\n"
                f"{self._guard.wrap_for_llm(safe_text)}"
            ),
        })

        try:
            response = self._client.complete(
                system=SYSTEM_PROMPT,
                messages=messages,
                max_tokens=512,
            )
        except LLMProviderError as exc:
            raise exc  # wordt opgevangen in classify() → fallback

        raw_text = response.text

        # Verwijder markdown code-fences als de LLM die toch toevoegt
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            # FIX M-5: Nooit raw LLM-uitvoer in exception-berichten opnemen.
            # raw_text kan getokeniseerde PII bevatten die via logs zichtbaar wordt.
            raise ValueError(
                f"LLM retourneerde geen geldig JSON "
                f"(lengte: {len(raw_text)}, begint met: {raw_text[:20]!r})"
            ) from exc

    def _parse_and_validate(self, data: dict[str, Any]) -> ClassificationResult:
        """
        Valideer de LLM-output tegen het verwachte schema.

        Gooit ValueError bij ontbrekende of ongeldige velden —
        de caller valt terug op Overig.
        """
        # ── Categorie ─────────────────────────────────────────
        raw_category = str(data.get("category", "")).strip()
        try:
            category = EmailCategory(raw_category)
        except ValueError:
            raise ValueError(f"Onbekende categorie: '{raw_category}'")

        # ── Confidence ────────────────────────────────────────
        raw_confidence = data.get("confidence")
        if not isinstance(raw_confidence, (int, float)):
            raise ValueError(f"Confidence is geen getal: {raw_confidence!r}")
        confidence = float(raw_confidence)
        if not (MIN_CONFIDENCE <= confidence <= 1.0):
            raise ValueError(f"Confidence buiten bereik: {confidence}")

        # ── Motivatie ─────────────────────────────────────────
        motivation = str(data.get("motivation", "")).strip()
        if not motivation:
            raise ValueError("Motivatie ontbreekt")
        motivation = motivation[:500]  # Hard maximum

        # ── Secundaire categorie ──────────────────────────────
        raw_secondary = data.get("secondary_category")
        secondary = None
        if raw_secondary and raw_secondary != "null":
            try:
                secondary = EmailCategory(str(raw_secondary).strip())
            except ValueError:
                secondary = None  # Onbekende waarde negeren, niet falen

        # ── Taal ──────────────────────────────────────────────
        language = str(data.get("detected_language", "nl"))[:5]

        return ClassificationResult(
            category=category,
            confidence=round(confidence, 2),
            motivation=motivation,
            requires_human_review=confidence < HUMAN_REVIEW_THRESHOLD,
            secondary_category=secondary,
            detected_language=language,
        )

    # ── Fallback-resultaten ───────────────────────────────────────────────────

    @staticmethod
    def _fallback_result(
        processing_ms: int,
        reason: str,
    ) -> ClassificationResult:
        """
        Geef Overig terug bij elke onherstelbare fout.

        Nooit raden — altijd transparant dat dit een fallback is.
        requires_human_review=True zodat een medewerker het beoordeelt.
        """
        logger.warning("classifier_fallback_used", reason=reason)
        return ClassificationResult(
            category=EmailCategory.OVERIG,
            confidence=0.0,
            motivation=f"Automatische classificatie mislukt ({reason}). Handmatige beoordeling vereist.",
            requires_human_review=True,
            processing_time_ms=processing_ms,
            used_fallback=True,
        )

    @staticmethod
    def _blocked_result(
        processing_ms: int,
        risk: str,
    ) -> ClassificationResult:
        """
        Geef een geblokkeerd resultaat terug bij prompt-injection.

        E-mail wordt niet geclassificeerd maar wel geflagged voor review.
        """
        return ClassificationResult(
            category=EmailCategory.OVERIG,
            confidence=0.0,
            motivation="E-mail geblokkeerd wegens vermoedelijke prompt-injectiepoging. Handmatige beoordeling vereist.",
            requires_human_review=True,
            processing_time_ms=processing_ms,
            injection_risk=risk,
            used_fallback=True,
        )

    @staticmethod
    def _elapsed(start: float) -> int:
        return round((time.perf_counter() - start) * 1000)
