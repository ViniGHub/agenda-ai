"""Language model calls that must return JSON.

The interface is tiny on purpose: the rest of the code (and the tests) only
needs "send a system and a user message, get a dict back".
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Protocol

from .config import LLM_MODEL, ROOT


class LLMError(RuntimeError):
    """The model could not be reached or did not return valid JSON."""


@dataclass
class Usage:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class JSONModel(Protocol):
    usage: Usage

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict: ...


@dataclass
class UnavailableModel:
    """Stand-in when there is no API key: every call fails, so the app degrades to search-only answers."""

    reason: str = "LLM indisponível"
    usage: Usage = field(default_factory=Usage)

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        raise LLMError(self.reason)


@dataclass
class GroqModel:
    model: str = field(default_factory=lambda: os.getenv("AGENDA_IA_MODELO", LLM_MODEL))
    usage: Usage = field(default_factory=Usage)

    def __post_init__(self) -> None:
        from dotenv import load_dotenv
        from groq import Groq

        load_dotenv(ROOT / ".env")
        if not os.getenv("GROQ_API_KEY"):
            raise LLMError("Falta a GROQ_API_KEY: copie .env.example para .env e preencha.")
        # The SDK already retries 429 (rate limit) and 5xx with exponential backoff.
        self._client = Groq(max_retries=4, timeout=60)

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        from groq import GroqError

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                temperature=0,
                # gpt-oss thinks before answering; "low" keeps us inside 8k tokens/minute.
                reasoning_effort="low",
                max_completion_tokens=max_tokens,
            )
        except GroqError as error:
            raise LLMError(f"Falha ao chamar o Groq: {error}") from error

        self.usage.calls += 1
        if response.usage:
            self.usage.prompt_tokens += response.usage.prompt_tokens or 0
            self.usage.completion_tokens += response.usage.completion_tokens or 0
        content = response.choices[0].message.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as error:
            raise LLMError(f"O modelo não devolveu JSON válido: {content[:200]!r}") from error
        if not isinstance(data, dict):
            raise LLMError("O modelo devolveu JSON, mas não um objeto.")
        return data
