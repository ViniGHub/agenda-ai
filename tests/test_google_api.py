"""The Google Calendar and Gmail routes, against a fake Google (no network)."""

from datetime import date, datetime, timedelta
from email import message_from_bytes, policy
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest
from conftest import FakeGoogle, FakeModel
from fastapi.testclient import TestClient

from agenda_ia.api import TASKS_AUDIENCE, AppState, RateLimiter, Workspace, create_app
from agenda_ia.assistant import Assistant
from agenda_ia.auth import AuthSettings
from agenda_ia.config import TZ
from agenda_ia.google_link import (CALENDAR_SCOPE, GMAIL_SEND_SCOPE, GoogleAccessLost, GoogleError, GoogleService,
                                   MemoryConnections)
from agenda_ia.store import SqliteEventStore

HEADERS = {"X-Requested-With": "agenda-ia"}
CLIENT_ID = "123.apps.googleusercontent.com"
SCHEDULER = "agenda-ia-agendador@projeto.iam.gserviceaccount.com"
BOTH = f"openid email {CALENDAR_SCOPE} {GMAIL_SEND_SCOPE}"
SOON = (datetime.now(TZ).date() + timedelta(days=2)).isoformat()


class Fake:
    """Google's side of the test: identities by token, code exchanges, the calendar, revoked tokens."""

    def __init__(self):
        self.google = FakeGoogle()
        self.claims = {"token-login": {"email": "dono@example.com", "email_verified": True, "name": "Dono", "sub": "111"}}
        self.exchanges = {}
        self.revoked = []
        self.tokens_used = []


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def client(sample_db, fake_embedder, fake, monkeypatch):
    monkeypatch.setenv("AGENDA_IA_AGENDADOR", SCHEDULER)

    def load():
        demo = Workspace(Assistant(sample_db, fake_embedder, FakeModel()), "exemplo", date(2026, 11, 1), date(2027, 1, 31))
        today = datetime.now(TZ).date()
        empty = SqliteEventStore.in_memory([], np.zeros((0, fake_embedder.dim), np.float32), fake_embedder.dim)
        own = Workspace(Assistant(empty, fake_embedder, FakeModel()), "privada", today - timedelta(days=60),
                        today + timedelta(days=120), persistent=True)

        def api_for(token):
            fake.tokens_used.append(token)
            return fake.google

        google = GoogleService(MemoryConnections(), api_for, revoke=fake.revoked.append)
        return AppState(demo, "fake", lambda user: own, google=google)

    def verifier(token, audience):
        if token.startswith("oidc:"):  # Cloud Scheduler's token
            assert audience == TASKS_AUDIENCE
            email = token.removeprefix("oidc:")
            return {"email": email, "email_verified": True, "aud": audience}
        if audience != CLIENT_ID or token not in fake.claims:
            raise ValueError("assinatura inválida")
        return fake.claims[token]

    auth = AuthSettings(CLIENT_ID, frozenset({"dono@example.com"}), "segredo", verifier, client_secret="s",
                        base_url="https://agenda.example",
                        exchanger=lambda code, cid, secret, uri: fake.exchanges[code])
    with TestClient(create_app(load, RateLimiter(50, 600), auth), base_url="https://testserver",
                    follow_redirects=False) as c:
        assert c.post("/api/login", json={"credential": "token-login"}, headers=HEADERS).status_code == 200
        yield c


def _connect(client, fake, scope=BOTH, sub="111", refresh="refresh-1"):
    start = client.get("/api/minha/google/conectar")
    assert start.status_code == 302
    query = parse_qs(urlparse(start.headers["location"]).query)
    fake.claims["jwt-agenda"] = {"email": "dono@example.com", "email_verified": True, "sub": sub,
                                 "nonce": query["nonce"][0]}
    fake.exchanges["codigo"] = {"id_token": "jwt-agenda", "refresh_token": refresh, "scope": scope}
    back = client.get(f"/api/login/google/retorno?code=codigo&state={query['state'][0]}")
    return query, back.headers["location"]


def _event(**changes):
    return {"titulo": "Dentista", "data": SOON, "dia_inteiro": False, "inicio": "15:00", "fim": "16:00",
            "local": "Clínica Sorriso", "participantes": ["Ana"], "notas": "", **changes}


def test_connecting_asks_for_calendar_and_gmail_with_offline_access(client, fake):
    assert client.get("/api/minha/google").json()["conectado"] is False
    query, location = _connect(client, fake)
    assert query["scope"] == [BOTH] and query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert query["login_hint"] == ["dono@example.com"]
    assert query["redirect_uri"] == ["https://agenda.example/api/login/google/retorno"]  # the login's own address
    assert location == "/?google=conectado#minha"
    status = client.get("/api/minha/google").json()
    assert status["conectado"] and status["agenda"] and status["gmail"] and status["resumo_diario"]
    assert status["email"] == "dono@example.com" and status["resumo_hora"] == "08:00"
    assert client.get("/api/me").json()["logado"] is True  # still signed in


@pytest.mark.parametrize(
    "scope, sub, refresh, outcome",
    [
        (BOTH, "999", "r", "outra-conta"),  # another Google account than the one signed in
        ("openid email", "111", "r", "sem-permissao"),  # both boxes unticked on Google's page
        (BOTH, "111", "", "falhou"),  # no refresh token: the 8 o'clock e-mail could not work
    ],
)
def test_connections_that_cannot_work_are_refused(client, fake, scope, sub, refresh, outcome):
    _, location = _connect(client, fake, scope=scope, sub=sub, refresh=refresh)
    assert location == f"/?google={outcome}#minha"
    assert client.get("/api/minha/google").json()["conectado"] is False


def test_only_the_calendar_box_ticked_turns_off_the_mail(client, fake):
    _connect(client, fake, scope=f"openid email {CALENDAR_SCOPE}")
    status = client.get("/api/minha/google").json()
    assert status["agenda"] and not status["gmail"] and not status["resumo_diario"]
    assert client.post("/api/minha/google/resumo", headers=HEADERS).status_code == 409


def test_cancelled_consent_and_a_forged_answer(client, fake):
    client.get("/api/minha/google/conectar")
    assert client.get("/api/login/google/retorno?error=access_denied").headers["location"] == "/?google=cancelado#minha"
    client.get("/api/minha/google/conectar")
    assert client.get("/api/login/google/retorno?code=x&state=forjado").headers["location"] == "/?google=falhou#minha"


def test_connecting_needs_a_signed_in_person(client):
    client.post("/api/logout", headers=HEADERS)
    assert client.get("/api/minha/google/conectar").headers["location"] == "/"
    assert client.get("/api/minha/google").status_code == 401


def test_once_connected_writes_go_to_google_first(client, fake):
    _connect(client, fake)
    added = client.post("/api/minha/compromissos", json=_event(), headers=HEADERS).json()
    assert added["google"] is True and added["origem"] == "cadastro"
    [item] = fake.google.items.values()
    assert item["summary"] == "Dentista" and item["description"] == "Com: Ana"

    edited = client.put(f"/api/minha/compromissos/{added['id']}", json=_event(titulo="Dentista (retorno)"), headers=HEADERS)
    assert edited.status_code == 200 and item["id"] in fake.google.items
    assert fake.google.items[item["id"]]["summary"] == "Dentista (retorno)"

    assert client.delete(f"/api/minha/compromissos/{added['id']}", headers=HEADERS).status_code == 200
    assert fake.google.items == {}


def test_when_google_refuses_nothing_is_saved_here(client, fake):
    _connect(client, fake)
    fake.google.fail = GoogleError(503, "indisponível")
    response = client.post("/api/minha/compromissos", json=_event(), headers=HEADERS)
    assert response.status_code == 502 and "Google Agenda" in response.json()["detail"]
    assert client.get("/api/minha/agenda").json() == []


def test_revoked_access_drops_the_connection(client, fake):
    _connect(client, fake)
    fake.google.fail = GoogleAccessLost(400, "invalid_grant")
    response = client.post("/api/minha/compromissos", json=_event(), headers=HEADERS)
    assert response.status_code == 409 and "Conecte de novo" in response.json()["detail"]
    assert client.get("/api/minha/google").json()["conectado"] is False
    fake.google.fail = None
    assert client.post("/api/minha/compromissos", json=_event(), headers=HEADERS).json()["google"] is False


def test_sync_brings_google_events_into_the_agenda(client, fake):
    _connect(client, fake)
    start = datetime.now(TZ).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    fake.google.items["x1"] = {"id": "x1", "iCalUID": "x1@google.com", "etag": '"1"', "status": "confirmed",
                               "summary": "Reunião", "start": {"dateTime": start.isoformat()},
                               "end": {"dateTime": (start + timedelta(hours=1)).isoformat()}}
    report = client.post("/api/minha/google/sincronizar", headers=HEADERS).json()
    assert (report["novos"], report["atualizados"], report["apagados"]) == (1, 0, 0)
    [event] = client.get("/api/minha/agenda").json()
    assert event["titulo"] == "Reunião" and event["origem"] == "google" and event["google"] is True
    assert client.get("/api/minha/google").json()["sincronizado_em"] is not None
    assert client.post("/api/minha/google/sincronizar").status_code == 403  # the custom header is required


def test_send_now_mails_todays_list_to_the_account(client, fake):
    _connect(client, fake)
    today = datetime.now(TZ).date().isoformat()
    client.post("/api/minha/compromissos", json=_event(data=today, inicio="23:00", fim="23:30"), headers=HEADERS)
    response = client.post("/api/minha/google/resumo", headers=HEADERS).json()
    assert response == {"enviado_para": "dono@example.com", "compromissos": 1}
    mail = message_from_bytes(fake.google.sent[0], policy=policy.default)
    assert mail["To"] == "dono@example.com" and "1 compromisso" in mail["Subject"]
    assert "Dentista" in mail.get_body(("plain",)).get_content()


def test_the_daily_task_only_runs_for_the_scheduler_and_once_a_day(client, fake):
    _connect(client, fake)
    url = "/api/tarefas/resumo-diario"
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"Authorization": "Bearer lixo"}).status_code == 401
    assert client.post(url, headers={"Authorization": "Bearer oidc:outra@projeto.iam.gserviceaccount.com"}).status_code == 403
    scheduler = {"Authorization": f"Bearer oidc:{SCHEDULER}"}
    assert client.post(url, headers=scheduler).json() == {"enviados": 1, "falhas": 0}
    assert client.post(url, headers=scheduler).json() == {"enviados": 0, "falhas": 0}  # a retry sends nothing
    assert len(fake.google.sent) == 1


def test_the_daily_mail_can_be_turned_off(client, fake):
    _connect(client, fake)
    status = client.put("/api/minha/google", json={"resumo_diario": False}, headers=HEADERS).json()
    assert status["resumo_diario"] is False
    response = client.post("/api/tarefas/resumo-diario", headers={"Authorization": f"Bearer oidc:{SCHEDULER}"})
    assert response.json() == {"enviados": 0, "falhas": 0} and fake.google.sent == []


def test_the_daily_task_is_off_without_a_configured_scheduler(client, monkeypatch):
    monkeypatch.delenv("AGENDA_IA_AGENDADOR")
    assert client.post("/api/tarefas/resumo-diario", headers={"Authorization": f"Bearer oidc:{SCHEDULER}"}).status_code == 503


def test_disconnecting_revokes_and_removes_googles_events(client, fake):
    _connect(client, fake)
    start = datetime.now(TZ).replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    fake.google.items["x1"] = {"id": "x1", "etag": '"1"', "status": "confirmed", "summary": "Reunião",
                               "start": {"dateTime": start.isoformat()},
                               "end": {"dateTime": (start + timedelta(hours=1)).isoformat()}}
    client.post("/api/minha/google/sincronizar", headers=HEADERS)
    mine = client.post("/api/minha/compromissos", json=_event(), headers=HEADERS).json()
    status = client.delete("/api/minha/google", headers=HEADERS).json()
    assert status["conectado"] is False and fake.revoked == ["refresh-1"]
    [left] = client.get("/api/minha/agenda").json()
    assert left["id"] == mine["id"] and left["google"] is False  # stays here, no longer linked
    assert "x1" in fake.google.items  # nothing is deleted in Google


def test_the_demo_never_touches_google(client, fake):
    _connect(client, fake)
    fake.google.calls.clear()
    assert client.post("/api/demo/compromissos", json=_event(), headers=HEADERS).status_code == 201
    assert fake.google.calls == []


def test_a_server_without_the_client_secret_offers_no_connection(sample_db, fake_embedder):
    def load():
        demo = Workspace(Assistant(sample_db, fake_embedder, FakeModel()), "exemplo", date(2026, 11, 1), date(2027, 1, 31))
        return AppState(demo, "fake", lambda user: demo)

    auth = AuthSettings(CLIENT_ID, frozenset({"dono@example.com"}), "segredo",
                        lambda token, audience: {"email": "dono@example.com", "email_verified": True, "sub": "111"})
    with TestClient(create_app(load, RateLimiter(50, 600), auth), base_url="https://testserver",
                    follow_redirects=False) as client:
        client.post("/api/login", json={"credential": "x" * 12}, headers=HEADERS)
        assert client.get("/api/minha/google").json()["disponivel"] is False
        assert client.get("/api/minha/google/conectar").headers["location"] == "/?google=indisponivel#minha"
