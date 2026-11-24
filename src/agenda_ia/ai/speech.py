"""Voice messages: audio -> text, which then goes down the same path as a typed chat message.

Turning speech into text needs a speech model; audio libraries only read, cut and convert files.
Whisper runs on Groq (the same provider and free tier as the language model), so nothing heavy runs
in this container. The audio is sent to Groq once and dropped: it is never written to disk or logged.

Whisper is known to "hear" text in silence or noise ("Legendas pela comunidade Amara.org",
"Obrigado."), learned from subtitled videos. Since a voice message can write to the agenda, those
are dropped: segments the model itself rates as probably not speech, and the stock phrases.
No transcription prompt is used either, because over silence Whisper may repeat the prompt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

from ..config import AUDIO_MODEL, ROOT
from .llm import LLMError

# Upload content types accepted, and the file extension Groq expects for each.
AUDIO_TYPES = {
    "audio/webm": "webm",  # Chrome, Firefox and Android
    "video/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",  # Safari and iPhone
    "audio/x-m4a": "m4a",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
}

NO_SPEECH = 0.6  # Whisper's own probability that a segment is not speech
_STOCK_PHRASES = ("amara.org", "obrigado por assistir", "inscreva-se", "legendas pela", "legendado por")
_ALONE = {"obrigado", "obrigada", "tchau", "e aí", "ok", "hum", "hmm"}


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, filename: str) -> str: ...


def clean_transcript(data: dict) -> str:
    """The text of a verbose_json transcription, without likely non-speech. '' when nothing is left."""
    segments = data.get("segments")
    if isinstance(segments, list) and segments:
        kept = [
            str(segment.get("text") or "")
            for segment in segments
            if isinstance(segment, dict) and _number(segment.get("no_speech_prob")) < NO_SPEECH
        ]
        text = " ".join(kept)
    else:
        text = str(data.get("text") or "")
    text = " ".join(text.split())
    plain = text.casefold().strip(" .,!?…")
    if not plain or plain in _ALONE or any(phrase in plain for phrase in _STOCK_PHRASES):
        return ""
    return text


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


@dataclass
class UnavailableTranscriber:
    """Stand-in when there is no API key: the microphone button is hidden."""

    reason: str = "transcrição indisponível"

    def transcribe(self, audio: bytes, filename: str) -> str:
        raise LLMError(self.reason)


@dataclass
class GroqTranscriber:
    model: str = field(default_factory=lambda: os.getenv("AGENDA_IA_MODELO_AUDIO", AUDIO_MODEL))

    def __post_init__(self) -> None:
        from dotenv import load_dotenv
        from groq import Groq

        load_dotenv(ROOT / ".env")
        if not os.getenv("GROQ_API_KEY"):
            raise LLMError("Falta a GROQ_API_KEY: copie .env.example para .env e preencha.")
        self._client = Groq(max_retries=2, timeout=60)

    def transcribe(self, audio: bytes, filename: str) -> str:
        from groq import GroqError

        try:
            response = self._client.audio.transcriptions.create(
                file=(filename, audio),
                model=self.model,
                language="pt",
                response_format="verbose_json",  # brings each segment's no-speech probability
                temperature=0,
            )
        except GroqError as error:
            raise LLMError(f"Falha ao transcrever no Groq: {error}") from error
        data = response.to_dict() if hasattr(response, "to_dict") else dict(response)
        return clean_transcript(data)
