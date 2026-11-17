"""Postgres + pgvector storage for private agendas (Neon in production).

One table holds everyone's events; every query is scoped to one owner (the Google account id),
so one person can never read or delete another person's events.

    DATABASE_URL=postgresql://usuario:senha@host/banco?sslmode=require

The vector goes in as a pgvector text literal ('[0.1,0.2,...]'::vector), which avoids registering
custom types on each pooled connection. Keyword search uses the 'simple' text configuration
(lowercase, no stemming) so the same prefix terms work here and in SQLite's FTS5.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

import numpy as np

from .calendar_loader import Event
from .config import TZ
from .store import BASE, StoredEvent
from .text import query_terms


def schema(dim: int) -> str:
    return f"""
    create extension if not exists vector;
    create table if not exists eventos (
        id bigserial primary key,
        dono text not null,
        chave text not null,
        uid text not null,
        titulo text not null,
        inicio timestamptz not null,
        fim timestamptz not null,
        dia_inteiro boolean not null,
        local text not null default '',
        participantes text not null default '',
        notas text not null default '',
        origem text not null,
        criado_em timestamptz not null default now(),
        embedding vector({int(dim)}) not null,
        busca tsvector generated always as (
            to_tsvector('simple', titulo || ' ' || local || ' ' || participantes || ' ' || notas)
        ) stored,
        unique (dono, chave)
    );
    create index if not exists eventos_dono_inicio on eventos (dono, inicio);
    create index if not exists eventos_busca on eventos using gin (busca);
    create index if not exists eventos_embedding on eventos using hnsw (embedding vector_cosine_ops);
    """


def _vector(values: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in np.asarray(values, dtype=np.float32)) + "]"


def _when(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, TZ)


COLUMNS = "id, uid, titulo, inicio, fim, dia_inteiro, local, participantes, notas, origem"


class PostgresDatabase:
    """Connection pool plus schema; hands out one store per owner."""

    def __init__(self, url: str, dim: int, max_size: int = 4):
        import psycopg
        from psycopg_pool import ConnectionPool

        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(schema(dim))
        # Neon suspends idle databases: check each connection before use and drop idle ones early.
        # prepare_threshold=None: no server-side prepared statements, which a pooled (PgBouncer)
        # connection string may not keep between transactions.
        self.pool = ConnectionPool(
            url, min_size=0, max_size=max_size, max_idle=60,
            kwargs={"autocommit": True, "prepare_threshold": None},
            check=ConnectionPool.check_connection, open=True,
        )

    def for_owner(self, owner: str) -> "PostgresEventStore":
        return PostgresEventStore(self, owner)

    def close(self) -> None:
        self.pool.close()


class PostgresEventStore:
    """EventStore for one owner's events."""

    def __init__(self, db: PostgresDatabase, owner: str):
        if not owner:
            raise ValueError("dono vazio")
        self.db, self.owner = db, owner

    def _rows(self, sql: str, params: Sequence) -> list[tuple]:
        with self.db.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def _window(self, start_ts: int | None, end_ts: int | None) -> tuple[str, list]:
        sql, params = "", []
        if start_ts is not None:
            sql += " and inicio >= %s"
            params.append(_when(start_ts))
        if end_ts is not None:
            sql += " and inicio < %s"
            params.append(_when(end_ts))
        return sql, params

    def vector_search(self, query_vector, k, start_ts, end_ts):
        window, params = self._window(start_ts, end_ts)
        rows = self._rows(
            f"select id from eventos where dono = %s{window} order by embedding <=> %s::vector limit %s",
            [self.owner, *params, _vector(query_vector), k],
        )
        return [row[0] for row in rows]

    def keyword_search(self, question, k, start_ts, end_ts):
        terms = query_terms(question)
        if not terms:
            return []
        tsquery = " | ".join(f"{term}:*" for term in terms)  # terms are letters and digits only
        window, params = self._window(start_ts, end_ts)
        rows = self._rows(
            f"""select id from eventos, to_tsquery('simple', %s) q
                where dono = %s and busca @@ q{window}
                order by ts_rank_cd(busca, q) desc limit %s""",
            [tsquery, self.owner, *params, k],
        )
        return [row[0] for row in rows]

    def get_events(self, ids):
        if not ids:
            return {}
        rows = self._rows(f"select {COLUMNS} from eventos where dono = %s and id = any(%s)", [self.owner, list(ids)])
        return {row[0]: _to_stored(row) for row in rows}

    def events_between(self, start_ts, end_ts, limit=50):
        window, params = self._window(start_ts, end_ts)
        rows = self._rows(
            f"select {COLUMNS} from eventos where dono = %s{window} order by inicio limit %s",
            [self.owner, *params, limit],
        )
        return [_to_stored(row) for row in rows]

    def count(self):
        return self._rows("select count(*) from eventos where dono = %s", [self.owner])[0][0]

    def add(self, events, vectors, origin):
        if len(events) != len(vectors):
            raise ValueError("cada evento precisa de um vetor")
        added = []
        with self.db.pool.connection() as conn, conn.transaction():
            for event, vector in zip(events, vectors):
                row = conn.execute(
                    f"""insert into eventos (dono, chave, uid, titulo, inicio, fim, dia_inteiro, local,
                                            participantes, notas, origem, embedding)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                        on conflict (dono, chave) do nothing
                        returning {COLUMNS}""",
                    [
                        self.owner, event.key, event.uid, event.title, event.start, event.end, event.all_day,
                        event.location, "; ".join(event.attendees), event.description, origin, _vector(vector),
                    ],
                ).fetchone()
                if row:
                    added.append(_to_stored(row))
        return added

    def update(self, event_id, event, vector):
        # dono, chave, uid and origem stay: the event keeps its identity. The tsvector column follows by itself.
        rows = self._rows(
            f"""update eventos set titulo = %s, inicio = %s, fim = %s, dia_inteiro = %s, local = %s,
                                  participantes = %s, notas = %s, embedding = %s::vector
                where dono = %s and id = %s and origem <> %s
                returning {COLUMNS}""",
            [
                event.title, event.start, event.end, event.all_day, event.location, "; ".join(event.attendees),
                event.description, _vector(vector), self.owner, event_id, BASE,
            ],
        )
        return _to_stored(rows[0]) if rows else None

    def delete(self, event_id):
        with self.db.pool.connection() as conn:
            cur = conn.execute(
                "delete from eventos where dono = %s and id = %s and origem <> %s", [self.owner, event_id, BASE]
            )
            return cur.rowcount > 0


def _to_stored(row: tuple) -> StoredEvent:
    id_, uid, title, start, end, all_day, location, attendees, notes, origin = row
    event = Event(
        uid=uid,
        title=title,
        start=start.astimezone(TZ),
        end=end.astimezone(TZ),
        all_day=bool(all_day),
        location=location,
        attendees=tuple(a for a in attendees.split("; ") if a),
        description=notes,
    )
    return StoredEvent(id=int(id_), event=event, origin=origin)
