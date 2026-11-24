"""Turns a question into search terms, shared by the SQLite (FTS5) and Postgres (tsquery) keyword searches."""

from __future__ import annotations

import re

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


def query_terms(question: str) -> list[str]:
    """Word prefixes worth searching for, in order, without repeats.

    "reuniões" -> "reuni", which also matches "reunião": crude but cheap stemming for Portuguese.
    Only letters and digits survive, so the terms are safe to put inside any query syntax.
    """
    terms: list[str] = []
    for word in re.findall(r"\w+", question.lower()):
        if word in STOPWORDS or len(word) < 3 or word.isdigit():
            continue
        stem = word[: max(4, len(word) - 3)]
        if stem not in terms:
            terms.append(stem)
    return terms
