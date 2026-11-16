from datetime import date

import pytest
from conftest import FakeModel
from fastapi.testclient import TestClient

from agenda_ia.api import AppState, RateLimiter, create_app
from agenda_ia.assistant import Assistant
from agenda_ia.llm import UnavailableModel


def _client(sample_db, fake_embedder, model, limit=20):
    def load():
        return AppState(Assistant(sample_db, fake_embedder, model), "exemplo", date(2026, 11, 1), date(2027, 1, 31),
                        None if isinstance(model, UnavailableModel) else "fake")

    return TestClient(create_app(load, RateLimiter(limit, 600)))


def test_status_and_agenda(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, FakeModel()) as client:
        status = client.get("/api/status").json()
        assert status["origem"] == "exemplo" and status["llm"] and status["eventos"] > 50
        agenda = client.get("/api/agenda").json()
        assert len(agenda) == status["eventos"]
        dentist = next(e for e in agenda if e["titulo"] == "Dentista")
        assert dentist["quando"] == "terça-feira, 17/11/2026, 08:00–09:00"
        assert dentist["participantes"] == ["Dr. Marcos Lima"]


def test_question_returns_answer_sources_and_details(sample_db, fake_embedder):
    model = FakeModel(
        {"tipo": "buscar", "de": None, "ate": None, "busca": "dentista"},
        {"resposta": "Terça, 17/11, às 8h.", "citacoes": [1], "encontrou": True},
    )
    with _client(sample_db, fake_embedder, model) as client:
        body = client.post("/api/perguntar", json={"pergunta": "quando é o dentista?"}).json()
    assert body["resposta"] == "Terça, 17/11, às 8h." and body["encontrou"]
    assert [f["titulo"] for f in body["fontes"]] == ["Dentista"]
    assert body["detalhes"]["estrategia"] == "busca híbrida" and body["detalhes"]["chamadas"] == 2


@pytest.mark.parametrize("question", ["", "x" * 301])
def test_question_size_is_validated(sample_db, fake_embedder, question):
    with _client(sample_db, fake_embedder, FakeModel()) as client:
        assert client.post("/api/perguntar", json={"pergunta": question}).status_code == 422


def test_rate_limit(sample_db, fake_embedder):
    replies = [{"tipo": "buscar", "de": None, "ate": None, "busca": "dentista"},
               {"resposta": "8h.", "citacoes": [1], "encontrou": True}] * 2
    with _client(sample_db, fake_embedder, FakeModel(*replies), limit=2) as client:
        codes = [client.post("/api/perguntar", json={"pergunta": "dentista?"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_without_llm_the_app_still_answers_with_search(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, UnavailableModel("sem chave")) as client:
        assert client.get("/api/status").json()["llm"] is False
        body = client.post("/api/perguntar", json={"pergunta": "dentista"}).json()
    assert not body["encontrou"] and "Dentista" in body["resposta"]
    assert body["detalhes"]["reserva"].startswith("LLMError")
