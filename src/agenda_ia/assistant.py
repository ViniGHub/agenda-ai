"""The whole question flow: understand -> retrieve -> answer."""

from __future__ import annotations

import sqlite3
import time as clock
from dataclasses import dataclass
from datetime import datetime, time, timedelta

from . import store
from .answering import Answer, compose
from .config import TZ
from .embeddings import Embedder
from .llm import JSONModel, Usage
from .search import hybrid_search
from .store import StoredEvent
from .understanding import Query, understand


@dataclass
class Result:
    question: str
    query: Query
    query_note: str | None
    strategy: str
    retrieved: list[StoredEvent]
    answer: Answer
    usage: Usage
    seconds: float


class Assistant:
    def __init__(self, conn: sqlite3.Connection, embedder: Embedder, model: JSONModel,
                 k: int = 5, wide_k: int = 20, list_limit: int = 40):
        self.conn, self.embedder, self.model = conn, embedder, model
        self.k, self.wide_k, self.list_limit = k, wide_k, list_limit

    def ask(self, question: str, now: datetime | None = None) -> Result:
        began = clock.perf_counter()
        now = now or datetime.now(TZ)
        calls_before = (self.model.usage.calls, self.model.usage.prompt_tokens, self.model.usage.completion_tokens)

        query, query_note = understand(self.model, question, now)
        start, end = query.window()
        if query.tipo in ("listar", "contar") and start is None and end is None:
            start = datetime.combine(now.date(), time.min, tzinfo=TZ)  # "what do I have?" means from today on

        if query.tipo == "buscar":
            # One specific event: best matches, each recurring series once, at its next occurrence.
            strategy = "busca híbrida"
            hits = hybrid_search(self.conn, self.embedder, query.busca or question, start, end, k=self.k, now=now)
            events = [hit.stored for hit in hits]
        elif query.busca:
            # Listing or counting a kind of event: every occurrence counts, in time order.
            strategy = "busca híbrida ampla"
            hits = hybrid_search(self.conn, self.embedder, query.busca, start, end, k=self.wide_k, collapse_series=False)
            events = sorted((hit.stored for hit in hits), key=lambda s: s.event.start)
        else:
            strategy = "lista do período"
            events = store.events_between(self.conn, _ts(start), _ts(end), limit=self.list_limit)

        answer = compose(self.model, question, events, now, describe_period(start, end))
        usage = Usage(
            self.model.usage.calls - calls_before[0],
            self.model.usage.prompt_tokens - calls_before[1],
            self.model.usage.completion_tokens - calls_before[2],
        )
        return Result(question, query, query_note, strategy, events, answer, usage, clock.perf_counter() - began)


def describe_period(start: datetime | None, end: datetime | None) -> str:
    if start and end:
        last = (end - timedelta(days=1)).date()
        if last == start.date():
            return f"{start:%d/%m/%Y}"
        return f"de {start:%d/%m/%Y} a {last:%d/%m/%Y}"
    if start:
        return f"a partir de {start:%d/%m/%Y}"
    if end:
        return f"até {(end - timedelta(days=1)):%d/%m/%Y}"
    return "toda a agenda"


def _ts(value: datetime | None) -> int | None:
    return int(value.timestamp()) if value else None
