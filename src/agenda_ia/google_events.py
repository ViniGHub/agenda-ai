"""Google Calendar events <-> the app's events.

Reading: one item of events.list (singleEvents=true, so each occurrence of a recurring event comes
on its own, with its own id) becomes an Event. Its iCalUID is the uid, the same one an .ics export
of that calendar carries, so an event imported earlier from Google's .ics is recognized (same uid
and start) instead of duplicated.

Writing: an Event becomes the body of events.insert / events.patch. People in the app are names,
not e-mail addresses, and adding them as Google attendees would e-mail them invitations, so they
travel inside the description, in a last line "Com: Ana; Bruno" that the reading side turns back
into names.
"""

from __future__ import annotations

import html
import re
from datetime import date, datetime, time, timedelta

from .calendar_loader import Event, clean_description
from .config import TZ

PEOPLE_PREFIX = "Com: "
_BREAK = re.compile(r"<\s*(br\s*/?|/p|/div|/li)\s*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


def event_from_google(item: dict) -> Event | None:
    """None for a cancelled occurrence or an item without a start."""
    if item.get("status") == "cancelled":
        return None
    start, end = item.get("start") or {}, item.get("end") or {}
    if start.get("dateTime"):
        begins = _instant(start["dateTime"])
        ends = _instant(end["dateTime"]) if end.get("dateTime") else begins + timedelta(hours=1)
        all_day = False
    elif start.get("date"):
        begins = datetime.combine(date.fromisoformat(start["date"]), time.min, tzinfo=TZ)
        ends = datetime.combine(date.fromisoformat(end["date"]), time.min, tzinfo=TZ) if end.get("date") else begins + timedelta(days=1)
        all_day = True
    else:
        return None
    notes, people = split_people(plain_text(item.get("description") or ""))
    names = [_line(n) for n in people]
    for attendee in item.get("attendees") or []:
        if attendee.get("self") or attendee.get("resource"):
            continue  # the person themself, and meeting rooms
        name = attendee.get("displayName") or str(attendee.get("email", "")).split("@")[0]
        names.append(_line(name))
    return Event(
        uid=str(item.get("iCalUID") or f"google-{item.get('id', '')}"),
        title=_line(item.get("summary") or "") or "(sem título)",
        start=begins,
        end=max(ends, begins),
        all_day=all_day,
        location=_line(item.get("location") or ""),
        attendees=tuple(dict.fromkeys(n for n in names if n)),  # unique, in order
        description=clean_description(notes),
    )


def google_body(event: Event) -> dict:
    """The fields of an event, as events.insert and events.patch take them."""
    description = event.description
    if event.attendees:
        description = (description + "\n\n" if description else "") + PEOPLE_PREFIX + "; ".join(event.attendees)
    if event.all_day:
        start, end = {"date": event.start.date().isoformat()}, {"date": event.end.date().isoformat()}
    else:
        start = {"dateTime": event.start.isoformat(), "timeZone": TZ.key}
        end = {"dateTime": event.end.isoformat(), "timeZone": TZ.key}
    return {"summary": event.title, "location": event.location, "description": description, "start": start, "end": end}


def split_people(text: str) -> tuple[str, list[str]]:
    """("notes", ["Ana", "Bruno"]) from a description whose last line is "Com: Ana; Bruno"."""
    lines = text.rstrip().split("\n")
    if lines and lines[-1].strip().startswith(PEOPLE_PREFIX):
        people = [p.strip() for p in lines[-1].strip()[len(PEOPLE_PREFIX):].split(";") if p.strip()]
        return "\n".join(lines[:-1]).strip(), people
    return text, []


def plain_text(description: str) -> str:
    """Google Calendar keeps descriptions written on the web as HTML; the agenda wants plain lines."""
    if "<" not in description and "&" not in description:
        return description
    return html.unescape(_TAG.sub("", _BREAK.sub("\n", description)))


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)


def _line(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()
