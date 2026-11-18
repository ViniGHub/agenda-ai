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
from .store import BASE, GOOGLE, StoredEvent
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
    alter table eventos add column if not exists google_id text;
    alter table eventos add column if not exists google_etag text;
    create index if not exists eventos_dono_inicio on eventos (dono, inicio);
    create index if not exists eventos_google on eventos (dono, google_id) where google_id is not null;
    -- One row per connected Google account. token: the refresh token, encrypted (google_link.TokenCipher).
    create table if not exists contas_google (
        dono text primary key,
        email text not null,
        token text not null,
        escopos text not null,
        resumo_diario boolean not null default true,
        ultimo_resumo date,
        sincronizado_em timestamptz,
        atualizado_em timestamptz not null default now()
    );
    create index if not exists eventos_busca on eventos using gin (busca);
    create index if not exists eventos_embedding on eventos using hnsw (embedding vector_cosine_ops);
    """


def _vector(values: np.ndarray) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in np.asarray(values, dtype=np.float32)) + "]"


def _when(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, TZ)


COLUMNS = "id, uid, titulo, inicio, fim, dia_inteiro, local, participantes, notas, origem, google_id"


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

    def google_links(self, start_ts, end_ts):
        rows = self._rows(
            """select id, google_id, google_etag from eventos
               where dono = %s and google_id is not null and inicio >= %s and inicio < %s""",
            [self.owner, _when(start_ts), _when(end_ts)],
        )
        return {google_id: (int(id_), etag or "") for id_, google_id, etag in rows}

    def set_link(self, event_id, google_id, etag):
        with self.db.pool.connection() as conn:
            conn.execute("update eventos set google_id = %s, google_etag = %s where dono = %s and id = %s and origem <> %s",
                         [google_id, etag, self.owner, event_id, BASE])

    def id_for_key(self, key):
        rows = self._rows("select id from eventos where dono = %s and chave = %s", [self.owner, key])
        return int(rows[0][0]) if rows else None

    def forget_google(self):
        with self.db.pool.connection() as conn, conn.transaction():
            removed = conn.execute("delete from eventos where dono = %s and origem = %s", [self.owner, GOOGLE]).rowcount
            conn.execute("update eventos set google_id = null, google_etag = null where dono = %s", [self.owner])
        return removed


class PostgresConnections:
    """ConnectionStore in the contas_google table, refresh tokens encrypted."""

    _SELECT = "select dono, email, token, escopos, resumo_diario, ultimo_resumo, sincronizado_em from contas_google"

    def __init__(self, db: PostgresDatabase, cipher):
        self.db, self.cipher = db, cipher

    def _read(self, row: tuple):
        from .google_link import Connection

        owner, email, token, scopes, daily, last_daily, synced_at = row
        try:
            refresh_token = self.cipher.decrypt(token)
        except ValueError:
            return None  # encrypted with another key (the session secret changed): connect again
        return Connection(owner, email, refresh_token, frozenset(scopes.split()), daily, last_daily,
                          synced_at.astimezone(TZ) if synced_at else None)

    def get(self, owner):
        with self.db.pool.connection() as conn:
            row = conn.execute(f"{self._SELECT} where dono = %s", [owner]).fetchone()
        return self._read(row) if row else None

    def save(self, connection):
        with self.db.pool.connection() as conn:
            conn.execute(
                """insert into contas_google (dono, email, token, escopos, resumo_diario, ultimo_resumo, sincronizado_em)
                   values (%s, %s, %s, %s, %s, %s, %s)
                   on conflict (dono) do update set email = excluded.email, token = excluded.token,
                       escopos = excluded.escopos, resumo_diario = excluded.resumo_diario,
                       ultimo_resumo = excluded.ultimo_resumo, sincronizado_em = excluded.sincronizado_em,
                       atualizado_em = now()""",
                [connection.owner, connection.email, self.cipher.encrypt(connection.refresh_token),
                 " ".join(sorted(connection.scopes)), connection.daily, connection.last_daily, connection.synced_at],
            )

    def delete(self, owner):
        with self.db.pool.connection() as conn:
            conn.execute("delete from contas_google where dono = %s", [owner])

    def daily(self):
        with self.db.pool.connection() as conn:
            rows = conn.execute(f"{self._SELECT} where resumo_diario").fetchall()
        return [c for c in (self._read(row) for row in rows) if c is not None]


def _to_stored(row: tuple) -> StoredEvent:
    id_, uid, title, start, end, all_day, location, attendees, notes, origin, google_id = row
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
    return StoredEvent(id=int(id_), event=event, origin=origin, google_id=google_id)
