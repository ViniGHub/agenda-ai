"""Keeping the private agenda and Google Calendar in step.

Once connected, Google Calendar is the source of truth:
- a write made here (new, edit, delete) goes to Google first; the local copy keeps the id and the
  etag (version tag) Google answers with. If Google refuses, nothing changes here either.
- sync() reads Google's window [first_day, last_day] and brings the local copy in line: new events
  are added, changed ones (another etag) rewritten, ones gone from Google deleted. Only what changed
  is embedded again, so a sync with nothing new costs one listing and no model time.
Events that existed only here before connecting stay here, unlinked.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

import numpy as np

from ..events.calendar_loader import Event
from ..config import TZ
from .events import event_from_google, google_body
from .link import GoogleApi
from ..storage.store import GOOGLE, EventStore, StoredEvent


@dataclass
class SyncReport:
    added: int = 0
    updated: int = 0
    removed: int = 0


def window(first_day: date, last_day: date) -> tuple[datetime, datetime]:
    return datetime.combine(first_day, time.min, tzinfo=TZ), datetime.combine(last_day + timedelta(days=1), time.min, tzinfo=TZ)


def sync(store: EventStore, embedder, api: GoogleApi, first_day: date, last_day: date) -> SyncReport:
    """Callers hold the workspace lock (the SQLite store is shared between threads)."""
    start, end = window(first_day, last_day)
    items = api.list_events(start, end)
    linked = store.google_links(int(start.timestamp()), int(end.timestamp()))
    new: list[tuple[Event, str, str]] = []
    changed: list[tuple[int, Event, str, str]] = []
    seen: set[str] = set()
    for item in items:
        google_id, event = str(item.get("id", "")), event_from_google(item)
        if not google_id or event is None:
            continue
        seen.add(google_id)
        etag = str(item.get("etag", ""))
        if google_id not in linked:
            new.append((event, google_id, etag))
        elif linked[google_id][1] != etag:
            changed.append((linked[google_id][0], event, google_id, etag))

    report = SyncReport()
    texts = [e.search_text() for e, _, _ in new] + [e.search_text() for _, e, _, _ in changed]
    vectors = embedder.passages(texts) if texts else np.zeros((0, 0), np.float32)
    for (event, google_id, etag), vector in zip(new, vectors[: len(new)]):
        added = store.add([event], vector[None, :], GOOGLE)
        if added:
            store.set_link(added[0].id, google_id, etag)
            report.added += 1
            continue
        # Same uid and start already here: imported earlier from Google's own .ics. Adopt it.
        existing = store.id_for_key(event.key)
        if existing is not None and store.update(existing, event, vector) is not None:
            store.set_link(existing, google_id, etag)
            report.updated += 1
    for (event_id, event, google_id, etag), vector in zip(changed, vectors[len(new):]):
        if store.update(event_id, event, vector) is not None:
            store.set_link(event_id, google_id, etag)
            report.updated += 1
    for google_id, (event_id, _) in linked.items():
        if google_id not in seen and store.delete(event_id):
            report.removed += 1
    return report


def push_new(store: EventStore, embedder, api: GoogleApi, event: Event, origin: str) -> list[StoredEvent]:
    """A new event: into Google Calendar first, then here, linked. Raises GoogleError if Google refuses."""
    item = api.insert_event(google_body(event))
    vector = embedder.passages([event.search_text()])
    added = store.add([event], vector, origin)
    if added:
        store.set_link(added[0].id, str(item.get("id", "")), str(item.get("etag", "")))
        added = [replace(added[0], google_id=str(item.get("id", "")))]
    return added


def push_update(store: EventStore, api: GoogleApi, current: StoredEvent, event: Event, vector) -> StoredEvent | None:
    """An edit of a linked event: Google first, then the local copy with the new etag."""
    assert current.google_id
    item = api.patch_event(current.google_id, google_body(event))
    updated = store.update(current.id, event, vector)
    if updated is not None:
        store.set_link(current.id, current.google_id, str(item.get("etag", "")))
    return updated
