from datetime import date, datetime, time, timedelta

import numpy as np
import pytest
from conftest import FakeModel
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agenda_ia.api.server import AppState, RateLimiter, Workspace, create_app
from agenda_ia.chat.assistant import Assistant
from agenda_ia.api.auth import AuthSettings
from agenda_ia.config import TZ
from agenda_ia.events.editing import EventDraft, ImportError_, import_events
from agenda_ia.events.sample import build
from agenda_ia.storage.store import SqliteEventStore

HEADERS = {"X-Requested-With": "agenda-ia"}
SOON = (datetime.now(TZ) + timedelta(days=3)).date()


def _draft(**changes) -> dict:
    body = {"titulo": "Dentista", "data": SOON.isoformat(), "inicio": "15:00", "local": "Clínica", "notas": "levar exame"}
    body.update(changes)
    return body


# ---------- validation ----------

def test_draft_builds_a_sao_paulo_event():
    event = EventDraft(**_draft(fim="16:30", participantes=["  Ana ", ""])).to_event()
    assert event.start == datetime.combine(SOON, time(15), tzinfo=TZ)
    assert event.end == datetime.combine(SOON, time(16, 30), tzinfo=TZ)
    assert event.attendees == ("Ana",) and event.uid.startswith("cadastro-")


def test_draft_defaults_to_one_hour_and_supports_all_day():
    assert EventDraft(**_draft()).to_event().end == datetime.combine(SOON, time(16), tzinfo=TZ)
    all_day = EventDraft(**_draft(dia_inteiro=True, inicio=None)).to_event()
    assert all_day.all_day and all_day.end - all_day.start == timedelta(days=1)


@pytest.mark.parametrize(
    "changes",
    [{"titulo": "   "}, {"inicio": None}, {"fim": "14:00"}, {"data": "2035-01-01"}, {"notas": "x" * 1001},
     {"participantes": ["x" * 61]}],
)
def test_draft_rejects_bad_input(changes):
    with pytest.raises(ValidationError):
        EventDraft(**_draft(**changes))


def test_import_reads_ics_and_rejects_other_files():
    content = build(date(2026, 11, 16)).to_ical().decode()
    events = import_events(content, date(2026, 11, 16), date(2026, 11, 22))
    assert {"Dentista", "Daily do time"} <= {e.title for e in events}
    with pytest.raises(ImportError_):
        import_events("nome,data\nDentista,2026-11-17", date(2026, 11, 16), date(2026, 11, 22))


# ---------- API ----------

@pytest.fixture
def client(sample_db, fake_embedder):
    def load():
        demo = Workspace(Assistant(sample_db, fake_embedder, FakeModel()), "exemplo", date(2026, 11, 1), date(2027, 1, 31))
        privates = {}

        def new_private(user):
            empty = SqliteEventStore.in_memory([], np.zeros((0, fake_embedder.dim), np.float32), fake_embedder.dim)
            privates[user.email] = Workspace(Assistant(empty, fake_embedder, FakeModel()), "privada",
                                             SOON - timedelta(days=30), SOON + timedelta(days=30))
            return privates[user.email]

        return AppState(demo, "fake", new_private)

    def verifier(token, audience):
        return {"email": "dono@example.com", "email_verified": True, "name": "Dono", "sub": "1"}

    auth = AuthSettings("id", frozenset({"dono@example.com"}), "segredo", verifier)
    with TestClient(create_app(load, RateLimiter(50, 600), auth), base_url="https://testserver") as c:
        yield c


def test_demo_additions_go_to_the_visitors_own_copy(client, sample_db):
    base_count = sample_db.count()
    response = client.post("/api/demo/compromissos", json=_draft(titulo="Show da banda"), headers=HEADERS)
    assert response.status_code == 201 and response.json()["origem"] == "cadastro"
    assert "__session" in response.headers["set-cookie"]
    assert client.get("/api/demo/status").json()["eventos"] == base_count + 1
    assert sample_db.count() == base_count  # the shared demo is untouched

    stranger = TestClient(client.app, base_url="https://testserver")  # another visitor, no cookie
    assert stranger.get("/api/demo/status").json()["eventos"] == base_count
    assert all(e["titulo"] != "Show da banda" for e in stranger.get("/api/demo/agenda").json())


def test_only_added_events_can_be_deleted(client):
    added = client.post("/api/demo/compromissos", json=_draft(), headers=HEADERS).json()
    base_event = next(e for e in client.get("/api/demo/agenda").json() if e["origem"] == "base")
    assert client.delete(f"/api/demo/compromissos/{base_event['id']}", headers=HEADERS).status_code == 404
    assert client.delete(f"/api/demo/compromissos/{added['id']}", headers=HEADERS).status_code == 200
    assert client.delete(f"/api/demo/compromissos/{added['id']}", headers=HEADERS).status_code == 404


def test_added_events_can_be_edited(client):
    added = client.post("/api/demo/compromissos", json=_draft(titulo="Dentista"), headers=HEADERS).json()
    changes = _draft(titulo="Oftalmologista", inicio="16:30", fim="17:00", local="Clínica Visão")
    response = client.put(f"/api/demo/compromissos/{added['id']}", json=changes, headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == added["id"] and body["origem"] == "cadastro"
    assert body["titulo"] == "Oftalmologista" and body["local"] == "Clínica Visão" and "16:30–17:00" in body["quando"]
    agenda = client.get("/api/demo/agenda").json()
    assert [e["titulo"] for e in agenda if e["id"] == added["id"]] == ["Oftalmologista"]


def test_base_events_and_bad_edits_are_refused(client):
    added = client.post("/api/demo/compromissos", json=_draft(), headers=HEADERS).json()
    base_event = next(e for e in client.get("/api/demo/agenda").json() if e["origem"] == "base")
    assert client.put(f"/api/demo/compromissos/{base_event['id']}", json=_draft(), headers=HEADERS).status_code == 404
    assert client.put("/api/demo/compromissos/999999", json=_draft(), headers=HEADERS).status_code == 404
    assert client.put(f"/api/demo/compromissos/{added['id']}", json=_draft(fim="08:00"), headers=HEADERS).status_code == 422
    assert client.put(f"/api/demo/compromissos/{added['id']}", json=_draft()).status_code == 403


def test_shared_demo_cannot_be_edited_by_a_stranger(client):
    added = client.post("/api/demo/compromissos", json=_draft(titulo="Meu"), headers=HEADERS).json()
    stranger = TestClient(client.app, base_url="https://testserver")  # no cookie: sees the shared demo
    assert stranger.put(f"/api/demo/compromissos/{added['id']}", json=_draft(), headers=HEADERS).status_code == 404


def test_writes_need_the_custom_header_and_valid_data(client):
    assert client.post("/api/demo/compromissos", json=_draft()).status_code == 403
    assert client.post("/api/demo/compromissos", json=_draft(inicio=None), headers=HEADERS).status_code == 422


def test_private_add_and_import_need_login(client):
    assert client.post("/api/minha/compromissos", json=_draft(), headers=HEADERS).status_code == 401
    assert client.put("/api/minha/compromissos/1", json=_draft(), headers=HEADERS).status_code == 401
    assert client.post("/api/minha/importar", json={"conteudo": "BEGIN:VCALENDAR"}, headers=HEADERS).status_code == 401


def test_private_import_and_add(client):
    client.post("/api/login", json={"credential": "qualquer-token-valido"}, headers=HEADERS)
    content = build(SOON).to_ical().decode()
    first = client.post("/api/minha/importar", json={"conteudo": content}, headers=HEADERS).json()
    assert first["importados"] == first["encontrados"] > 20
    again = client.post("/api/minha/importar", json={"conteudo": content}, headers=HEADERS).json()
    assert again["importados"] == 0  # importing the same file twice does not duplicate
    assert client.post("/api/minha/compromissos", json=_draft(), headers=HEADERS).status_code == 201
    status = client.get("/api/minha/status").json()
    assert status["eventos"] == first["importados"] + 1 and status["persistente"] is False
    origins = {e["origem"] for e in client.get("/api/minha/agenda").json()}
    assert origins == {"importado", "cadastro"}


def test_import_rejects_a_non_ics_file(client):
    client.post("/api/login", json={"credential": "qualquer-token-valido"}, headers=HEADERS)
    response = client.post("/api/minha/importar", json={"conteudo": "não é um calendário"}, headers=HEADERS)
    assert response.status_code == 422 and "ics" in response.json()["detail"]
