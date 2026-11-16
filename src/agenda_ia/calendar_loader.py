"""Reads an .ics file (Google Calendar or Outlook export) into plain events.

Recurring events are expanded inside a date window, every time is converted to
São Paulo time, and attendee e-mails are reduced to names (data minimization:
the model only needs to know who, not their address).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import icalendar
import recurring_ical_events

from .config import TZ

WEEKDAYS = ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"]

# Lines that start the boilerplate Teams/Meet/Zoom add to invitations.
_BOILERPLATE_START = re.compile(
    r"^\s*(_{5,}|-{5,}|Reunião do Microsoft Teams|Microsoft Teams meeting|Participe pelo Google Meet|"
    r"Join with Google Meet|Entrar com o Google Meet|Join Zoom Meeting|Ingressar na reunião Zoom)",
    re.IGNORECASE,
)
_URL = re.compile(r"https?://\S+")


@dataclass(frozen=True)
class Event:
    uid: str
    title: str
    start: datetime  # timezone-aware, São Paulo
    end: datetime
    all_day: bool
    location: str
    attendees: tuple[str, ...]
    description: str

    @property
    def key(self) -> str:
        """Unique per occurrence: a weekly meeting has the same UID every week."""
        return f"{self.uid}|{self.start.isoformat()}"

    def search_text(self) -> str:
        """What the keyword and vector searches look at (no dates: dates are filtered apart)."""
        parts = [self.title, self.location, ", ".join(self.attendees), self.description]
        return "\n".join(p for p in parts if p)

    def context_text(self) -> str:
        """How the event is shown to the language model."""
        lines = [f"Título: {self.title}", f"Quando: {format_when(self)}"]
        if self.location:
            lines.append(f"Local: {self.location}")
        if self.attendees:
            lines.append(f"Participantes: {', '.join(self.attendees)}")
        if self.description:
            lines.append(f"Notas: {self.description}")
        return "\n".join(lines)


def format_when(event: Event) -> str:
    day = f"{WEEKDAYS[event.start.weekday()]}, {event.start:%d/%m/%Y}"
    if event.all_day:
        last_day = (event.end - timedelta(days=1)).date()
        if last_day > event.start.date():
            return f"{day} a {last_day:%d/%m/%Y} (dia inteiro)"
        return f"{day} (dia inteiro)"
    return f"{day}, {event.start:%H:%M}–{event.end:%H:%M}"


def load_events(path: str | Path, window_start: date, window_end: date) -> list[Event]:
    """Every occurrence that starts in [window_start, window_end), sorted by start."""
    calendar = icalendar.Calendar.from_ical(Path(path).read_bytes())
    occurrences = recurring_ical_events.of(calendar).between(window_start, window_end)
    events = []
    for component in occurrences:
        if str(component.get("STATUS", "")).upper() == "CANCELLED":
            continue
        events.append(_to_event(component))
    events.sort(key=lambda e: e.start)
    return events


def _to_event(component) -> Event:
    raw_start = component.decoded("DTSTART")
    all_day = not isinstance(raw_start, datetime)
    start = _to_local(raw_start)
    if component.get("DTEND") is not None:
        end = _to_local(component.decoded("DTEND"))
    elif component.get("DURATION") is not None:
        end = start + component.decoded("DURATION")
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(hours=1))

    return Event(
        uid=str(component.get("UID", "")) or f"sem-uid-{start.isoformat()}",
        title=_clean_line(component.get("SUMMARY", "")) or "(sem título)",
        start=start,
        end=end,
        all_day=all_day,
        location=_clean_line(component.get("LOCATION", "")),
        attendees=_attendees(component),
        description=clean_description(str(component.get("DESCRIPTION", ""))),
    )


def _to_local(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        # A time with no zone ("floating") is taken as local time.
        return value.replace(tzinfo=TZ) if value.tzinfo is None else value.astimezone(TZ)
    return datetime.combine(value, time.min, tzinfo=TZ)


def _attendees(component) -> tuple[str, ...]:
    raw = component.get("ATTENDEE")
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raw = [raw]
    names = []
    for attendee in raw:
        name = attendee.params.get("CN") if hasattr(attendee, "params") else None
        if not name:
            # No display name: keep only the part before the @.
            name = str(attendee).removeprefix("mailto:").removeprefix("MAILTO:").split("@")[0]
        name = _clean_line(name)
        if name and name not in names:
            names.append(name)
    return tuple(names)


def clean_description(text: str) -> str:
    """Drops meeting-link boilerplate and URLs, keeps the human notes."""
    kept = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if _BOILERPLATE_START.match(line):
            break
        line = _URL.sub("", line).strip()
        if line:
            kept.append(line)
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


def _clean_line(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()
