"""
Prompt injection bescherming.

ISO27001 A.12.2 — Bescherming tegen kwaadaardige code.
SOC2 CC6.8      — Preventie van ongeautoriseerde software.

Inkomende e-mails zijn onbetrouwbare input. Een aanvaller kan
instructies in de e-mailtekst verstoppen om LLM-gedrag te sturen.

Aanpak:
    1. Detecteer bekende injection-patronen (flag + loggen)
    2. Wrap e-mailinhoud altijd in XML-grenzen (structurele scheiding)
    3. Valideer LLM-output structureel (JSON-schema) — geen vrije instructies
"""

import re
from dataclasses import dataclass
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class RiskLevel(str, Enum):
    SAFE      = "safe"
    SUSPICIOUS = "suspicious"
    BLOCKED   = "blocked"


@dataclass(frozen=True)
class GuardResult:
    risk_level: RiskLevel
    matched_patterns: list[str]
    sanitized_text: str


# ── Bekende injection-patronen ────────────────────────────────────────────────
# Volgorde: meest gevaarlijk eerst
_BLOCKED_PATTERNS: list[tuple[str, str]] = [
    # Directe instructie-overschrijving
    (r"ignore\s+(all\s+)?previous\s+instructions?",        "instruction-override"),
    (r"disregard\s+(all\s+)?prior\s+instructions?",         "instruction-override"),
    (r"forget\s+(everything|all)\s+(you|i)\s+(know|told)", "instruction-override"),
    # Rol-injectie
    (r"you\s+are\s+now\s+(a|an)\s+\w+",                   "role-injection"),
    (r"act\s+as\s+(a|an|if)\s+",                           "role-injection"),
    (r"pretend\s+(to\s+be|you\s+are)",                     "role-injection"),
    (r"your\s+new\s+(role|persona|task|job)\s+is",         "role-injection"),
    # Systeem-prompt-extractie — staat ook woorden als "internal" toe vóór het doelobject
    (r"(repeat|print|show|reveal)\s+your\s+.{0,30}(prompt|instructions?|configuration)", "prompt-leak"),
    (r"what\s+(are\s+your\s+instructions?|instructions?\s+you\s+received|is\s+your\s+system\s+prompt)", "prompt-leak"),
    # Jailbreak-pogingen
    # \b = woordgrens — voorkomt false positives op "Bedankt" (bevat "dan")
    (r"(\bDAN\b|jailbreak|developer\s+mode|god\s+mode)",   "jailbreak"),
    (r"hypothetically\s+speaking.{0,40}(send|email|forward)", "jailbreak"),
    # Indirecte data-exfiltratie
    (r"forward\s+(this|all|my)\s+(email|message|data)",    "exfiltration"),
    (r"send\s+(a\s+copy|this).{0,60}(to\s+(my|external|\w+@))", "exfiltration"),
]

_SUSPICIOUS_PATTERNS: list[tuple[str, str]] = [
    (r"<\s*script",                                         "script-tag"),
    (r"javascript\s*:",                                     "js-protocol"),
    # FIX M-3: Karakter-klasse-exclusies ipv lazy wildcards met re.DOTALL.
    # r"\{\{.*?\}\}" met DOTALL kan O(n²) backtracking veroorzaken op
    # lange inputs zonder sluitende }}. Nieuwe patronen zijn lineair: O(n).
    (r"\{\{[^}]{0,200}\}\}",                               "template-injection"),
    (r"\$\{[^}]{0,200}\}",                                 "template-injection"),
    (r"--\s*(system|user|assistant)\s*:",                   "role-delimiter"),
    (r"\[INST\]|\[\/INST\]|<\|im_start\|>",                "llm-special-token"),
]

# Gecompileerde regex-objecten (eenmalig bij module-load)
# DOTALL verwijderd uit SUSPICIOUS — karakterklassen [^}] zijn al newline-safe
_compiled_blocked = [
    (re.compile(pattern, re.IGNORECASE | re.DOTALL), label)
    for pattern, label in _BLOCKED_PATTERNS
]
_compiled_suspicious = [
    (re.compile(pattern, re.IGNORECASE), label)   # FIX M-3: geen DOTALL meer
    for pattern, label in _SUSPICIOUS_PATTERNS
]


class InjectionGuard:
    """
    Controleert tekst op prompt-injection vóór LLM-verwerking.

    Gebruik:
        guard = InjectionGuard()
        result = guard.check(email_body)

        if result.risk_level == RiskLevel.BLOCKED:
            # Weiger verwerking of flag voor handmatige review
            ...

        # Gebruik altijd sanitized_text voor de LLM-call
        llm_input = guard.wrap_for_llm(result.sanitized_text)
    """

    def check(self, text: str) -> GuardResult:
        """
        Controleer tekst op injection-pogingen.

        Logt een security-event bij SUSPICIOUS en BLOCKED.
        Gooit nooit een exception — geeft altijd een GuardResult terug.
        """
        if not text:
            return GuardResult(
                risk_level=RiskLevel.SAFE,
                matched_patterns=[],
                sanitized_text=text,
            )

        matched: list[str] = []
        risk = RiskLevel.SAFE

        # Check geblokkeerde patronen eerst
        for regex, label in _compiled_blocked:
            if regex.search(text):
                matched.append(label)
                risk = RiskLevel.BLOCKED

        # Check verdachte patronen als nog niet geblokkeerd
        if risk != RiskLevel.BLOCKED:
            for regex, label in _compiled_suspicious:
                if regex.search(text):
                    matched.append(label)
                    risk = RiskLevel.SUSPICIOUS

        if risk != RiskLevel.SAFE:
            logger.warning(
                "injection_attempt_detected",
                risk_level=risk,
                patterns=matched,
                # Nooit de originele tekst loggen — kan PII bevatten
            )

        return GuardResult(
            risk_level=risk,
            matched_patterns=matched,
            sanitized_text=self._strip_dangerous_html(text),
        )

    def wrap_for_llm(self, text: str) -> str:
        """
        Wikkel e-mailinhoud in XML-grenzen voor de LLM-prompt.

        Zorgt voor structurele scheiding tussen systeeminstructies
        en gebruikersinhoud — maakt injection moeilijker.

        Nooit de ruwe tekst direct in een f-string-prompt plakken.
        """
        return f"<email_content>\n{text}\n</email_content>"

    @staticmethod
    def _strip_dangerous_html(text: str) -> str:
        """Verwijder script-tags, event handlers en gevaarlijke URI-schema's."""
        if not text:
            return text

        # Herhaaldelijk gevaarlijke tags verwijderen (tegen geneste bypasses)
        prev = None
        current = text
        while prev != current:
            prev = current
            current = re.sub(
                r"<\s*(script|iframe|object|embed|applet|style|svg|meta|link)[^>]*>.*?</\s*\1\s*>",
                "[GEVAARLIJKE_TAG_VERWIJDERD]",
                current,
                flags=re.IGNORECASE | re.DOTALL,
            )
            current = re.sub(
                r"<\s*(script|iframe|object|embed|applet|style|svg|meta|link)[^>]*?/?>",
                "[GEVAARLIJKE_TAG_VERWIJDERD]",
                current,
                flags=re.IGNORECASE,
            )

        # Event handlers (onclick, onerror, onload, incl. zonder quotes)
        current = re.sub(
            r'\s+on\w+\s*=\s*(["\'][^"\']*["\']|[^\s>]+)',
            "",
            current,
            flags=re.IGNORECASE,
        )

        # javascript: en data: URI protocollen in attributen
        current = re.sub(
            r'(href|src|action)\s*=\s*["\']?\s*(javascript|vbscript|data\s*:\s*text/html)[^"\'>\s]*["\']?',
            r'\1="#"',
            current,
            flags=re.IGNORECASE,
        )

        return current
