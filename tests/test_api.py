from datetime import date, datetime, timedelta

import pytest
from conftest import FakeModel, FakeTranscriber
from fastapi.testclient import TestClient

from agenda_ia.api.server import AppState, RateLimiter, Workspace, create_app
from agenda_ia.chat.assistant import Assistant
from agenda_ia.api.auth import AuthSettings
from agenda_ia.config import TZ
from agenda_ia.ai.llm import LLMError, UnavailableModel
from agenda_ia.ai.speech import UnavailableTranscriber

HEADERS = {"X-Requested-With": "agenda-ia"}


def _client(sample_db, fake_embedder, model, limit=20, transcriber=None):
    def load():
        demo = Workspace(Assistant(sample_db, fake_embedder, model), "exemplo", date(2026, 11, 1), date(2027, 1, 31))
        llm = None if isinstance(model, UnavailableModel) else "fake"
        return AppState(demo, llm, new_private=lambda user: demo, transcriber=transcriber or UnavailableTranscriber())

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
    assert "__session" not in response.cookies  # no private copy of the demo is created just to read a sentence


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


NOTE = {"tipo": "anotar", "de": None, "ate": None, "busca": None}


def _ask(client, message, **extra):
    return client.post("/api/demo/perguntar", json={"pergunta": message, **extra}, headers=HEADERS)


def test_explicit_order_in_the_chat_is_saved_with_the_event(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, FakeModel(NOTE, DENTIST)) as client:
        before = client.get("/api/demo/status").json()["eventos"]
        response = _ask(client, "marca dentista quinta 15h com a Ana na clínica Sorriso")
        body = response.json()
        assert body["acao"]["tipo"] == "anotado" and body["acao"]["evento"]["origem"] == "cadastro"
        assert body["resposta"].startswith("Anotei na agenda: Dentista,") and "Clínica Sorriso" in body["resposta"]
        assert body["detalhes"]["tipo"] == "anotar" and body["detalhes"]["chamadas"] == 2
        assert "__session" in response.cookies  # saved in the visitor's own copy of the demo
        assert client.get("/api/demo/status").json()["eventos"] == before + 1
        undo = client.delete(f"/api/demo/compromissos/{body['acao']['evento']['id']}", headers=HEADERS)
        assert undo.status_code == 200
        assert client.get("/api/demo/status").json()["eventos"] == before


def test_message_that_only_describes_an_appointment_asks_first(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, FakeModel(NOTE, DENTIST)) as client:
        before = client.get("/api/demo/status").json()["eventos"]
        body = _ask(client, "dentista quinta 15h com a Ana na clínica Sorriso").json()
        assert body["acao"]["tipo"] == "confirmar" and body["acao"]["evento"] is None
        assert body["acao"]["rascunho"]["titulo"] == "Dentista" and body["resposta"].startswith("Quer que eu anote")
        assert client.get("/api/demo/status").json()["eventos"] == before


def test_missing_day_or_time_sends_to_the_form(sample_db, fake_embedder):
    reply = {"titulo": "Reunião com a padaria", "data": None, "inicio": None}
    with _client(sample_db, fake_embedder, FakeModel(NOTE, reply)) as client:
        body = _ask(client, "marca reunião com a padaria semana que vem").json()
    assert body["acao"]["tipo"] == "completar" and body["acao"]["rascunho"]["faltando"] == ["data", "inicio"]
    assert body["resposta"] == 'Para anotar "Reunião com a padaria", falta o dia e o horário.'


def test_that_was_a_question_skips_the_note(sample_db, fake_embedder):
    model = FakeModel(NOTE, {"resposta": "Terça às 8h.", "citacoes": [1], "encontrou": True})
    with _client(sample_db, fake_embedder, model) as client:
        body = _ask(client, "dentista quinta 15h", modo="pergunta").json()
    assert body["acao"] is None and body["resposta"] == "Terça às 8h." and body["detalhes"]["tipo"] == "buscar"


def test_questions_carry_no_action(sample_db, fake_embedder):
    model = FakeModel({"tipo": "buscar", "de": None, "ate": None, "busca": "dentista"},
                      {"resposta": "Terça às 8h.", "citacoes": [1], "encontrou": True})
    with _client(sample_db, fake_embedder, model) as client:
        response = _ask(client, "quando é o dentista?")
    assert response.json()["acao"] is None and "__session" not in response.cookies


def test_write_it_down_button_reads_the_message_and_asks_first(sample_db, fake_embedder):
    model = FakeModel(DENTIST)  # one call: no need to ask the model what the message is
    with _client(sample_db, fake_embedder, model) as client:
        before = client.get("/api/demo/status").json()["eventos"]
        body = _ask(client, "marca dentista quinta 15h com a Ana na clínica Sorriso", modo="anotar").json()
        assert body["acao"]["tipo"] == "confirmar"  # even with "marca": the person has not seen the fields yet
        assert body["detalhes"]["chamadas"] == 1
        assert client.get("/api/demo/status").json()["eventos"] == before


AUDIO = b"\x1a\x45\xdf\xa3" + bytes(2000)  # stands in for a short WebM recording


def _audio(client, body=AUDIO, kind="audio/webm;codecs=opus", **headers):
    return client.post("/api/demo/transcrever", content=body, headers={**HEADERS, "Content-Type": kind, **headers})


def test_voice_message_becomes_text(sample_db, fake_embedder):
    transcriber = FakeTranscriber("marca dentista quinta às 15h")
    with _client(sample_db, fake_embedder, FakeModel(), transcriber=transcriber) as client:
        assert client.get("/api/demo/status").json()["audio"] is True
        response = _audio(client)
    assert response.status_code == 200 and response.json() == {"texto": "marca dentista quinta às 15h"}
    assert transcriber.received == [(len(AUDIO), "mensagem.webm")]


@pytest.mark.parametrize("kind, extension", [("audio/mp4", "m4a"), ("audio/ogg;codecs=opus", "ogg"), ("audio/wav", "wav")])
def test_each_browser_format_keeps_its_extension(sample_db, fake_embedder, kind, extension):
    transcriber = FakeTranscriber()
    with _client(sample_db, fake_embedder, FakeModel(), transcriber=transcriber) as client:
        assert _audio(client, kind=kind).status_code == 200
    assert transcriber.received[0][1] == f"mensagem.{extension}"


@pytest.mark.parametrize(
    "body, kind, status",
    [
        (AUDIO, "application/json", 415),
        (AUDIO, "image/png", 415),
        (b"x" * 100, "audio/webm", 422),  # too short to hold speech
        (b"x" * 2_000_001, "audio/webm", 413),
    ],
)
def test_bad_uploads_never_reach_the_transcriber(sample_db, fake_embedder, body, kind, status):
    transcriber = FakeTranscriber()
    with _client(sample_db, fake_embedder, FakeModel(), transcriber=transcriber) as client:
        assert _audio(client, body=body, kind=kind).status_code == status
    assert transcriber.received == []


@pytest.mark.parametrize("text, status", [("", 422), (LLMError("fora do ar"), 502)])
def test_silence_and_failures(sample_db, fake_embedder, text, status):
    with _client(sample_db, fake_embedder, FakeModel(), transcriber=FakeTranscriber(text)) as client:
        response = _audio(client)
    assert response.status_code == status and isinstance(response.json()["detail"], str)


def test_voice_needs_the_header_and_a_configured_transcriber(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, FakeModel()) as client:
        assert client.get("/api/demo/status").json()["audio"] is False
        assert _audio(client).status_code == 503
    with _client(sample_db, fake_embedder, FakeModel(), transcriber=FakeTranscriber()) as client:
        refused = client.post("/api/demo/transcrever", content=AUDIO, headers={"Content-Type": "audio/webm"})
        assert refused.status_code == 403


def test_long_transcripts_fit_the_chat_box(sample_db, fake_embedder):
    with _client(sample_db, fake_embedder, FakeModel(), transcriber=FakeTranscriber("palavra " * 100)) as client:
        assert len(_audio(client).json()["texto"]) == 300
