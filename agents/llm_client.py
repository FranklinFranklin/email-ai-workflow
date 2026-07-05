"""
Uniforme LLM-client — ondersteunt Anthropic en Ollama.

Alle agents gebruiken uitsluitend deze adapter.
De rest van de code weet niet welke provider actief is.

Provider-selectie via LLM_PROVIDER in .env:
    LLM_PROVIDER=anthropic   → Anthropic Claude (standaard)
    LLM_PROVIDER=ollama      → Lokaal model via Ollama

ISO27001 A.12.1 — Gedocumenteerde operationele procedures.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config import settings

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class LLMResponse:
    """Genormaliseerde respons — onafhankelijk van provider."""
    text: str
    model: str
    provider: str
    duration_ms: int


class LLMProviderError(Exception):
    """Tijdelijke provider-fout — retry is zinvol."""


class LLMClient:
    """
    Provider-agnostische LLM-client.

    Gebruik:
        client = LLMClient()
        response = client.complete(
            system="Je bent een classificatieassistent.",
            messages=[{"role": "user", "content": "..."}],
            max_tokens=512,
        )
        print(response.text)

    Retry: 3 pogingen met exponentiële back-off ingebakken.
    """

    def __init__(self) -> None:
        self._provider = settings.LLM_PROVIDER
        self._model    = self._resolve_model()
        self._client   = self._build_client()

    # ── Publieke interface ────────────────────────────────────────────────────

    def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """
        Stuur een chat-verzoek naar de geconfigureerde provider.

        Args:
            system:     Systeemprompt (instructies voor het model).
            messages:   Lijst van {"role": "user"/"assistant", "content": "..."}.
            max_tokens: Maximum tokens in de respons.

        Returns:
            LLMResponse met de gegenereerde tekst.

        Raises:
            LLMProviderError: Bij tijdelijke API-fouten (na alle retries).
            ValueError:       Bij configuratiefouten.
        """
        start = time.perf_counter()

        if self._provider == "anthropic":
            text = self._complete_anthropic(system, messages, max_tokens)
        else:
            text = self._complete_ollama(system, messages, max_tokens)

        duration_ms = round((time.perf_counter() - start) * 1000)

        logger.debug(
            "llm_complete",
            provider=self._provider,
            model=self._model,
            duration_ms=duration_ms,
        )

        return LLMResponse(
            text=text,
            model=self._model,
            provider=self._provider,
            duration_ms=duration_ms,
        )

    # ── Anthropic ─────────────────────────────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(LLMProviderError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _complete_anthropic(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int,
    ) -> str:
        try:
            import anthropic as _anthropic
            response = self._client.messages.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
            )
            return response.content[0].text.strip()
        except _anthropic.APIStatusError as exc:
            logger.warning("anthropic_api_error", status=exc.status_code)
            raise LLMProviderError(f"Anthropic API-fout: {exc.status_code}") from exc
        except Exception as exc:
            raise LLMProviderError("Anthropic onbereikbaar") from exc

    # ── Ollama (OpenAI-compatibele API) ───────────────────────────────────────

    @retry(
        retry=retry_if_exception_type(LLMProviderError),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _complete_ollama(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int,
    ) -> str:
        """
        Ollama gebruikt de OpenAI-compatibele API op /v1.
        Systeemprompt gaat als eerste bericht mee.
        """
        try:
            all_messages = [{"role": "system", "content": system}, *messages]
            response = self._client.chat.completions.create(
                model=self._model,
                messages=all_messages,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        except Exception as exc:
            logger.warning("ollama_api_error", error=type(exc).__name__)
            raise LLMProviderError(
                f"Ollama onbereikbaar op {settings.OLLAMA_BASE_URL}. "
                "Controleer of Ollama draait: `ollama serve`"
            ) from exc

    # ── Initialisatie ─────────────────────────────────────────────────────────

    def _resolve_model(self) -> str:
        if self._provider == "ollama":
            return settings.OLLAMA_MODEL
        return settings.ANTHROPIC_MODEL

    def _build_client(self):
        if self._provider == "ollama":
            import openai as _openai
            logger.info(
                "llm_provider_ollama",
                base_url=settings.OLLAMA_BASE_URL,
                model=self._model,
            )
            return _openai.OpenAI(
                base_url=settings.OLLAMA_BASE_URL,
                api_key="ollama",  # Ollama vereist geen echte sleutel
                timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
            )

        # Anthropic (standaard)
        import anthropic as _anthropic
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError(
                "ANTHROPIC_API_KEY is niet ingesteld. "
                "Stel LLM_PROVIDER=ollama in voor lokaal gebruik."
            )
        logger.info("llm_provider_anthropic", model=self._model)
        return _anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.ANTHROPIC_TIMEOUT_SECONDS,
        )
