"""Hybrid search: meaning (vectors) + shared words (BM25 / ts_rank), merged with Reciprocal Rank Fusion.

Works on any EventStore, so the same code searches the SQLite demo and the Postgres private agendas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .config import TZ
from .embeddings import Embedder
from .store import EventStore, StoredEvent, fts_query  # noqa: F401  (fts_query re-exported for callers and tests)

RRF_K = 60  # standard constant from the RRF paper; dampens the weight of the very top ranks


@dataclass(frozen=True)
class Hit:
    stored: StoredEvent
    score: float
    found_by: tuple[str, ...]  # "significado", "palavra" or both


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
    store: EventStore,
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
    by_meaning = store.vector_search(embedder.query(question), candidates, start_ts, end_ts)
    by_word = store.keyword_search(question, candidates, start_ts, end_ts)
    fused = reciprocal_rank_fusion({"significado": by_meaning, "palavra": by_word})

    if not collapse_series:
        fused = fused[:k]
        events = store.get_events([item for item, _, _ in fused])
        return [Hit(events[item], score, found_by) for item, score, found_by in fused if item in events]

    events = store.get_events([item for item, _, _ in fused])
    series: dict[str, list[tuple[int, float, tuple[str, ...]]]] = {}
    for item, score, found_by in fused:  # fused is sorted, so each series keeps its best score first
        if item in events:
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
