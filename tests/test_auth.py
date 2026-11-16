from datetime import date

import numpy as np
import pytest
from conftest import FakeModel
from fastapi.testclient import TestClient

from agenda_ia.api import AppState, RateLimiter, Workspace, create_app
from agenda_ia.assistant import Assistant
from agenda_ia.auth import COOKIE_NAME, AuthSettings
from agenda_ia.store import SqliteEventStore

HEADERS = {"X-Requested-With": "agenda-ia"}
CLIENT_ID = "123.apps.googleusercontent.com"


def fake_verifier(token: str, audience: str) -> dict:
    """Stands in for Google: 'tokens' are just names of fixed identities."""
    assert audience == CLIENT_ID
    identities = {
        "token-dono": {"email": "Dono@Example.com", "email_verified": True, "name": "Dono", "sub": "111"},
        "token-estranho": {"email": "outra@example.com", "email_verified": True, "name": "Outra", "sub": "222"},
        "token-nao-verificado": {"email": "dono@example.com", "email_verified": False, "sub": "111"},
    }
    if token not in identities:
        raise ValueError("assinatura inválida")
    return identities[token]


@pytest.fixture
def client(sample_db, fake_embedder):
    created = []

    def load():
        demo = Workspace(Assistant(sample_db, fake_embedder, FakeModel()), "exemplo", date(2026, 11, 1), date(2027, 1, 31))

        def new_private(user):
            created.append(user.email)
            empty = SqliteEventStore.in_memory([], np.zeros((0, fake_embedder.dim), np.float32), fake_embedder.dim)
            return Workspace(Assistant(empty, fake_embedder, FakeModel()), "privada", date(2026, 11, 1), date(2027, 1, 31))

        return AppState(demo, "fake", new_private)

    auth = AuthSettings(CLIENT_ID, frozenset({"dono@example.com"}), "segredo-de-teste", fake_verifier)
    with TestClient(create_app(load, RateLimiter(50, 600), auth), base_url="https://testserver") as c:
        c.created = created
        yield c


def _login(client, token="token-dono"):
    return client.post("/api/login", json={"credential": token}, headers=HEADERS)


def test_config_exposes_only_the_public_client_id(client):
    assert client.get("/api/config").json() == {"login": True, "google_client_id": CLIENT_ID}


def test_allowed_account_gets_a_secure_session_cookie(client):
    response = _login(client)
    assert response.status_code == 200 and response.json()["email"] == "dono@example.com"
    cookie = response.headers["set-cookie"].lower()
    assert COOKIE_NAME in cookie and "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert client.get("/api/me").json() == {"logado": True, "nome": "Dono", "email": "dono@example.com"}


@pytest.mark.parametrize(
    ("token", "status"),
    [("token-estranho", 403), ("token-nao-verificado", 401), ("token-falso-qualquer", 401)],
)
def test_other_accounts_and_bad_tokens_are_refused(client, token, status):
    assert _login(client, token).status_code == status
    assert client.get("/api/me").json() == {"logado": False, "nome": None, "email": None}


def test_login_requires_the_custom_header(client):
    assert client.post("/api/login", json={"credential": "token-dono"}).status_code == 403


def test_private_routes_need_a_session(client):
    assert client.get("/api/minha/status").status_code == 401
    assert client.get("/api/minha/agenda").status_code == 401
    assert client.post("/api/minha/perguntar", json={"pergunta": "amanhã?"}, headers=HEADERS).status_code == 401
    assert client.created == []


def test_private_agenda_is_separate_from_the_demo(client):
    _login(client)
    status = client.get("/api/minha/status").json()
    assert status["origem"] == "privada" and status["eventos"] == 0
    assert client.get("/api/minha/agenda").json() == []
    assert client.get("/api/demo/status").json()["eventos"] > 50
    assert client.created == ["dono@example.com"]


def test_tampered_cookie_is_ignored(client):
    _login(client)
    value = client.cookies.get(COOKIE_NAME)
    tampered = value[:-1] + ("A" if value[-1] != "A" else "B")  # breaks the signature
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, tampered)
    assert client.get("/api/minha/status").status_code == 401
    client.cookies.clear()
    client.cookies.set(COOKIE_NAME, value)
    assert client.get("/api/minha/status").status_code == 200


def test_logout_ends_the_session(client):
    _login(client)
    assert client.post("/api/logout", headers=HEADERS).status_code == 200
    assert client.get("/api/minha/status").status_code == 401


def test_login_disabled_without_configuration(sample_db, fake_embedder):
    def load():
        demo = Workspace(Assistant(sample_db, fake_embedder, FakeModel()), "exemplo", date(2026, 11, 1), date(2027, 1, 31))
        return AppState(demo, "fake", lambda user: demo)

    auth = AuthSettings(None, frozenset(), "x", fake_verifier)
    with TestClient(create_app(load, RateLimiter(5, 600), auth), base_url="https://testserver") as client:
        assert client.get("/api/config").json() == {"login": False, "google_client_id": None}
        assert _login(client).status_code == 503
