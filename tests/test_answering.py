from datetime import datetime

from conftest import FakeModel

from agenda_ia import store
from agenda_ia.answering import NOT_CONFIRMED, build_user_prompt, compose
from agenda_ia.config import TZ
from agenda_ia.llm import LLMError

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=TZ)


def _tuesday(conn):
    start = int(datetime(2026, 11, 17, tzinfo=TZ).timestamp())
    end = int(datetime(2026, 11, 18, tzinfo=TZ).timestamp())
    return store.events_between(conn, start, end)  # Dentista, Daily, Kickoff, Aula de inglês


def test_prompt_numbers_events_and_fences_them(sample_db):
    prompt = build_user_prompt("o que tenho amanhã?", _tuesday(sample_db), "17/11/2026")
    assert "[1] Título: Dentista" in prompt and "[4] Título: Aula de inglês" in prompt
    assert prompt.index("<compromissos>") < prompt.index("[1]") < prompt.index("</compromissos>")


def test_valid_citations_are_kept(sample_db):
    events = _tuesday(sample_db)
    model = FakeModel({"resposta": "Dentista às 8h.", "citacoes": [1], "encontrou": True})
    answer = compose(model, "quando é o dentista?", events, NOW, "17/11/2026")
    assert answer.found and [s.event.title for s in answer.cited] == ["Dentista"] and answer.note is None


def test_invalid_citations_are_dropped(sample_db):
    events = _tuesday(sample_db)
    model = FakeModel({"resposta": "Dentista e kickoff.", "citacoes": [1, 3, 9, 1], "encontrou": True})
    answer = compose(model, "?", events, NOW, "17/11/2026")
    assert [s.event.title for s in answer.cited] == ["Dentista", "Kickoff com a Padaria Pão de Mel"]
    assert "9" in answer.note


def test_claim_without_valid_citation_is_replaced(sample_db):
    model = FakeModel({"resposta": "Você tem show do Coldplay.", "citacoes": [42], "encontrou": True})
    answer = compose(model, "tenho show?", _tuesday(sample_db), NOW, "17/11/2026")
    assert answer.text == NOT_CONFIRMED and not answer.found and not answer.cited


def test_not_found_is_passed_through(sample_db):
    model = FakeModel({"resposta": "Não encontrei show na agenda.", "citacoes": [], "encontrou": False})
    answer = compose(model, "tenho show?", _tuesday(sample_db), NOW, "17/11/2026")
    assert not answer.found and answer.text == "Não encontrei show na agenda."


def test_no_events_means_no_model_call():
    model = FakeModel()
    answer = compose(model, "o que tenho dia 25/12?", [], NOW, "25/12/2026")
    assert not answer.found and "25/12/2026" in answer.text and model.sent == []


def test_model_failure_still_shows_the_events(sample_db):
    answer = compose(FakeModel(LLMError("timeout")), "amanhã?", _tuesday(sample_db), NOW, "17/11/2026")
    assert "Dentista" in answer.text and len(answer.cited) == 3 and answer.note.startswith("LLMError")


def test_malformed_reply_is_handled(sample_db):
    answer = compose(FakeModel({"answer": "oops"}), "amanhã?", _tuesday(sample_db), NOW, "17/11/2026")
    assert not answer.found and answer.note.startswith("ValidationError")
