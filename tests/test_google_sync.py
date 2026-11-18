from datetime import date, datetime

import numpy as np
import pytest
from conftest import FakeGoogle

from agenda_ia.calendar_loader import Event
from agenda_ia.config import TZ
from agenda_ia.google_link import GoogleError
from agenda_ia.google_sync import push_new, push_update, sync
from agenda_ia.store import CREATED, GOOGLE, IMPORTED, SqliteEventStore

FIRST, LAST = date(2026, 11, 1), date(2027, 1, 31)


def _item(google_id, title, day=19, hour=15, etag='"1"', uid=None):
    start = datetime(2026, 11, day, hour, tzinfo=TZ)
    return {"id": google_id, "iCalUID": uid or f"{google_id}@google.com", "etag": etag, "status": "confirmed",
            "summary": title, "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": start.replace(hour=hour + 1).isoformat()}}


@pytest.fixture
def store(fake_embedder):
    return SqliteEventStore.in_memory([], np.zeros((0, fake_embedder.dim), np.float32), fake_embedder.dim)


def _titles(store):
    return sorted((s.event.title, s.origin, s.google_id) for s in store.events_between(None, None))


def test_first_sync_brings_googles_events_in(store, fake_embedder):
    google = FakeGoogle([_item("g1", "Dentista"), _item("g2", "Academia", day=20)])
    report = sync(store, fake_embedder, google, FIRST, LAST)
    assert (report.added, report.updated, report.removed) == (2, 0, 0)
    assert _titles(store) == [("Academia", GOOGLE, "g2"), ("Dentista", GOOGLE, "g1")]
    assert store.vector_search(fake_embedder.query("dentista"), 1, None, None)  # indexed for the questions


def test_nothing_new_rewrites_nothing(store, fake_embedder):
    google = FakeGoogle([_item("g1", "Dentista")])
    sync(store, fake_embedder, google, FIRST, LAST)

    class CountingEmbedder:
        dim, calls = fake_embedder.dim, 0

        def passages(self, texts):
            CountingEmbedder.calls += len(texts)
            return fake_embedder.passages(texts)

    report = sync(store, CountingEmbedder(), google, FIRST, LAST)
    assert (report.added, report.updated, report.removed) == (0, 0, 0) and CountingEmbedder.calls == 0


def test_changes_and_deletions_in_google_are_followed(store, fake_embedder):
    google = FakeGoogle([_item("g1", "Dentista"), _item("g2", "Academia", day=20)])
    sync(store, fake_embedder, google, FIRST, LAST)
    google.items["g1"] = _item("g1", "Dentista (remarcado)", hour=17, etag='"2"')
    del google.items["g2"]
    report = sync(store, fake_embedder, google, FIRST, LAST)
    assert (report.added, report.updated, report.removed) == (0, 1, 1)
    [only] = store.events_between(None, None)
    assert only.event.title == "Dentista (remarcado)" and only.event.start.hour == 17


def test_an_event_imported_from_googles_ics_is_adopted_not_duplicated(store, fake_embedder):
    start = datetime(2026, 11, 19, 15, tzinfo=TZ)
    imported = Event("abc@google.com", "Dentista", start, start.replace(hour=16), False, "", (), "")
    store.add([imported], fake_embedder.passages([imported.search_text()]), IMPORTED)
    report = sync(store, fake_embedder, FakeGoogle([_item("g1", "Dentista", uid="abc@google.com")]), FIRST, LAST)
    assert (report.added, report.updated) == (0, 1)
    assert _titles(store) == [("Dentista", IMPORTED, "g1")]


def test_events_only_here_are_left_alone(store, fake_embedder):
    start = datetime(2026, 11, 21, 9, tzinfo=TZ)
    local = Event("cadastro-1", "Feira", start, start.replace(hour=10), False, "", (), "")
    store.add([local], fake_embedder.passages([local.search_text()]), CREATED)
    sync(store, fake_embedder, FakeGoogle([_item("g1", "Dentista")]), FIRST, LAST)
    sync(store, fake_embedder, FakeGoogle([]), FIRST, LAST)  # everything gone from Google
    assert _titles(store) == [("Feira", CREATED, None)]


def test_a_write_goes_to_google_first_and_stays_linked(store, fake_embedder):
    google = FakeGoogle()
    start = datetime(2026, 11, 22, 12, tzinfo=TZ)
    event = Event("cadastro-2", "Almoço", start, start.replace(hour=13), False, "Padaria", ("Ana",), "")
    [stored] = push_new(store, fake_embedder, google, event, CREATED)
    assert stored.google_id == "g1" and google.items["g1"]["description"] == "Com: Ana"
    sync(store, fake_embedder, google, FIRST, LAST)  # the same event coming back is not a new one
    assert len(store.events_between(None, None)) == 1

    edited = Event("cadastro-2", "Almoço com a Ana", start, start.replace(hour=14), False, "Padaria", ("Ana",), "")
    push_update(store, google, stored, edited, fake_embedder.passages([edited.search_text()])[0])
    assert google.items["g1"]["summary"] == "Almoço com a Ana"
    assert sync(store, fake_embedder, google, FIRST, LAST).updated == 0  # the etag was kept


def test_google_refusing_a_write_saves_nothing_here(store, fake_embedder):
    google = FakeGoogle()
    google.fail = GoogleError(500, "erro")
    start = datetime(2026, 11, 22, 12, tzinfo=TZ)
    with pytest.raises(GoogleError):
        push_new(store, fake_embedder, google, Event("c", "Almoço", start, start, False, "", (), ""), CREATED)
    assert store.count() == 0


def test_disconnecting_removes_what_came_from_google_and_unlinks_the_rest(store, fake_embedder):
    google = FakeGoogle([_item("g1", "Dentista")])
    sync(store, fake_embedder, google, FIRST, LAST)
    start = datetime(2026, 11, 22, 12, tzinfo=TZ)
    push_new(store, fake_embedder, google, Event("c", "Almoço", start, start.replace(hour=13), False, "", (), ""), CREATED)
    assert store.forget_google() == 1
    assert _titles(store) == [("Almoço", CREATED, None)]
    assert store.google_links(0, 2**31) == {}
