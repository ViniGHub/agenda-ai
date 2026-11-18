"""The morning e-mail: today's appointments, sent from the person's own Gmail to themself.

No language model on this path: the e-mail is the exact list from the agenda, so it can never
invent or drop an appointment.
"""

from __future__ import annotations

import html
from datetime import date, datetime, time, timedelta
from email.message import EmailMessage

from .calendar_loader import WEEKDAYS, Event
from .config import TZ
from .store import EventStore

SEND_HOUR = "08:00"  # the Cloud Scheduler job (deploy/agendador.sh) fires at this time, São Paulo


def todays_events(store: EventStore, day: date) -> list[Event]:
    """Events on the day, including ones that started before and are still going (a trip, a holiday)."""
    start = datetime.combine(day, time.min, tzinfo=TZ)
    end = start + timedelta(days=1)
    earlier = store.events_between(int((start - timedelta(days=31)).timestamp()), int(end.timestamp()), limit=500)
    events = [s.event for s in earlier if s.event.end > start]
    return sorted(events, key=lambda e: (not e.all_day, e.start))


def _day_name(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()].removesuffix('-feira')}, {day:%d/%m}"


def _when(event: Event, day: date) -> str:
    if event.all_day or (event.start.date() < day and event.end.date() > day):
        return "dia todo"
    starts = f"{event.start:%H:%M}" if event.start.date() == day else "antes"
    ends = f"{event.end:%H:%M}" if event.end.date() == day else "depois"
    return f"{starts}–{ends}"


def _details(event: Event) -> str:
    parts = []
    if event.location:
        parts.append(event.location)
    if event.attendees:
        parts.append("com " + ", ".join(event.attendees))
    return " · ".join(parts)


def compose(events: list[Event], day: date, app_url: str) -> tuple[str, str, str]:
    """(subject, plain text, HTML)."""
    count = len(events)
    summary = "dia livre" if count == 0 else f"{count} compromisso{'s' if count > 1 else ''}"
    subject = f"Agenda de hoje ({_day_name(day)}): {summary}"

    lines = [f"Hoje, {_day_name(day)}:", ""]
    if not events:
        lines.append("Nenhum compromisso na agenda.")
    for event in events:
        lines.append(f"{_when(event, day):<12} {event.title}")
        if _details(event):
            lines.append(f"{'':<12} {_details(event)}")
        if event.description:
            lines.append(f"{'':<12} {event.description[:200]}")
    lines += ["", f"Abrir a agenda: {app_url}", "Para não receber mais, desligue o e-mail das 8h na página da agenda."]
    text = "\n".join(lines)

    rows = []
    for event in events:
        extra = "".join(
            f'<div style="color:#6b6257;font-size:13px">{html.escape(part)}</div>'
            for part in (_details(event), event.description[:200]) if part
        )
        rows.append(
            '<tr>'
            f'<td style="padding:8px 12px 8px 0;vertical-align:top;font-family:monospace;color:#6b6257;white-space:nowrap">{html.escape(_when(event, day))}</td>'
            f'<td style="padding:8px 0;vertical-align:top"><strong>{html.escape(event.title)}</strong>{extra}</td>'
            '</tr>'
        )
    body = (
        f'<table style="border-collapse:collapse">{"".join(rows)}</table>' if rows
        else '<p>Nenhum compromisso na agenda.</p>'
    )
    page = (
        '<div style="font-family:Georgia,serif;font-size:15px;color:#2b2621;max-width:560px">'
        f'<p style="font-size:18px;margin:0 0 12px">Hoje, {html.escape(_day_name(day))}</p>{body}'
        f'<p style="margin-top:20px;font-size:13px"><a href="{html.escape(app_url)}">Abrir a agenda</a> · '
        'para não receber mais, desligue o e-mail das 8h na página da agenda.</p></div>'
    )
    return subject, text, page


def message(to: str, subject: str, text: str, page: str) -> bytes:
    """A MIME message from the person to themself (Gmail only sends as the account's own address)."""
    mail = EmailMessage()
    mail["To"] = to
    mail["From"] = to
    mail["Subject"] = subject
    mail.set_content(text, cte="quoted-printable")  # 7-bit safe on every relay, accents included
    mail.add_alternative(page, subtype="html")
    return mail.as_bytes()
