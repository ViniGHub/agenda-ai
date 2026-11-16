"""The same contract for every EventStore. Postgres runs only when TEST_DATABASE_URL points to a database
with pgvector (for example a Neon branch or a local pgvector container); it uses a throwaway table owner."""

import os
import uuid
from datetime import date, datetime, timedelta

import numpy as np
import pytest

from agenda_ia.calendar_loader import Event
from agenda_ia.config import TZ
from agenda_ia.store import BASE, CREATED, IMPORTED, SqliteEventStore

WHEN = datetime(2026, 11, 20, 15, 0, tzinfo=TZ)


def _event(title: str, start: datetime = WHEN, notes: str = "") -> Event:
    return Event(uid=f"teste-{uuid.uuid4()}", title=title, start=start, end=start + timedelta(hours=1),
                 all_day=False, location="", attendees=("Ana",), description=notes)


@pytest.fixture(params=["sqlite", "postgres"])
def make_store(request, fake_embedder):
    """Returns a function that builds an empty store for an owner."""
    if request.param == "sqlite":
        yield lambda owner="a": SqliteEventStore.in_memory([], np.zeros((0, fake_embedder.dim), np.float32), fake_embedder.dim)
        return
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL não definido")
    from agenda_ia.pgstore import PostgresDatabase

    db = PostgresDatabase(url, fake_embedder.dim)
    prefix = f"teste-{uuid.uuid4()}"
    yield lambda owner="a": db.for_owner(f"{prefix}-{owner}")
    with db.pool.connection() as conn:
        conn.execute("delete from eventos where dono like %s", [prefix + "%"])
    db.close()


def _add(store, embedder, events, origin=CREATED):
    return store.add(events, embedder.passages([e.search_text() for e in events]), origin)


def test_added_event_is_found_by_every_search(make_store, fake_embedder):
    store = make_store()
    [stored] = _add(store, fake_embedder, [_event("Dentista", notes="levar a carteirinha do convênio")])
    assert stored.origin == CREATED and store.count() == 1
    assert store.keyword_search("quando é o dentista?", 5, None, None) == [stored.id]
    assert store.vector_search(fake_embedder.query("dentista"), 5, None, None)[0] == stored.id
    day = int(datetime(2026, 11, 20, tzinfo=TZ).timestamp())
    assert [s.id for s in store.events_between(day, day + 86400)] == [stored.id]
    assert store.get_events([stored.id])[stored.id].event.start == WHEN


def test_date_window_is_respected(make_store, fake_embedder):
    store = make_store()
    _add(store, fake_embedder, [_event("Dentista"), _event("Dentista de novo", WHEN + timedelta(days=10))])
    day = int(datetime(2026, 11, 20, tzinfo=TZ).timestamp())
    assert len(store.keyword_search("dentista", 5, day, day + 86400)) == 1
    assert len(store.vector_search(fake_embedder.query("dentista"), 5, day, day + 86400)) == 1


def test_same_event_twice_is_not_duplicated(make_store, fake_embedder):
    store = make_store()
    event = _event("Reunião de equipe")
    assert len(_add(store, fake_embedder, [event], IMPORTED)) == 1
    assert _add(store, fake_embedder, [event], IMPORTED) == []
    assert store.count() == 1


def test_delete_removes_from_every_index(make_store, fake_embedder):
    store = make_store()
    [stored] = _add(store, fake_embedder, [_event("Veterinário")])
    assert store.delete(stored.id) is True
    assert store.count() == 0
    assert store.keyword_search("veterinário", 5, None, None) == []
    assert store.vector_search(fake_embedder.query("veterinário"), 5, None, None) == []
    assert store.delete(stored.id) is False


def test_base_events_cannot_be_deleted(make_store, fake_embedder):
    store = make_store()
    [stored] = _add(store, fake_embedder, [_event("Daily")], BASE)
    assert store.delete(stored.id) is False and store.count() == 1


def test_owners_are_isolated(make_store, fake_embedder, request):
    if request.node.callspec.params["make_store"] == "sqlite":
        pytest.skip("no SQLite cada dono tem o seu próprio banco")
    mine, theirs = make_store("eu"), make_store("outra")
    [stored] = _add(mine, fake_embedder, [_event("Consulta")])
    assert theirs.count() == 0
    assert theirs.get_events([stored.id]) == {}
    assert theirs.keyword_search("consulta", 5, None, None) == []
    assert theirs.delete(stored.id) is False and mine.count() == 1


def test_sqlite_clone_is_independent(sample_db, fake_embedder):
    copy = sample_db.clone()
    before = sample_db.count()
    [stored] = _add(copy, fake_embedder, [_event("Show")])
    assert copy.count() == before + 1 and sample_db.count() == before
    assert copy.keyword_search("show", 5, None, None) == [stored.id]
    assert sample_db.keyword_search("show", 5, None, None) == []
    assert date(2026, 11, 17) in {s.event.start.date() for s in copy.events_between(None, None, 1000)}
