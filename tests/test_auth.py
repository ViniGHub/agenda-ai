from datetime import date
from urllib.parse import parse_qs, urlparse

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


def _soon():
    from datetime import datetime, timedelta

    from agenda_ia.config import TZ

    return (datetime.now(TZ).date() + timedelta(days=2)).isoformat()


def _login(client, token="token-dono"):
    return client.post("/api/login", json={"credential": token}, headers=HEADERS)


def test_config_exposes_only_the_public_client_id(client):
    assert client.get("/api/config").json() == {"login": True, "google_client_id": CLIENT_ID, "login_redirect": False}


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
    assert client.post("/api/minha/interpretar", json={"frase": "dentista amanhã"}, headers=HEADERS).status_code == 401
    audio = {**HEADERS, "Content-Type": "audio/webm"}
    assert client.post("/api/minha/transcrever", content=bytes(2000), headers=audio).status_code == 401
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
        assert client.get("/api/config").json() == {"login": False, "google_client_id": None, "login_redirect": False}
        assert _login(client).status_code == 503


def test_one_cookie_holds_the_login_and_the_demo_copy(client):
    assert _login(client).status_code == 200
    added = client.post("/api/demo/compromissos", json={"titulo": "Teste", "data": _soon(), "inicio": "10:00"},
                        headers=HEADERS)
    assert added.status_code == 201
    assert list(client.cookies.keys()) == [COOKIE_NAME]  # Firebase Hosting forwards only "__session"
    assert client.get("/api/minha/status").status_code == 200  # still signed in
    demo_count = client.get("/api/demo/status").json()["eventos"]
    client.post("/api/logout", headers=HEADERS)
    assert client.get("/api/minha/status").status_code == 401
    assert client.get("/api/demo/status").json()["eventos"] == demo_count  # the demo copy survives logout


def test_re_signing_for_the_demo_does_not_extend_a_login(client):
    auth = AuthSettings(CLIENT_ID, frozenset({"dono@example.com"}), "segredo-de-teste", fake_verifier)
    old_login = {"user": {"email": "dono@example.com", "name": "Dono", "sub": "111", "at": 0}, "demo": "x"}
    client.cookies.set(COOKIE_NAME, auth.session_cookie(old_login))
    assert client.get("/api/minha/status").status_code == 401


def test_api_answers_are_never_cached(client):
    assert client.get("/api/demo/status").headers["cache-control"] == "no-store"


# ---------- redirect sign-in (the installed iPhone app) ----------

def _redirect_client(sample_db, fake_embedder, exchanged):
    """An app whose Google is fake: the code exchange returns `exchanged`, the verifier reads the token as a key."""
    def load():
        demo = Workspace(Assistant(sample_db, fake_embedder, FakeModel()), "exemplo", date(2026, 11, 1), date(2027, 1, 31))
        empty = SqliteEventStore.in_memory([], np.zeros((0, fake_embedder.dim), np.float32), fake_embedder.dim)
        own = Workspace(Assistant(empty, fake_embedder, FakeModel()), "privada", date(2026, 11, 1), date(2027, 1, 31))
        return AppState(demo, "fake", lambda user: own)

    def verifier(token, audience):
        assert audience == CLIENT_ID
        return exchanged["claims"][token]

    auth = AuthSettings(CLIENT_ID, frozenset({"dono@example.com"}), "segredo-de-teste", verifier,
                        client_secret="segredo-do-cliente", base_url="https://agenda.example",
                        exchanger=lambda code, cid, secret, uri: exchanged["tokens"][code])
    return TestClient(create_app(load, RateLimiter(50, 600), auth), base_url="https://testserver", follow_redirects=False)


def _start(client):
    response = client.get("/api/login/google")
    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    return query["state"][0], query["nonce"][0], query


def test_redirect_sign_in_goes_to_google_and_back(sample_db, fake_embedder):
    claims = {}
    exchanged = {"tokens": {"codigo-1": {"id_token": "jwt-1"}}, "claims": claims}
    with _redirect_client(sample_db, fake_embedder, exchanged) as client:
        assert client.get("/api/config").json()["login_redirect"] is True
        state, nonce, query = _start(client)
        assert query["redirect_uri"] == ["https://agenda.example/api/login/google/retorno"]
        assert query["scope"] == ["openid email profile"] and query["client_id"] == [CLIENT_ID]
        claims["jwt-1"] = {"email": "dono@example.com", "email_verified": True, "name": "Dono", "sub": "111", "nonce": nonce}
        back = client.get(f"/api/login/google/retorno?code=codigo-1&state={state}")
        assert back.status_code == 302 and back.headers["location"] == "/#minha"
        assert client.get("/api/me").json()["logado"] is True
        assert client.get("/api/minha/status").status_code == 200


@pytest.mark.parametrize(
    "tamper, reason",
    [
        ("state", "falhou"),  # an answer this browser did not ask for (login CSRF)
        ("nonce", "falhou"),  # an ID token minted for another attempt
        ("email", "sem-acesso"),  # a Google account outside the allowlist
    ],
)
def test_redirect_sign_in_refuses_what_does_not_match(sample_db, fake_embedder, tamper, reason):
    claims = {}
    exchanged = {"tokens": {"codigo-1": {"id_token": "jwt-1"}}, "claims": claims}
    with _redirect_client(sample_db, fake_embedder, exchanged) as client:
        state, nonce, _ = _start(client)
        claims["jwt-1"] = {
            "email": "outra@example.com" if tamper == "email" else "dono@example.com", "email_verified": True,
            "sub": "1", "nonce": "outro" if tamper == "nonce" else nonce,
        }
        sent_state = "forjado" if tamper == "state" else state
        back = client.get(f"/api/login/google/retorno?code=codigo-1&state={sent_state}")
        assert back.headers["location"] == f"/?erro_login={reason}"
        assert client.get("/api/me").json()["logado"] is False


def test_cancelled_or_unstarted_sign_in_is_explained(sample_db, fake_embedder):
    with _redirect_client(sample_db, fake_embedder, {"tokens": {}, "claims": {}}) as client:
        assert client.get("/api/login/google/retorno?error=access_denied").headers["location"] == "/?erro_login=cancelado"
        assert client.get("/api/login/google/retorno?code=x&state=y").headers["location"] == "/?erro_login=falhou"


def test_without_client_secret_the_popup_is_used(client):
    assert client.get("/api/config").json()["login_redirect"] is False
    response = client.get("/api/login/google", follow_redirects=False)
    assert response.headers["location"] == "/?erro_login=indisponivel"


def test_session_lasts_thirty_days(client):
    cookie = _login(client).headers["set-cookie"].lower()
    assert "max-age=2592000" in cookie
