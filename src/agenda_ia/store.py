"""Event storage.

`EventStore` is what the search and the API need. Two implementations:
- SqliteEventStore (this module): one SQLite database with FTS5 (words) and sqlite-vec (vectors).
  Used for the demo agenda, the tests and local runs. Lives in memory or in a file.
- PostgresEventStore (pgstore.py): Postgres with pgvector, for private agendas that must survive
  restarts (Neon in production). Every query is scoped to one owner.

Events have an origin: "base" (indexed from an .ics at startup, read-only), "cadastro" (created
by the person) or "importado" (imported from their .ics). Only the last two can be deleted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np
import sqlite_vec

from .calendar_loader import Event
from .config import TZ
from .text import query_terms

BASE, CREATED, IMPORTED = "base", "cadastro", "importado"


@dataclass(frozen=True)
class StoredEvent:
    id: int
    event: Event
    origin: str = BASE


class EventStore(Protocol):
    def vector_search(self, query_vector: np.ndarray, k: int, start_ts: int | None, end_ts: int | None) -> list[int]: ...

    def keyword_search(self, question: str, k: int, start_ts: int | None, end_ts: int | None) -> list[int]: ...

    def get_events(self, ids: Sequence[int]) -> dict[int, StoredEvent]: ...

    def events_between(self, start_ts: int | None, end_ts: int | None, limit: int = 50) -> list[StoredEvent]: ...

    def count(self) -> int: ...

    def add(self, events: Sequence[Event], vectors: np.ndarray, origin: str) -> list[StoredEvent]:
        """Adds events; one already present (same uid and start) is skipped, not duplicated."""
        ...

    def delete(self, event_id: int) -> bool:
        """Deletes an event created or imported by the person. Base events are never deleted."""
        ...


# ---------- SQLite ----------

def connect(path: str | Path, check_same_thread: bool = True) -> sqlite3.Connection:
    """check_same_thread=False lets a web server's worker threads share it (callers must serialize writes)."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    return conn


def rebuild(conn: sqlite3.Connection, events: Sequence[Event], vectors: np.ndarray, dim: int) -> None:
    """Recreates every table from scratch. The agenda is small, so a full rebuild is simpler than syncing."""
    if len(events) != len(vectors):
        raise ValueError("cada evento precisa de um vetor")
    with conn:
        conn.executescript(
            """
            drop table if exists events;
            drop table if exists events_fts;
            drop table if exists events_vec;
            drop table if exists meta;
            create table meta (name text primary key, value text not null);
            create table events (
                id integer primary key,
                key text unique not null,
                uid text not null,
                title text not null,
                start_ts integer not null,
                end_ts integer not null,
                all_day integer not null,
                location text not null,
                attendees text not null,
                description text not null,
                origin text not null default 'base'
            );
            create index events_start on events(start_ts);
            -- remove_diacritics: "reuniao" finds "reunião"
            create virtual table events_fts using fts5(
                title, location, attendees, description,
                tokenize = 'unicode61 remove_diacritics 2'
            );
            """
        )
        # start_ts is a metadata column, so the vector search can filter by date itself.
        conn.execute(f"create virtual table events_vec using vec0(embedding float[{int(dim)}], start_ts integer)")
        conn.execute("insert into meta values ('dim', ?)", (str(dim),))
        for event, vector in zip(events, vectors):
            _insert(conn, event, vector, BASE)


def _insert(conn: sqlite3.Connection, event: Event, vector: np.ndarray, origin: str) -> int | None:
    cur = conn.execute(
        """insert or ignore into events
           (key, uid, title, start_ts, end_ts, all_day, location, attendees, description, origin)
           values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event.key, event.uid, event.title,
            int(event.start.timestamp()), int(event.end.timestamp()), int(event.all_day),
            event.location, "; ".join(event.attendees), event.description, origin,
        ),
    )
    if cur.rowcount == 0:  # same uid and start already stored
        return None
    row_id = cur.lastrowid
    conn.execute(
        "insert into events_fts (rowid, title, location, attendees, description) values (?, ?, ?, ?, ?)",
        (row_id, event.title, event.location, ", ".join(event.attendees), event.description),
    )
    conn.execute(
        "insert into events_vec (rowid, embedding, start_ts) values (?, ?, ?)",
        (row_id, np.asarray(vector, dtype=np.float32).tobytes(), int(event.start.timestamp())),
    )
    return row_id


def fts_query(question: str) -> str:
    """A safe FTS5 query: each term as a quoted prefix, joined with OR."""
    return " OR ".join(f'"{term}"*' for term in query_terms(question))


def vector_search(
    conn: sqlite3.Connection, query_vector: np.ndarray, k: int, start_ts: int | None, end_ts: int | None
) -> list[int]:
    """Nearest events by meaning, already restricted to the date window."""
    sql = "select rowid from events_vec where embedding match ? and k = ?"
    params: list = [np.asarray(query_vector, dtype=np.float32).tobytes(), k]
    if start_ts is not None:
        sql += " and start_ts >= ?"
        params.append(start_ts)
    if end_ts is not None:
        sql += " and start_ts < ?"
        params.append(end_ts)
    sql += " order by distance"
    return [row[0] for row in conn.execute(sql, params)]


def keyword_search(
    conn: sqlite3.Connection, match: str, k: int, start_ts: int | None, end_ts: int | None
) -> list[int]:
    """Events that share words with the question, ranked by BM25 (lower is better)."""
    if not match:
        return []
    sql = """select e.id from events_fts f join events e on e.id = f.rowid
             where events_fts match ?"""
    params: list = [match]
    if start_ts is not None:
        sql += " and e.start_ts >= ?"
        params.append(start_ts)
    if end_ts is not None:
        sql += " and e.start_ts < ?"
        params.append(end_ts)
    sql += " order by bm25(events_fts) limit ?"
    params.append(k)
    return [row[0] for row in conn.execute(sql, params)]


def events_between(conn: sqlite3.Connection, start_ts: int | None, end_ts: int | None, limit: int = 50) -> list[StoredEvent]:
    """Plain listing in time order, for questions like "what do I have tomorrow?"."""
    sql = "select * from events where 1 = 1"
    params: list = []
    if start_ts is not None:
        sql += " and start_ts >= ?"
        params.append(start_ts)
    if end_ts is not None:
        sql += " and start_ts < ?"
        params.append(end_ts)
    sql += " order by start_ts limit ?"
    params.append(limit)
    return [_row_to_stored(row) for row in conn.execute(sql, params)]


def get_events(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, StoredEvent]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"select * from events where id in ({marks})", list(ids))
    return {row["id"]: _row_to_stored(row) for row in rows}


def count_events(conn: sqlite3.Connection) -> int:
    return conn.execute("select count(*) from events").fetchone()[0]


def _row_to_stored(row: sqlite3.Row) -> StoredEvent:
    event = Event(
        uid=row["uid"],
        title=row["title"],
        start=datetime.fromtimestamp(row["start_ts"], TZ),
        end=datetime.fromtimestamp(row["end_ts"], TZ),
        all_day=bool(row["all_day"]),
        location=row["location"],
        attendees=tuple(a for a in row["attendees"].split("; ") if a),
        description=row["description"],
    )
    return StoredEvent(id=row["id"], event=event, origin=row["origin"])


class SqliteEventStore:
    """EventStore over one SQLite connection (callers serialize access when threads share it)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    @classmethod
    def in_memory(cls, events: Sequence[Event], vectors: np.ndarray, dim: int) -> "SqliteEventStore":
        conn = connect(":memory:", check_same_thread=False)
        rebuild(conn, events, vectors, dim)
        return cls(conn)

    def clone(self) -> "SqliteEventStore":
        """A private copy of the whole database (events, word index and vectors), in memory."""
        copy = connect(":memory:", check_same_thread=False)
        self.conn.backup(copy)
        return SqliteEventStore(copy)

    def vector_search(self, query_vector, k, start_ts, end_ts):
        return vector_search(self.conn, query_vector, k, start_ts, end_ts)

    def keyword_search(self, question, k, start_ts, end_ts):
        return keyword_search(self.conn, fts_query(question), k, start_ts, end_ts)

    def get_events(self, ids):
        return get_events(self.conn, ids)

    def events_between(self, start_ts, end_ts, limit=50):
        return events_between(self.conn, start_ts, end_ts, limit)

    def count(self):
        return count_events(self.conn)

    def add(self, events, vectors, origin):
        if len(events) != len(vectors):
            raise ValueError("cada evento precisa de um vetor")
        with self.conn:
            ids = [_insert(self.conn, event, vector, origin) for event, vector in zip(events, vectors)]
        return list(self.get_events([i for i in ids if i is not None]).values())

    def delete(self, event_id):
        with self.conn:
            cur = self.conn.execute("delete from events where id = ? and origin != ?", (event_id, BASE))
            if cur.rowcount == 0:
                return False
            self.conn.execute("delete from events_fts where rowid = ?", (event_id,))
            self.conn.execute("delete from events_vec where rowid = ?", (event_id,))
        return True
