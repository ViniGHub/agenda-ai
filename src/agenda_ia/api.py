"""HTTP layer (FastAPI). It only translates HTTP <-> Assistant; the RAG logic lives in the other modules.

    uvicorn agenda_ia.api:app --port 8000           # API (and the built React app, if web/dist exists)
    AGENDA_ICS=agenda_real/minha.ics uvicorn ...    # your own agenda, locally

The response models below are the contract with the front-end; web/src/types.ts mirrors them.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .assistant import Assistant
from .calendar_loader import format_when, load_events
from .config import ROOT, TZ
from .llm import LLMError, UnavailableModel
from .store import StoredEvent

WEB_DIST = ROOT / "web" / "dist"
MAX_QUESTION_CHARS = 300


# ---------- contract ----------

class EventOut(BaseModel):
    id: int
    titulo: str
    inicio: datetime
    fim: datetime
    dia_inteiro: bool
    quando: str
    local: str
    participantes: list[str]
    notas: str


class DetailsOut(BaseModel):
    tipo: str
    de: date | None
    ate: date | None
    busca: str | None
    reserva: str | None = Field(description="por que o entendimento da pergunta caiu para a busca simples")
    estrategia: str
    recuperados: list[EventOut]
    guarda: str | None = Field(description="qual guarda agiu na resposta, se alguma")
    tokens: int
    chamadas: int
    segundos: float


class AnswerOut(BaseModel):
    resposta: str
    encontrou: bool
    fontes: list[EventOut]
    detalhes: DetailsOut


class QuestionIn(BaseModel):
    pergunta: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


class StatusOut(BaseModel):
    origem: str  # "exemplo" (fictitious, rebuilt around today) or "arquivo" (AGENDA_ICS)
    eventos: int
    de: date
    ate: date
    llm: bool
    modelo: str | None
    agora: datetime


def event_out(stored: StoredEvent) -> EventOut:
    e = stored.event
    return EventOut(
        id=stored.id, titulo=e.title, inicio=e.start, fim=e.end, dia_inteiro=e.all_day, quando=format_when(e),
        local=e.location, participantes=list(e.attendees), notas=e.description,
    )


# ---------- state ----------

@dataclass
class AppState:
    assistant: Assistant
    origin: str
    first_day: date
    last_day: date
    llm_model: str | None
    lock: threading.Lock = field(default_factory=threading.Lock)


def load_state() -> AppState:
    """Indexes the agenda in memory at startup (nothing is written on the server)."""
    from .embeddings import E5Embedder
    from .llm import GroqModel
    from .sample import build as build_sample

    today = datetime.now(TZ).date()
    first_day, last_day = today - timedelta(days=60), today + timedelta(days=120)
    if os.getenv("AGENDA_ICS"):
        path, origin = Path(os.environ["AGENDA_ICS"]), "arquivo"
    else:
        path, origin = Path(tempfile.gettempdir()) / "agenda_ia_exemplo.ics", "exemplo"
        path.write_bytes(build_sample(today).to_ical())

    events = load_events(path, first_day, last_day + timedelta(days=1))
    embedder = E5Embedder()
    conn = store.connect(":memory:", check_same_thread=False)
    store.rebuild(conn, events, embedder.passages([e.search_text() for e in events]), embedder.dim)

    try:
        model = GroqModel()
        llm_model = model.model
    except LLMError as error:
        model, llm_model = UnavailableModel(str(error)), None
    return AppState(Assistant(conn, embedder, model), origin, first_day, last_day, llm_model)


class RateLimiter:
    """Sliding window per client. The demo runs on a free LLM quota, so one visitor must not drain it."""

    def __init__(self, limit: int, seconds: int):
        self.limit, self.seconds = limit, seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[client]
            while hits and now - hits[0] > self.seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


def client_id(request: Request) -> str:
    # Behind the Hugging Face proxy the real client comes first in X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "desconhecido")


# ---------- app ----------

def create_app(load: Callable[[], AppState] = load_state, limiter: RateLimiter | None = None) -> FastAPI:
    limiter = limiter or RateLimiter(int(os.getenv("AGENDA_IA_LIMITE", "20")), 600)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.agenda = load()
        yield

    app = FastAPI(title="agenda-ia", version="0.1.0", lifespan=lifespan)

    def state(request: Request) -> AppState:
        return request.app.state.agenda

    @app.get("/api/status", response_model=StatusOut)
    def get_status(request: Request) -> StatusOut:
        s = state(request)
        with s.lock:
            total = store.count_events(s.assistant.conn)
        return StatusOut(
            origem=s.origin, eventos=total, de=s.first_day, ate=s.last_day,
            llm=s.llm_model is not None, modelo=s.llm_model, agora=datetime.now(TZ),
        )

    @app.get("/api/agenda", response_model=list[EventOut])
    def get_agenda(request: Request) -> list[EventOut]:
        s = state(request)
        with s.lock:
            return [event_out(e) for e in store.events_between(s.assistant.conn, None, None, limit=100_000)]

    @app.post("/api/perguntar", response_model=AnswerOut)
    def ask(body: QuestionIn, request: Request) -> AnswerOut:
        if not limiter.allow(client_id(request)):
            raise HTTPException(429, "Limite da demonstração atingido. Tente de novo em alguns minutos.")
        s = state(request)
        with s.lock:  # one question at a time: shared SQLite connection and a per-minute token quota
            r = s.assistant.ask(body.pergunta.strip())
        return AnswerOut(
            resposta=r.answer.text,
            encontrou=r.answer.found,
            fontes=[event_out(e) for e in r.answer.cited],
            detalhes=DetailsOut(
                tipo=r.query.tipo, de=r.query.de, ate=r.query.ate, busca=r.query.busca, reserva=r.query_note,
                estrategia=r.strategy, recuperados=[event_out(e) for e in r.retrieved], guarda=r.answer.note,
                tokens=r.usage.total, chamadas=r.usage.calls, segundos=round(r.seconds, 2),
            ),
        )

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
