"""Hybrid search: meaning (vectors) + shared words (BM25), merged with Reciprocal Rank Fusion."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from . import store
from .config import TZ
from .embeddings import Embedder
from .store import StoredEvent

# Words that say nothing about which event the person wants.
STOPWORDS = {
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "no", "na",
    "nos", "nas", "por", "pra", "para", "com", "sem", "sobre", "e", "ou", "que", "qual", "quais", "quando",
    "onde", "quem", "como", "quanto", "quantos", "quantas", "eu", "me", "meu", "minha", "meus", "minhas",
    "tenho", "tem", "tive", "teve", "vou", "vai", "foi", "era", "sera", "será", "é", "esta", "está", "estou",
    "essa", "esse", "este", "isso", "isto", "aquele", "aquela", "hoje", "amanha", "amanhã", "ontem", "dia",
    "dias", "semana", "mes", "mês", "proxima", "próxima", "proximo", "próximo", "agenda", "compromisso",
    "compromissos", "marcado", "marcada", "algum", "alguma", "ja", "já", "ainda", "mais", "ao", "aos", "à", "às",
}

RRF_K = 60  # standard constant from the RRF paper; dampens the weight of the very top ranks


@dataclass(frozen=True)
class Hit:
    stored: StoredEvent
    score: float
    found_by: tuple[str, ...]  # "significado", "palavra" or both


def fts_query(question: str) -> str:
    """Turns free text into a safe FTS5 query: light stemming by prefix, terms joined with OR."""
    terms = []
    for word in re.findall(r"\w+", question.lower()):
        if word in STOPWORDS or len(word) < 3 or word.isdigit():
            continue
        # "reuniões" -> "reuni*" also matches "reunião"; crude but cheap stemming for Portuguese.
        stem = word[: max(4, len(word) - 3)]
        term = f'"{stem}"*'
        if term not in terms:
            terms.append(term)
    return " OR ".join(terms)


def reciprocal_rank_fusion(rankings: dict[str, list[int]], k: int = RRF_K) -> list[tuple[int, float, tuple[str, ...]]]:
    """Each list votes 1/(k + position). Items found by both searches rise to the top."""
    scores: dict[int, float] = {}
    sources: dict[int, list[str]] = {}
    for name, ids in rankings.items():
        for position, item in enumerate(ids, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
            sources.setdefault(item, []).append(name)
    ordered = sorted(scores, key=lambda item: scores[item], reverse=True)
    return [(item, scores[item], tuple(sources[item])) for item in ordered]


def hybrid_search(
    conn: sqlite3.Connection,
    embedder: Embedder,
    question: str,
    start: datetime | None = None,
    end: datetime | None = None,
    k: int = 5,
    candidates: int = 200,
    collapse_series: bool = True,
    now: datetime | None = None,
) -> list[Hit]:
    """Top k events. With collapse_series, a recurring event counts once, shown at its next occurrence.

    Candidates are generous because every occurrence of a recurring event has the same text:
    a daily meeting alone could otherwise fill a small candidate list.
    """
    start_ts = int(start.timestamp()) if start else None
    end_ts = int(end.timestamp()) if end else None
    by_meaning = store.vector_search(conn, embedder.query(question), candidates, start_ts, end_ts)
    by_word = store.keyword_search(conn, fts_query(question), candidates, start_ts, end_ts)
    fused = reciprocal_rank_fusion({"significado": by_meaning, "palavra": by_word})

    if not collapse_series:
        fused = fused[:k]
        events = store.get_events(conn, [item for item, _, _ in fused])
        return [Hit(events[item], score, found_by) for item, score, found_by in fused if item in events]

    events = store.get_events(conn, [item for item, _, _ in fused])
    series: dict[str, list[tuple[int, float, tuple[str, ...]]]] = {}
    for item, score, found_by in fused:  # fused is sorted, so each series keeps its best score first
        series.setdefault(events[item].event.uid, []).append((item, score, found_by))

    now = now or datetime.now(TZ)
    hits = []
    for members in list(series.values())[:k]:
        sources = tuple(dict.fromkeys(name for _, _, found_by in members for name in found_by))
        hits.append(Hit(_closest_occurrence([events[m[0]] for m in members], now), members[0][1], sources))
    return hits


def _closest_occurrence(occurrences: list[StoredEvent], now: datetime) -> StoredEvent:
    """The next occurrence that has not ended; if all are past, the most recent one."""
    upcoming = [o for o in occurrences if o.event.end > now]
    if upcoming:
        return min(upcoming, key=lambda o: o.event.start)
    return max(occurrences, key=lambda o: o.event.start)
