from datetime import date, datetime, timedelta

import pytest
from conftest import FakeModel
from fastapi.testclient import TestClient

from agenda_ia.api import AppState, RateLimiter, Workspace, create_app
from agenda_ia.assistant import Assistant
from agenda_ia.auth import AuthSettings
from agenda_ia.config import TZ
from agenda_ia.llm import LLMError, UnavailableModel

HEADERS = {"X-Requested-With": "agenda-ia"}


def _client(sample_db, fake_embedder, model, limit=20):
    def load():
        demo = Workspace(Assistant(sample_db, fake_embedder, model), "exemplo", date(2026, 11, 1), date(2027, 1, 31))
        llm = None if isinstance(model, UnavailableModel) else "fake"
        return AppState(demo, llm, new_private=lambda user: demo)

    auth = AuthSettings(client_id=None, allowed=frozenset(), secret="teste")
    return TestClient(create_app(load, RateLimiter(limit, 600), auth), base_url="https://testserver")


def test_status_and_agenda(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, FakeModel()) as client:
        status = client.get("/api/demo/status").json()
        assert status["origem"] == "exemplo" and status["llm"] and status["eventos"] > 50
        agenda = client.get("/api/demo/agenda").json()
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
        body = client.post("/api/demo/perguntar", json={"pergunta": "quando é o dentista?"}, headers=HEADERS).json()
    assert body["resposta"] == "Terça, 17/11, às 8h." and body["encontrou"]
    assert [f["titulo"] for f in body["fontes"]] == ["Dentista"]
    assert body["detalhes"]["estrategia"] == "busca híbrida" and body["detalhes"]["chamadas"] == 2


@pytest.mark.parametrize("question", ["", "x" * 301])
def test_question_size_is_validated(sample_db, fake_embedder, question):
    with _client(sample_db, fake_embedder, FakeModel()) as client:
        assert client.post("/api/demo/perguntar", json={"pergunta": question}, headers=HEADERS).status_code == 422


def test_post_without_the_custom_header_is_refused(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, FakeModel()) as client:
        assert client.post("/api/demo/perguntar", json={"pergunta": "dentista?"}).status_code == 403


def test_rate_limit(sample_db, fake_embedder):
    replies = [{"tipo": "buscar", "de": None, "ate": None, "busca": "dentista"},
               {"resposta": "8h.", "citacoes": [1], "encontrou": True}] * 2
    with _client(sample_db, fake_embedder, FakeModel(*replies), limit=2) as client:
        codes = [client.post("/api/demo/perguntar", json={"pergunta": "dentista?"}, headers=HEADERS).status_code
                 for _ in range(3)]
    assert codes == [200, 200, 429]


def test_without_llm_the_app_still_answers_with_search(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, UnavailableModel("sem chave")) as client:
        assert client.get("/api/demo/status").json()["llm"] is False
        body = client.post("/api/demo/perguntar", json={"pergunta": "dentista"}, headers=HEADERS).json()
    assert not body["encontrou"] and "Dentista" in body["resposta"]
    assert body["detalhes"]["reserva"].startswith("LLMError")


SOON = (datetime.now(TZ).date() + timedelta(days=2)).isoformat()  # the form only takes dates near today
DENTIST = {"titulo": "Dentista", "data": SOON, "dia_inteiro": False, "inicio": "15:00", "fim": "16:00",
           "local": "Clínica Sorriso", "participantes": ["Ana"], "notas": None}


def test_sentence_fills_the_form_and_saves_nothing(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, FakeModel(DENTIST)) as client:
        before = client.get("/api/demo/status").json()["eventos"]
        response = client.post("/api/demo/interpretar", json={"frase": "dentista quinta 15h com a Ana"}, headers=HEADERS)
        after = client.get("/api/demo/status").json()["eventos"]
    assert response.status_code == 200
    body = response.json()
    assert body["titulo"] == "Dentista" and body["data"] == SOON
    assert body["inicio"] == "15:00" and body["fim"] == "16:00"  # HH:MM, ready for <input type="time">
    assert body["participantes"] == ["Ana"] and body["notas"] == "" and body["faltando"] == []
    assert body["tokens"] == 110
    assert after == before
    assert "agenda_demo" not in response.cookies  # no private copy of the demo is created just to read a sentence


def test_sentence_reports_missing_fields(sample_db, fake_embedder):
    reply = {"titulo": "Cortar o cabelo", "data": None, "dia_inteiro": False, "inicio": None}
    with _client(sample_db, fake_embedder, FakeModel(reply)) as client:
        body = client.post("/api/demo/interpretar", json={"frase": "cortar o cabelo"}, headers=HEADERS).json()
    assert body["faltando"] == ["data", "inicio"] and body["data"] is None and body["inicio"] is None


@pytest.mark.parametrize(
    "model, status",
    [
        (FakeModel({"titulo": None}), 422),  # no appointment in the sentence
        (FakeModel(LLMError("fora do ar")), 502),
        (UnavailableModel("sem chave"), 503),
    ],
)
def test_sentence_errors(sample_db, fake_embedder, model, status):
    with _client(sample_db, fake_embedder, model) as client:
        response = client.post("/api/demo/interpretar", json={"frase": "qual a capital da França?"}, headers=HEADERS)
    assert response.status_code == status and isinstance(response.json()["detail"], str)


def test_sentence_needs_the_header_and_a_sane_size(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, FakeModel()) as client:
        assert client.post("/api/demo/interpretar", json={"frase": "dentista"}).status_code == 403
        for frase in ["", "x" * 301]:
            assert client.post("/api/demo/interpretar", json={"frase": frase}, headers=HEADERS).status_code == 422


def test_sentences_and_questions_share_the_model_quota(sample_db, fake_embedder):
    replies = [{"tipo": "buscar", "de": None, "ate": None, "busca": "dentista"},
               {"resposta": "8h.", "citacoes": [1], "encontrou": True}, DENTIST]
    with _client(sample_db, fake_embedder, FakeModel(*replies), limit=2) as client:
        codes = [
            client.post("/api/demo/perguntar", json={"pergunta": "dentista?"}, headers=HEADERS).status_code,
            client.post("/api/demo/interpretar", json={"frase": "dentista quinta 15h"}, headers=HEADERS).status_code,
            client.post("/api/demo/interpretar", json={"frase": "dentista quinta 15h"}, headers=HEADERS).status_code,
        ]
    assert codes == [200, 200, 429]
