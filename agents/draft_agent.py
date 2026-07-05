"""
Draft Generation Agent — provider-agnostisch via LLMClient.

Werkt met Anthropic Claude én Ollama (lokaal model).
Selecteer de provider via LLM_PROVIDER in .env.

Pipeline per e-mail:
    1. PII tokeniseren (lokaal) — nooit echte PII naar LLM
    2. Injection guard checken
    3. Categorie-hint toevoegen op basis van classificatieresultaat
    4. LLM-call met strict JSON-schema (aparte call van classificatie)
    5. Output valideren — ontbrekende velden → fallback
    6. requires_human_review bepalen op basis van confidence + warnings

⛔ Geen send()-methode. Verzenden is altijd een menselijke handeling.
"""

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

from agents.llm_client import LLMClient, LLMProviderError
from agents.classifier_agent import ClassificationResult, EmailCategory
from agents.prompts.draft_generation import (
    CATEGORY_HINTS,
    DRAFT_INSTRUCTION,
    FEW_SHOT_EXAMPLES,
    SYSTEM_PROMPT,
)
from config import settings
from security.audit import AuditAction, audit_log
from security.injection_guard import InjectionGuard, RiskLevel
from security.pii_detector import tokenize_pii

logger = structlog.get_logger(__name__)

# Confidence-drempel voor menselijke review
HUMAN_REVIEW_THRESHOLD = 0.75

# Harde maximumlimiet voor gegenereerde concepten (woorden)
MAX_DRAFT_WORDS = 300

# Maximale inputlengte voor de LLM (karakters)
MAX_INPUT_CHARS = 6_000


class ReviewStatus(str, Enum):
    READY_FOR_REVIEW = "ready_for_review"   # hoge confidence, geen waarschuwingen
    REQUIRES_REVIEW  = "requires_review"    # lage confidence of waarschuwingen
    BLOCKED          = "blocked"            # injection of kritieke fout


@dataclass
class DraftResult:
    """
    Resultaat van de draft-generatieagent.

    draft_text is altijd een concept — nooit automatisch verzonden.
    """

    # ── Verplichte outputvelden ───────────────────────────────
    draft_text:           str
    review_status:        ReviewStatus
    confidence:           float          # 0.40 – 1.00

    # ── Kwaliteitsvelden ─────────────────────────────────────
    warnings:             list[str] = field(default_factory=list)
    requires_human_review: bool = True   # standaard True — expliciet uitschakelen

    # ── Traceerbaarheid ───────────────────────────────────────
    model_id:             str = ""
    prompt_version:       str = "v1"
    processing_time_ms:   int = 0
    word_count:           int = 0
    tone:                 str = "formal"

    # ── Diagnosevlaggen ───────────────────────────────────────
    used_fallback:        bool = False
    injection_risk:       str = "safe"


@dataclass
class DraftInput:
    """Invoer voor de draft-generatieagent."""
    subject:              str
    body:                 str
    classification:       ClassificationResult
    email_id:             str = ""
    thread_context:       str = ""   # Optionele eerdere berichten in de thread


class DraftGenerationAgent:
    """
    Genereert professionele conceptantwoorden via Claude.

    Regels (altijd van toepassing):
      - Geen verzonnen prijzen, data of afspraken
      - Geen aannames over onbekende context
      - Kort en professioneel (max 300 woorden)
      - Ontbrekende info → in het concept vragen, niet invullen
      - Nooit automatisch verzenden

    Gebruik:
        agent = DraftGenerationAgent()
        result = agent.generate(DraftInput(
            subject="Re: Offerteverzoek",
            body="Kunnen jullie een offerte sturen?",
            classification=classification_result,
            email_id="uuid-hier",
        ))
    """

    def __init__(self) -> None:
        self._client = LLMClient()   # provider bepaald door LLM_PROVIDER in .env
        self._guard  = InjectionGuard()

    def generate(self, input_data: DraftInput) -> DraftResult:
        """
        Genereer een concept voor een geclassificeerde e-mail.

        Returns altijd een DraftResult — gooit nooit een exception.
        Bij iedere fout: fallback-concept met requires_human_review=True.
        """
        start = time.perf_counter()

        # ── Stap 1: Samengestelde tekst ───────────────────────
        combined = self._combine_input(input_data)

        # ── Stap 2: Injection guard ───────────────────────────
        guard_result = self._guard.check(combined)
        if guard_result.risk_level == RiskLevel.BLOCKED:
            audit_log(
                AuditAction.SECURITY_INJECTION_DETECTED,
                entity_type="email",
                entity_id=input_data.email_id or None,
                metadata={"patterns": guard_result.matched_patterns, "stage": "draft_gen"},
            )
            return self._blocked_result(self._elapsed(start))

        # ── Stap 3: PII tokeniseren ───────────────────────────
        tokenization = tokenize_pii(guard_result.sanitized_text)
        safe_text = tokenization.anonymized_text

        # ── Stap 4: LLM-call ─────────────────────────────────
        try:
            raw = self._call_llm(safe_text, input_data.classification.category)
        except Exception as exc:
            logger.error("draft_agent_llm_failed", error=type(exc).__name__)
            return self._fallback_result(self._elapsed(start), reason="llm_error")

        # ── Stap 5: Valideren ─────────────────────────────────
        try:
            result = self._parse_and_validate(raw)
        except (ValueError, KeyError) as exc:
            logger.warning("draft_agent_parse_failed", error=str(exc))
            return self._fallback_result(self._elapsed(start), reason="parse_error")

        # ── Stap 6: Metadata invullen ─────────────────────────
        result.model_id           = self._client._model  # provider-agnostisch
        result.processing_time_ms = self._elapsed(start)
        result.injection_risk     = guard_result.risk_level.value

        # ── Audit log ─────────────────────────────────────────
        audit_log(
            AuditAction.DRAFT_CREATED,
            entity_type="email",
            entity_id=input_data.email_id or None,
            metadata={
                "confidence":           result.confidence,
                "review_status":        result.review_status.value,
                "requires_human_review": result.requires_human_review,
                "word_count":           result.word_count,
                "warnings_count":       len(result.warnings),
                "category":             input_data.classification.category.value,
            },
        )

        logger.info(
            "draft_generated",
            review_status=result.review_status.value,
            confidence=result.confidence,
            requires_human_review=result.requires_human_review,
            word_count=result.word_count,
        )

        return result

    # ── Privémethoden ─────────────────────────────────────────────────────────

    @staticmethod
    def _combine_input(data: DraftInput) -> str:
        """Combineer onderwerp, body en optionele thread-context."""
        parts: list[str] = []
        if data.subject.strip():
            parts.append(f"Onderwerp: {data.subject.strip()}")
        if data.body.strip():
            parts.append(data.body.strip())
        if data.thread_context.strip():
            parts.append(f"\n--- Eerdere berichten ---\n{data.thread_context.strip()}")
        return "\n\n".join(parts)[:MAX_INPUT_CHARS]

    def _call_llm(self, safe_text: str, category: EmailCategory) -> dict[str, Any]:
        """
        Roep de LLM aan via LLMClient (Anthropic of Ollama).

        Retry-logica zit in LLMClient — geen @retry decorator nodig hier.
        E-mailinhoud staat altijd in XML-tags — nooit los in de prompt.
        """
        category_hint = CATEGORY_HINTS.get(category.value, CATEGORY_HINTS["Overig"])

        example = next(
            (e for e in FEW_SHOT_EXAMPLES if e["category"] == category.value),
            FEW_SHOT_EXAMPLES[0],
        )

        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"{DRAFT_INSTRUCTION}\n"
                    f"Categorie-context: {category_hint}\n\n"
                    f"{self._guard.wrap_for_llm(example['email'])}"
                ),
            },
            {
                "role": "assistant",
                "content": json.dumps(example["output"], ensure_ascii=False),
            },
            {
                "role": "user",
                "content": (
                    f"{DRAFT_INSTRUCTION}\n"
                    f"Categorie-context: {category_hint}\n\n"
                    f"{self._guard.wrap_for_llm(safe_text)}"
                ),
            },
        ]

        try:
            response = self._client.complete(
                system=SYSTEM_PROMPT,
                messages=messages,
                max_tokens=1024,
            )
        except LLMProviderError as exc:
            raise exc  # opgevangen in generate() → fallback

        raw_text = response.text
                    f"{self._guard.wrap_for_llm(safe_text)}"
                ),
            },
        ]

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )

        raw_text = response.content[0].text.strip()

        # Verwijder eventuele markdown-code-fences
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            # FIX M-5: Geen raw LLM-uitvoer in exception-berichten.
            raise ValueError(
                f"LLM retourneerde geen geldig JSON "
                f"(lengte: {len(raw_text)}, begint met: {raw_text[:20]!r})"
            ) from exc

    def _parse_and_validate(self, data: dict[str, Any]) -> DraftResult:
        """
        Valideer de LLM-output.

        Strikte checks — bij twijfel liever een ValueError dan stilletjes
        een ongeldig concept doorgeven.
        """
        # ── Draft-tekst ───────────────────────────────────────
        draft_text = str(data.get("draft_text", "")).strip()
        if not draft_text:
            raise ValueError("draft_text ontbreekt of leeg")

        # Harde woordlimiet afdwingen
        words = draft_text.split()
        if len(words) > MAX_DRAFT_WORDS:
            draft_text = " ".join(words[:MAX_DRAFT_WORDS]) + "\n\n[…concept ingekort door systeem]"

        # ── Confidence ────────────────────────────────────────
        raw_conf = data.get("confidence")
        if not isinstance(raw_conf, (int, float)):
            raise ValueError(f"Confidence is geen getal: {raw_conf!r}")
        confidence = float(raw_conf)
        if not (0.40 <= confidence <= 1.0):
            raise ValueError(f"Confidence buiten bereik: {confidence}")

        # ── Waarschuwingen ────────────────────────────────────
        warnings_raw = data.get("warnings", [])
        warnings = [str(w)[:200] for w in warnings_raw if w][:10]  # Max 10

        # ── Review-status bepalen ─────────────────────────────
        requires_review = confidence < HUMAN_REVIEW_THRESHOLD or bool(warnings)
        if requires_review:
            review_status = ReviewStatus.REQUIRES_REVIEW
        else:
            review_status = ReviewStatus.READY_FOR_REVIEW

        # ── Overige velden ────────────────────────────────────
        tone = str(data.get("tone", "formal"))
        if tone not in ("formal", "semi-formal"):
            tone = "formal"

        return DraftResult(
            draft_text=draft_text,
            review_status=review_status,
            confidence=round(confidence, 2),
            warnings=warnings,
            requires_human_review=requires_review,
            word_count=len(draft_text.split()),
            tone=tone,
        )

    # ── Fallback-resultaten ───────────────────────────────────────────────────

    @staticmethod
    def _fallback_result(processing_ms: int, reason: str) -> DraftResult:
        """
        Altijd-veilige fallback: leeg concept + requires_human_review=True.

        Nooit raden of zelf tekst verzinnen.
        """
        logger.warning("draft_agent_fallback_used", reason=reason)
        return DraftResult(
            draft_text=(
                "Geachte heer/mevrouw,\n\n"
                "Hartelijk dank voor uw bericht. Wij nemen uw verzoek in behandeling "
                "en nemen zo spoedig mogelijk contact met u op.\n\n"
                "Met vriendelijke groet,\n[NAAM MEDEWERKER]"
            ),
            review_status=ReviewStatus.REQUIRES_REVIEW,
            confidence=0.0,
            warnings=[f"Automatische conceptgeneratie mislukt ({reason}). Handmatige aanpassing vereist."],
            requires_human_review=True,
            processing_time_ms=processing_ms,
            used_fallback=True,
        )

    @staticmethod
    def _blocked_result(processing_ms: int) -> DraftResult:
        """Geblokkeerd resultaat bij gedetecteerde prompt-injectie."""
        return DraftResult(
            draft_text="",
            review_status=ReviewStatus.BLOCKED,
            confidence=0.0,
            warnings=["Conceptgeneratie geblokkeerd wegens vermoedelijke injectiepoging."],
            requires_human_review=True,
            processing_time_ms=processing_ms,
            injection_risk="blocked",
            used_fallback=True,
        )

    @staticmethod
    def _elapsed(start: float) -> int:
        return round((time.perf_counter() - start) * 1000)
