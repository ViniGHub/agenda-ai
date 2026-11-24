"""Creating events from the form and importing an .ics file.

The form sends a date and local times (São Paulo); the server builds the timezone-aware
event, so the browser's clock and timezone never matter.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

from pydantic import BaseModel, Field, field_validator, model_validator

from .calendar_loader import Event, parse_events
from ..config import TZ

MAX_IMPORT_CHARS = 2_000_000  # a few years of a busy calendar
MAX_IMPORT_EVENTS = 2_000


class ImportError_(ValueError):
    """The uploaded content is not a usable iCalendar file."""


class EventDraft(BaseModel):
    titulo: str = Field(min_length=1, max_length=120)
    data: date
    dia_inteiro: bool = False
    inicio: time | None = None  # required unless dia_inteiro
    fim: time | None = None  # optional; defaults to one hour after inicio
    local: str = Field(default="", max_length=120)
    participantes: list[str] = Field(default_factory=list, max_length=10)
    notas: str = Field(default="", max_length=1000)

    @field_validator("titulo", "local", "notas")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("participantes")
    @classmethod
    def _names(cls, names: list[str]) -> list[str]:
        cleaned = [" ".join(n.split()) for n in names if n.strip()]
        if any(len(n) > 60 for n in cleaned):
            raise ValueError("nome de participante longo demais")
        return cleaned

    @model_validator(mode="after")
    def _check(self) -> "EventDraft":
        if not self.titulo:
            raise ValueError("o título não pode ficar vazio")
        today = datetime.now(TZ).date()
        if abs((self.data - today).days) > 730:
            raise ValueError("a data precisa estar a menos de dois anos de hoje")
        if not self.dia_inteiro:
            if self.inicio is None:
                raise ValueError("informe o horário de início ou marque dia inteiro")
            if self.fim is not None and self.fim <= self.inicio:
                raise ValueError("o fim precisa ser depois do início")
        return self

    def to_event(self) -> Event:
        if self.dia_inteiro:
            start = datetime.combine(self.data, time.min, tzinfo=TZ)
            end = start + timedelta(days=1)
        else:
            assert self.inicio is not None
            start = datetime.combine(self.data, self.inicio, tzinfo=TZ)
            end = datetime.combine(self.data, self.fim, tzinfo=TZ) if self.fim else start + timedelta(hours=1)
        return Event(
            uid=f"cadastro-{uuid.uuid4()}",
            title=self.titulo,
            start=start,
            end=end,
            all_day=self.dia_inteiro,
            location=self.local,
            attendees=tuple(self.participantes),
            description=self.notas,
        )


def import_events(content: str, first_day: date, last_day: date) -> list[Event]:
    """Occurrences of an uploaded .ics inside the agenda's window (recurrences expanded, cancelled ones dropped)."""
    if len(content) > MAX_IMPORT_CHARS:
        raise ImportError_("O arquivo é grande demais (máximo de 2 MB).")
    if "BEGIN:VCALENDAR" not in content[:2000].upper():
        raise ImportError_("Isso não parece um arquivo .ics (iCalendar).")
    try:
        events = parse_events(content.encode("utf-8"), first_day, last_day + timedelta(days=1))
    except Exception as error:  # icalendar raises several kinds of errors for broken files
        raise ImportError_("Não consegui ler esse arquivo .ics.") from error
    if len(events) > MAX_IMPORT_EVENTS:
        raise ImportError_(f"O arquivo tem mais de {MAX_IMPORT_EVENTS} compromissos no período.")
    return events
