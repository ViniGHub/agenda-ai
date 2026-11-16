from datetime import date, datetime

from conftest import FakeModel

from agenda_ia.config import TZ
from agenda_ia.llm import LLMError
from agenda_ia.understanding import build_system_prompt, understand

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=TZ)  # Monday


def test_prompt_carries_exact_dates():
    prompt = build_system_prompt(NOW)
    assert "seg 16/11/2026 = 2026-11-16 (hoje)" in prompt
    assert "ter 17/11/2026 = 2026-11-17 (amanhã)" in prompt
    assert "Esta semana: 2026-11-16 a 2026-11-22" in prompt
    assert "Semana que vem: 2026-11-23 a 2026-11-29" in prompt
    assert "Mês que vem: 2026-12-01 a 2026-12-31" in prompt


def test_valid_reply_becomes_a_query():
    model = FakeModel({"tipo": "listar", "de": "2026-11-17", "ate": "2026-11-17", "busca": None})
    query, note = understand(model, "o que tenho amanhã?", NOW)
    assert note is None
    assert (query.tipo, query.de, query.ate, query.busca) == ("listar", date(2026, 11, 17), date(2026, 11, 17), None)
    start, end = query.window()
    assert start == datetime(2026, 11, 17, tzinfo=TZ) and end == datetime(2026, 11, 18, tzinfo=TZ)
    assert model.sent[0][1] == "Pergunta: o que tenho amanhã?"


def test_reversed_range_falls_back_to_plain_search():
    model = FakeModel({"tipo": "listar", "de": "2026-11-20", "ate": "2026-11-17", "busca": None})
    query, note = understand(model, "o que tenho?", NOW)
    assert query.tipo == "buscar" and query.busca == "o que tenho?" and query.de is None
    assert "ValidationError" in note


def test_search_without_terms_is_rejected():
    query, note = understand(FakeModel({"tipo": "buscar", "de": None, "ate": None, "busca": "  "}), "dentista", NOW)
    assert query.busca == "dentista" and note


def test_model_failure_falls_back():
    query, note = understand(FakeModel(LLMError("429")), "quando é o dentista?", NOW)
    assert query.tipo == "buscar" and query.busca == "quando é o dentista?"
    assert note.startswith("LLMError")
