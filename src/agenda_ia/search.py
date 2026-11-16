"""Hybrid search: meaning (vectors) + shared words (BM25), merged with Reciprocal Rank Fusion."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from . import store
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
    candidates: int = 30,
) -> list[Hit]:
    start_ts = int(start.timestamp()) if start else None
    end_ts = int(end.timestamp()) if end else None
    by_meaning = store.vector_search(conn, embedder.query(question), candidates, start_ts, end_ts)
    by_word = store.keyword_search(conn, fts_query(question), candidates, start_ts, end_ts)
    fused = reciprocal_rank_fusion({"significado": by_meaning, "palavra": by_word})[:k]
    events = store.get_events(conn, [item for item, _, _ in fused])
    return [Hit(events[item], score, found_by) for item, score, found_by in fused if item in events]
