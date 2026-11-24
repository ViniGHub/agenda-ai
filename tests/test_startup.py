"""The server answers while the start-up load (model + demo index) is still running."""

import threading
import time
from datetime import date

import pytest
from conftest import FakeModel
from fastapi.testclient import TestClient

from agenda_ia.api import server as api
from agenda_ia.api.server import AppState, RateLimiter, Workspace, create_app
from agenda_ia.chat.assistant import Assistant
from agenda_ia.api.auth import AuthSettings


@pytest.fixture
def slow(sample_db, fake_embedder, monkeypatch):
    """An app whose load reports "modelo" and then waits for `release`; short waits so tests stay fast."""
    monkeypatch.setattr(api, "READY_WAIT", 0.3)
    monkeypatch.setattr(api, "STAGE_WAIT", 0.3)
    release = threading.Event()
    failure = {}

    def load(progress):
        progress("modelo")
        release.wait(10)
        if "erro" in failure:
            raise failure["erro"]
        progress("exemplo")
        demo = Workspace(Assistant(sample_db, fake_embedder, FakeModel()), "exemplo", date(2026, 11, 1), date(2027, 1, 31))
        return AppState(demo, "fake", lambda user: demo)

    auth = AuthSettings(client_id="id", allowed=frozenset({"dono@example.com"}), secret="s")
    with TestClient(create_app(load, RateLimiter(50, 600), auth), base_url="https://testserver") as client:
        client.release, client.failure = release, failure
        yield client
        release.set()


def test_cover_and_login_answer_while_loading(slow):
    started = time.monotonic()
    assert slow.get("/api/config").status_code == 200
    assert slow.get("/api/me").json() == {"logado": False, "nome": None, "email": None}
    assert time.monotonic() - started < 0.25  # neither waited for the load
    ready = slow.get("/api/pronto").json()
    assert ready["pronto"] is False and ready["etapa"] == "modelo" and ready["segundos"] >= 0


def test_routes_that_need_the_agenda_wait_then_say_it_is_still_waking(slow):
    response = slow.get("/api/demo/status")
    assert response.status_code == 503 and "acordando" in response.json()["detail"]
    slow.release.set()
    assert slow.get("/api/demo/status").status_code == 200


def test_ready_is_a_long_poll_that_returns_on_the_next_step(slow):
    started = time.monotonic()
    assert slow.get("/api/pronto?etapa=modelo").json()["etapa"] == "modelo"  # nothing new: waited STAGE_WAIT
    assert time.monotonic() - started >= 0.25
    assert slow.get("/api/pronto?etapa=servidor").json()["etapa"] == "modelo"  # already past it: at once
    slow.release.set()
    seen, known = [], "modelo"
    while known != "pronto" and len(seen) < 5:  # each answer is the next step, as the page polls
        known = slow.get(f"/api/pronto?etapa={known}").json()["etapa"]
        seen.append(known)
    assert seen[-1] == "pronto" and set(seen) <= {"exemplo", "pronto"}
    assert slow.get("/api/pronto?etapa=pronto").json()["pronto"] is True  # never waits once ready


def test_a_failed_load_is_reported_not_hidden(slow):
    slow.failure["erro"] = RuntimeError("banco fora do ar")
    slow.release.set()
    assert slow.get("/api/pronto?etapa=modelo").json()["etapa"] == "falhou"
    response = slow.get("/api/demo/status")
    assert response.status_code == 503 and "não conseguiu iniciar" in response.json()["detail"]
    assert slow.get("/api/config").status_code == 200  # the cover still opens


def test_the_morning_task_waits_longer_than_the_page(sample_db, fake_embedder, monkeypatch):
    """Cloud Scheduler wakes the server itself, so its call waits past READY_WAIT for the load."""
    monkeypatch.setattr(api, "READY_WAIT", 0.3)
    monkeypatch.setattr(api, "TASK_READY_WAIT", 5.0)
    monkeypatch.setenv("AGENDA_IA_AGENDADOR", "agendador@projeto.iam.gserviceaccount.com")
    release = threading.Event()

    def load():
        release.wait(10)
        demo = Workspace(Assistant(sample_db, fake_embedder, FakeModel()), "exemplo", date(2026, 11, 1), date(2027, 1, 31))
        return AppState(demo, "fake", lambda user: demo)

    scheduler = lambda token, audience: {"email": "agendador@projeto.iam.gserviceaccount.com", "email_verified": True}  # noqa: E731
    auth = AuthSettings(client_id="id", allowed=frozenset(), secret="s", verifier=scheduler)
    with TestClient(create_app(load, RateLimiter(50, 600), auth), base_url="https://testserver") as client:
        threading.Timer(0.8, release.set).start()  # longer than READY_WAIT, shorter than the task's wait
        response = client.post("/api/tarefas/resumo-diario", headers={"Authorization": "Bearer token"})
        release.set()
    assert response.status_code == 200 and response.json() == {"enviados": 0, "falhas": 0}
