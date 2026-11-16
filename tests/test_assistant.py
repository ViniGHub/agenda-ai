from datetime import datetime

from conftest import FakeModel

from agenda_ia.assistant import Assistant, describe_period
from agenda_ia.config import TZ

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=TZ)


def test_listing_a_day_uses_the_period_not_the_search(sample_db, fake_embedder):
    model = FakeModel(
        {"tipo": "listar", "de": "2026-11-17", "ate": "2026-11-17", "busca": None},
        {"resposta": "Amanhã: dentista às 8h...", "citacoes": [1, 2, 3, 4], "encontrou": True},
    )
    result = Assistant(sample_db, fake_embedder, model).ask("o que tenho amanhã?", NOW)
    assert result.strategy == "lista do período"
    assert [s.event.title for s in result.retrieved] == ["Dentista", "Daily do time", "Kickoff com a Padaria Pão de Mel", "Aula de inglês"]
    assert "Período consultado: 17/11/2026" in model.sent[1][1]
    assert result.usage.calls == 2


def test_specific_question_collapses_recurring_series(sample_db, fake_embedder):
    model = FakeModel(
        {"tipo": "buscar", "de": "2026-11-16", "ate": None, "busca": "daily do time"},
        {"resposta": "Amanhã às 9h30.", "citacoes": [1], "encontrou": True},
    )
    result = Assistant(sample_db, fake_embedder, model).ask("quando é a próxima daily?", NOW)
    dailies = [s for s in result.retrieved if s.event.title == "Daily do time"]
    assert len(dailies) == 1
    assert dailies[0].event.start == datetime(2026, 11, 17, 9, 30, tzinfo=TZ)  # today's already happened at noon


def test_counting_a_kind_keeps_every_occurrence(sample_db, fake_embedder):
    model = FakeModel(
        {"tipo": "contar", "de": "2026-11-16", "ate": "2026-11-22", "busca": "aula de inglês"},
        {"resposta": "Duas aulas.", "citacoes": [1, 2], "encontrou": True},
    )
    result = Assistant(sample_db, fake_embedder, model).ask("quantas aulas de inglês tenho esta semana?", NOW)
    assert result.strategy == "busca híbrida ampla"
    assert len([s for s in result.retrieved if s.event.title == "Aula de inglês"]) == 2
    starts = [s.event.start for s in result.retrieved]
    assert starts == sorted(starts)


def test_empty_period_answers_without_second_call(sample_db, fake_embedder):
    model = FakeModel({"tipo": "listar", "de": "2027-06-01", "ate": "2027-06-01", "busca": None})
    result = Assistant(sample_db, fake_embedder, model).ask("o que tenho em 1º de junho?", NOW)
    assert not result.answer.found and result.usage.calls == 1


def test_describe_period():
    assert describe_period(None, None) == "toda a agenda"
    assert describe_period(datetime(2026, 11, 17, tzinfo=TZ), datetime(2026, 11, 18, tzinfo=TZ)) == "17/11/2026"
    assert describe_period(datetime(2026, 11, 16, tzinfo=TZ), None) == "a partir de 16/11/2026"
