"""HTTP layer (FastAPI). It only translates HTTP <-> Assistant and the stores; the RAG logic lives elsewhere.

    uvicorn agenda_ia.api:app --port 8000           # API (and the built React app, if web/dist exists)

Two workspaces share the same question pipeline:
  /api/demo/...   the fictitious agenda, open to anyone. Events a visitor adds go to a private copy
                  of the demo, kept in memory for a couple of hours and tied to the session cookie.
  /api/minha/...  the signed-in person's own agenda (Google sign-in + allowlist, see auth.py).
                  Stored in Postgres when DATABASE_URL is set (Neon), otherwise in memory.

Both have .../interpretar: one sentence in, the event form filled in out. It saves nothing; the
person checks the form and sends it to .../compromissos like any other new event.

The chat (.../perguntar) also takes new appointments. An explicit order with a day and a time
("marca dentista quinta às 15h") is saved at once and the reply offers an undo; a message that only
describes an appointment ("dentista quinta às 15h") comes back as a question; a missing day or
time comes back as a form to complete. AnswerOut.acao says which one happened.

Voice messages: .../transcrever takes the recorded audio (raw body, Content-Type audio/*) and
returns the text; the page then sends that text to .../perguntar like a typed message.

Locally, AGENDA_ICS=agenda_real/minha.ics loads your real .ics into an in-memory private workspace.
The response models below are the contract with the front-end; web/src/types.ts mirrors them.
"""

from __future__ import annotations

import os
import secrets
import tempfile
import threading
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from datetime import time as dt_time  # the module name "time" is taken by the clock below
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from .assistant import Assistant
from .auth import COOKIE_NAME, CSRF_HEADER, CSRF_VALUE, SESSION_SECONDS, AuthError, AuthSettings, User
from .calendar_loader import format_when, load_events
from .config import ROOT, TZ
from .drafting import NoEventError, Suggestion, draft_from_text, explicit_request
from .editing import MAX_IMPORT_CHARS, EventDraft, ImportError_, import_events
from .llm import LLMError, UnavailableModel
from .speech import AUDIO_TYPES, Transcriber, UnavailableTranscriber
from .store import BASE, CREATED, IMPORTED, SqliteEventStore, StoredEvent

WEB_DIST = ROOT / "web" / "dist"
MAX_QUESTION_CHARS = 300
DEMO_SESSION_SECONDS = 2 * 60 * 60
DEMO_MAX_SESSIONS = 200
MAX_EVENTS_PER_AGENDA = 3_000
MAX_AUDIO_BYTES = 2_000_000  # a minute of speech is ~250 KB in Opus and ~1 MB in AAC
MIN_AUDIO_BYTES = 500


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
    origem: str  # "base" (part of the agenda), "cadastro" (added by the person) or "importado"


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


class DraftOut(BaseModel):
    """The event form, filled in from a message. Mirrors editing.EventDraft, with gaps allowed."""

    titulo: str
    data: date | None
    dia_inteiro: bool
    inicio: str | None  # HH:MM
    fim: str | None
    local: str
    participantes: list[str]
    notas: str
    faltando: list[str] = Field(description="campos que a frase não trouxe e o formulário exige: data, inicio")


class ActionOut(BaseModel):
    """What the chat did with a message that asked to write down a new appointment."""

    tipo: Literal["anotado", "confirmar", "completar"]  # saved (undo offered) / asks first / form needed
    rascunho: DraftOut
    evento: EventOut | None = None  # the saved event, when "anotado"


class AnswerOut(BaseModel):
    resposta: str
    encontrou: bool
    fontes: list[EventOut]
    detalhes: DetailsOut
    acao: ActionOut | None = None


class QuestionIn(BaseModel):
    pergunta: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    # "auto": the model tells questions from new appointments; "pergunta": the person said it was a
    # question; "anotar": the person asked to write it down (then it is never saved without a look).
    modo: Literal["auto", "pergunta", "anotar"] = "auto"


class StatusOut(BaseModel):
    origem: str  # "exemplo" (fictitious), "arquivo" (AGENDA_ICS) or "privada"
    eventos: int
    de: date
    ate: date
    llm: bool
    modelo: str | None
    agora: datetime
    persistente: bool  # False: what is added here disappears when the server restarts (or, in the demo, in 2 hours)
    audio: bool  # voice messages can be transcribed


class ConfigOut(BaseModel):
    login: bool
    google_client_id: str | None


class MeOut(BaseModel):
    logado: bool
    nome: str | None = None
    email: str | None = None


class LoginIn(BaseModel):
    credential: str = Field(min_length=10, max_length=8192)


class SentenceIn(BaseModel):
    frase: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


class SuggestionOut(DraftOut):
    """The form, filled in from one sentence. Nothing was saved: the person reviews it and sends the form."""

    tokens: int


class TranscriptOut(BaseModel):
    texto: str  # what was said; the page sends it to .../perguntar


class ImportIn(BaseModel):
    conteudo: str = Field(min_length=1, max_length=MAX_IMPORT_CHARS)


class ImportOut(BaseModel):
    encontrados: int  # occurrences in the agenda's window
    importados: int  # new ones (repeats of earlier imports are skipped)


def _clock(value: dt_time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


def draft_out(s: Suggestion) -> DraftOut:
    return DraftOut(
        titulo=s.titulo, data=s.data, dia_inteiro=s.dia_inteiro, inicio=_clock(s.inicio), fim=_clock(s.fim),
        local=s.local, participantes=s.participantes, notas=s.notas, faltando=s.missing,
    )


def event_draft(s: Suggestion) -> EventDraft:
    """Raises ValidationError if the suggestion does not pass the form's own rules."""
    return EventDraft(
        titulo=s.titulo, data=s.data, dia_inteiro=s.dia_inteiro, inicio=s.inicio, fim=s.fim,
        local=s.local, participantes=s.participantes, notas=s.notas,
    )


def event_out(stored: StoredEvent) -> EventOut:
    e = stored.event
    return EventOut(
        id=stored.id, titulo=e.title, inicio=e.start, fim=e.end, dia_inteiro=e.all_day, quando=format_when(e),
        local=e.location, participantes=list(e.attendees), notas=e.description, origem=stored.origin,
    )


# ---------- state ----------

@dataclass
class Workspace:
    assistant: Assistant
    origin: str
    first_day: date
    last_day: date
    persistent: bool = False

    @property
    def store(self):
        return self.assistant.store


@dataclass
class AppState:
    demo: Workspace
    llm_model: str | None
    new_private: Callable[[User], Workspace]
    private: dict[str, Workspace] = field(default_factory=dict)
    demo_sessions: "OrderedDict[str, tuple[Workspace, float]]" = field(default_factory=OrderedDict)
    # One lock for everything: the SQLite connections are shared between threads, and the
    # LLM quota (tokens per minute) is shared by every workspace anyway.
    lock: threading.Lock = field(default_factory=threading.Lock)
    transcriber: Transcriber = field(default_factory=UnavailableTranscriber)

    def private_for(self, user: User) -> Workspace:
        key = user.sub or user.email
        if key not in self.private:
            self.private[key] = self.new_private(user)
        return self.private[key]

    def demo_for(self, token: str | None, create: bool = False) -> tuple[Workspace, str | None]:
        """The visitor's own copy of the demo if they have one (or `create`), else the shared demo."""
        now = time.monotonic()
        for stale in [t for t, (_, used) in self.demo_sessions.items() if now - used > DEMO_SESSION_SECONDS]:
            del self.demo_sessions[stale]
        if token and token in self.demo_sessions:
            workspace, _ = self.demo_sessions.pop(token)
            self.demo_sessions[token] = (workspace, now)
            return workspace, token
        if not create:
            return self.demo, None
        base = self.demo
        copy = Workspace(Assistant(base.store.clone(), base.assistant.embedder, base.assistant.model),
                         base.origin, base.first_day, base.last_day)
        token = secrets.token_urlsafe(24)
        self.demo_sessions[token] = (copy, now)
        while len(self.demo_sessions) > DEMO_MAX_SESSIONS:
            self.demo_sessions.popitem(last=False)  # forget the least recently used copy
        return copy, token


def load_state() -> AppState:
    """Loads the model once, indexes the demo agenda in memory and connects to the database if configured."""
    from .embeddings import E5Embedder
    from .llm import GroqModel
    from .speech import GroqTranscriber
    from .sample import build as build_sample

    today = datetime.now(TZ).date()
    first_day, last_day = today - timedelta(days=60), today + timedelta(days=120)
    embedder = E5Embedder()
    try:
        model = GroqModel()
        llm_model = model.model
    except LLMError as error:
        model, llm_model = UnavailableModel(str(error)), None
    try:
        transcriber: Transcriber = GroqTranscriber()
    except LLMError as error:
        transcriber = UnavailableTranscriber(str(error))

    def memory_workspace(path: Path | None, origin: str) -> Workspace:
        events = load_events(path, first_day, last_day + timedelta(days=1)) if path else []
        vectors = embedder.passages([e.search_text() for e in events]) if events else np.zeros((0, embedder.dim), np.float32)
        store = SqliteEventStore.in_memory(events, vectors, embedder.dim)
        return Workspace(Assistant(store, embedder, model), origin, first_day, last_day)

    sample_path = Path(tempfile.gettempdir()) / "agenda_ia_exemplo.ics"
    sample_path.write_bytes(build_sample(today).to_ical())
    demo = memory_workspace(sample_path, "exemplo")

    if os.getenv("DATABASE_URL"):
        from .pgstore import PostgresDatabase

        db = PostgresDatabase(os.environ["DATABASE_URL"], embedder.dim)

        def new_private(user: User) -> Workspace:
            store = db.for_owner(user.sub or user.email)
            return Workspace(Assistant(store, embedder, model), "privada", first_day, last_day, persistent=True)
    else:
        # Without a database the private agenda lives in memory. Locally, AGENDA_ICS fills it with your .ics.
        own_ics = Path(os.environ["AGENDA_ICS"]) if os.getenv("AGENDA_ICS") else None

        def new_private(user: User) -> Workspace:
            return memory_workspace(own_ics, "arquivo" if own_ics else "privada")

    return AppState(demo, llm_model, new_private, transcriber=transcriber)


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
    # Behind Cloud Run's front end the real client comes first in X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "desconhecido")


# ---------- app ----------

def create_app(
    load: Callable[[], AppState] = load_state,
    limiter: RateLimiter | None = None,
    auth: AuthSettings | None = None,
) -> FastAPI:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # local development only; in production the variables come from Cloud Run
    limiter = limiter or RateLimiter(int(os.getenv("AGENDA_IA_LIMITE", "20")), 600)
    write_limiter = RateLimiter(int(os.getenv("AGENDA_IA_LIMITE_ESCRITA", "30")), 600)
    login_limiter = RateLimiter(10, 600)
    audio_limiter = RateLimiter(int(os.getenv("AGENDA_IA_LIMITE_AUDIO", "15")), 600)
    auth = auth or AuthSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.agenda = load()
        yield

    app = FastAPI(title="agenda-ia", version="0.5.0", lifespan=lifespan)

    @app.middleware("http")
    async def cache_rules(request: Request, call_next):
        # Firebase Hosting is a CDN in front of this app: say what it may keep.
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"  # personal answers, never cached
        elif path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # names carry a content hash
        else:
            response.headers["Cache-Control"] = "no-cache"  # index.html: always check for a new build
        return response

    def state(request: Request) -> AppState:
        return request.app.state.agenda

    def same_site_only(request: Request) -> None:
        # Cookie-authenticated writes must carry a custom header. A page on another site cannot add it
        # without a CORS preflight, which this API never approves, so cross-site requests stop here.
        if request.headers.get(CSRF_HEADER) != CSRF_VALUE:
            raise HTTPException(403, "Requisição recusada.")

    def session_of(request: Request) -> dict:
        return auth.read_session(request.cookies.get(COOKIE_NAME))

    def save_session(response: Response, session: dict) -> None:
        if session:
            response.set_cookie(COOKIE_NAME, auth.session_cookie(session), max_age=SESSION_SECONDS,
                                httponly=True, secure=True, samesite="lax", path="/")
        else:
            response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="lax")

    def current_user(request: Request) -> User | None:
        return auth.user_in(session_of(request))

    def require_user(request: Request) -> User:
        user = current_user(request)
        if user is None:
            raise HTTPException(401, "Entre com a sua conta para ver a sua agenda.")
        return user

    def limit_writes(request: Request) -> None:
        if not write_limiter.allow(client_id(request)):
            raise HTTPException(429, "Muitas alterações seguidas. Tente de novo em alguns minutos.")

    # ----- shared handlers -----

    def status_of(s: AppState, ws: Workspace) -> StatusOut:
        with s.lock:
            total = ws.store.count()
        return StatusOut(origem=ws.origin, eventos=total, de=ws.first_day, ate=ws.last_day,
                         llm=s.llm_model is not None, modelo=s.llm_model, agora=datetime.now(TZ),
                         persistente=ws.persistent, audio=not isinstance(s.transcriber, UnavailableTranscriber))

    def agenda_of(s: AppState, ws: Workspace) -> list[EventOut]:
        with s.lock:
            return [event_out(e) for e in ws.store.events_between(None, None, limit=100_000)]

    def answer_in(s: AppState, ws: Workspace, body: QuestionIn, request: Request,
                  saving_workspace: Callable[[], Workspace]) -> AnswerOut:
        """saving_workspace: where a new appointment goes (the demo makes the visitor's copy only then)."""
        if not limiter.allow(client_id(request)):
            raise HTTPException(429, "Limite de perguntas atingido. Tente de novo em alguns minutos.")
        question = body.pergunta.strip()
        with s.lock:  # one question at a time: shared SQLite connections and a per-minute token quota
            r = ws.assistant.ask(question, mode=body.modo)
        text, action = r.answer.text, None
        if r.suggestion is not None:
            text, action = note_from_chat(s, r.suggestion, question, request, saving_workspace,
                                          may_save=body.modo == "auto")
        return AnswerOut(
            resposta=text,
            encontrou=r.answer.found,
            fontes=[event_out(e) for e in r.answer.cited],
            detalhes=DetailsOut(
                tipo=r.query.tipo, de=r.query.de, ate=r.query.ate, busca=r.query.busca, reserva=r.query_note,
                estrategia=r.strategy, recuperados=[event_out(e) for e in r.retrieved], guarda=r.answer.note,
                tokens=r.usage.total, chamadas=r.usage.calls, segundos=round(r.seconds, 2),
            ),
            acao=action,
        )

    def note_from_chat(s: AppState, suggestion: Suggestion, message: str, request: Request,
                       saving_workspace: Callable[[], Workspace], may_save: bool) -> tuple[str, ActionOut]:
        draft = draft_out(suggestion)
        if suggestion.missing:
            return (f"Para anotar \"{suggestion.titulo}\", falta {suggestion.missing_text()}.",
                    ActionOut(tipo="completar", rascunho=draft))
        try:
            event = event_draft(suggestion)
        except ValidationError:
            return (f"Para anotar \"{suggestion.titulo}\", confira os campos no formulário.",
                    ActionOut(tipo="completar", rascunho=draft))
        # Saved straight away only when the person clearly ordered it, and within the write limit.
        if may_save and explicit_request(message) and write_limiter.allow(client_id(request)):
            saved = add_to(s, saving_workspace(), event)
            where = f" ({saved.local})" if saved.local else ""
            return (f"Anotei na agenda: {saved.titulo}, {saved.quando}{where}.",
                    ActionOut(tipo="anotado", rascunho=draft, evento=saved))
        return (f"Quer que eu anote \"{suggestion.titulo}\" na agenda?", ActionOut(tipo="confirmar", rascunho=draft))

    def interpret_in(s: AppState, ws: Workspace, body: SentenceIn, request: Request) -> SuggestionOut:
        if s.llm_model is None:
            raise HTTPException(503, "O modelo de linguagem não está configurado: preencha o formulário à mão.")
        if not limiter.allow(client_id(request)):  # same quota as questions: each sentence is one model call
            raise HTTPException(429, "Limite de uso do modelo atingido. Tente de novo em alguns minutos.")
        model = ws.assistant.model
        with s.lock:
            before = model.usage.total
            try:
                suggestion = draft_from_text(model, body.frase.strip(), datetime.now(TZ))
            except NoEventError as error:
                raise HTTPException(422, str(error))
            except LLMError:
                raise HTTPException(502, "Não consegui ler a frase agora. Preencha o formulário ou tente de novo.")
            tokens = model.usage.total - before
        return SuggestionOut(**draft_out(suggestion).model_dump(), tokens=tokens)

    async def transcribe_in(s: AppState, request: Request) -> TranscriptOut:
        if isinstance(s.transcriber, UnavailableTranscriber):
            raise HTTPException(503, "A transcrição de áudio não está configurada: escreva a mensagem.")
        kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
        extension = AUDIO_TYPES.get(kind)
        if extension is None:
            raise HTTPException(415, "Formato de áudio não suportado.")
        if int(request.headers.get("content-length") or 0) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Áudio longo demais: grave até um minuto.")
        if not audio_limiter.allow(client_id(request)):
            raise HTTPException(429, "Muitos áudios seguidos. Tente de novo em alguns minutos.")
        audio = await request.body()
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, "Áudio longo demais: grave até um minuto.")
        if len(audio) < MIN_AUDIO_BYTES:
            raise HTTPException(422, "Áudio curto demais.")
        try:
            # The audio only lives in this request: sent to the transcriber, never stored.
            text = await run_in_threadpool(s.transcriber.transcribe, audio, f"mensagem.{extension}")
        except LLMError:
            raise HTTPException(502, "Não consegui transcrever o áudio agora. Tente de novo ou escreva.")
        if not text:
            raise HTTPException(422, "Não entendi o áudio. Fale mais perto do microfone e tente de novo.")
        return TranscriptOut(texto=text[:MAX_QUESTION_CHARS])

    def add_to(s: AppState, ws: Workspace, draft: EventDraft) -> EventOut:
        event = draft.to_event()
        with s.lock:
            if ws.store.count() >= MAX_EVENTS_PER_AGENDA:
                raise HTTPException(409, "A agenda atingiu o limite de compromissos.")
            vector = ws.assistant.embedder.passages([event.search_text()])
            added = ws.store.add([event], vector, CREATED)
        if not added:
            raise HTTPException(409, "Esse compromisso já existe.")
        return event_out(added[0])

    def update_in(s: AppState, ws: Workspace, event_id: int, draft: EventDraft) -> EventOut:
        missing = HTTPException(404, "Compromisso não encontrado, ou ele faz parte da agenda original.")
        with s.lock:
            current = ws.store.get_events([event_id]).get(event_id)
            if current is None or current.origin == BASE:
                raise missing
            event = replace(draft.to_event(), uid=current.event.uid)  # same identity, new fields
            vector = ws.assistant.embedder.passages([event.search_text()])[0]
            updated = ws.store.update(event_id, event, vector)
        if updated is None:
            raise missing
        return event_out(updated)

    def delete_from(s: AppState, ws: Workspace, event_id: int) -> dict:
        with s.lock:
            if not ws.store.delete(event_id):
                raise HTTPException(404, "Compromisso não encontrado, ou ele faz parte da agenda original.")
        return {"apagado": event_id}

    # ----- session -----

    @app.get("/api/config", response_model=ConfigOut)
    def get_config() -> ConfigOut:
        return ConfigOut(login=auth.enabled, google_client_id=auth.client_id if auth.enabled else None)

    @app.get("/api/me", response_model=MeOut)
    def get_me(request: Request) -> MeOut:
        user = current_user(request)
        return MeOut(logado=True, nome=user.name, email=user.email) if user else MeOut(logado=False)

    @app.post("/api/login", response_model=MeOut, dependencies=[Depends(same_site_only)])
    def login(body: LoginIn, request: Request, response: Response) -> MeOut:
        if not login_limiter.allow(client_id(request)):
            raise HTTPException(429, "Muitas tentativas de login. Tente de novo em alguns minutos.")
        try:
            user = auth.login(body.credential)
        except AuthError as error:
            raise HTTPException(error.status, error.message)
        save_session(response, auth.with_user(session_of(request), user))  # keeps the visitor's demo copy
        return MeOut(logado=True, nome=user.name, email=user.email)

    @app.post("/api/logout", response_model=MeOut, dependencies=[Depends(same_site_only)])
    def logout(request: Request, response: Response) -> MeOut:
        session = session_of(request)
        session.pop("user", None)
        save_session(response, session)
        return MeOut(logado=False)

    # ----- demo workspace (open; additions go to the visitor's temporary copy) -----

    def demo_workspace(request: Request, response: Response | None = None, create: bool = False) -> Workspace:
        s = state(request)
        session = session_of(request)
        with s.lock:
            ws, token = s.demo_for(session.get("demo"), create)
        if response is not None and token and token != session.get("demo"):
            save_session(response, {**session, "demo": token})
        return ws

    @app.get("/api/demo/status", response_model=StatusOut)
    def demo_status(request: Request) -> StatusOut:
        return status_of(state(request), demo_workspace(request))

    @app.get("/api/demo/agenda", response_model=list[EventOut])
    def demo_agenda(request: Request) -> list[EventOut]:
        return agenda_of(state(request), demo_workspace(request))

    @app.post("/api/demo/perguntar", response_model=AnswerOut, dependencies=[Depends(same_site_only)])
    def demo_ask(body: QuestionIn, request: Request, response: Response) -> AnswerOut:
        return answer_in(state(request), demo_workspace(request), body, request,
                         lambda: demo_workspace(request, response, create=True))

    @app.post("/api/demo/interpretar", response_model=SuggestionOut, dependencies=[Depends(same_site_only)])
    def demo_interpret(body: SentenceIn, request: Request) -> SuggestionOut:
        return interpret_in(state(request), demo_workspace(request), body, request)

    @app.post("/api/demo/transcrever", response_model=TranscriptOut, dependencies=[Depends(same_site_only)])
    async def demo_transcribe(request: Request) -> TranscriptOut:
        return await transcribe_in(state(request), request)

    @app.post("/api/demo/compromissos", response_model=EventOut, status_code=201,
              dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def demo_add(draft: EventDraft, request: Request, response: Response) -> EventOut:
        return add_to(state(request), demo_workspace(request, response, create=True), draft)

    @app.put("/api/demo/compromissos/{event_id}", response_model=EventOut,
             dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def demo_update(event_id: int, draft: EventDraft, request: Request) -> EventOut:
        return update_in(state(request), demo_workspace(request), event_id, draft)

    @app.delete("/api/demo/compromissos/{event_id}", dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def demo_delete(event_id: int, request: Request) -> dict:
        return delete_from(state(request), demo_workspace(request), event_id)

    # ----- private workspace (signed in) -----

    def private_workspace(request: Request, user: User) -> Workspace:
        s = state(request)
        with s.lock:
            return s.private_for(user)

    @app.get("/api/minha/status", response_model=StatusOut)
    def own_status(request: Request, user: User = Depends(require_user)) -> StatusOut:
        return status_of(state(request), private_workspace(request, user))

    @app.get("/api/minha/agenda", response_model=list[EventOut])
    def own_agenda(request: Request, user: User = Depends(require_user)) -> list[EventOut]:
        return agenda_of(state(request), private_workspace(request, user))

    @app.post("/api/minha/perguntar", response_model=AnswerOut, dependencies=[Depends(same_site_only)])
    def own_ask(body: QuestionIn, request: Request, user: User = Depends(require_user)) -> AnswerOut:
        ws = private_workspace(request, user)
        return answer_in(state(request), ws, body, request, lambda: ws)

    @app.post("/api/minha/interpretar", response_model=SuggestionOut, dependencies=[Depends(same_site_only)])
    def own_interpret(body: SentenceIn, request: Request, user: User = Depends(require_user)) -> SuggestionOut:
        return interpret_in(state(request), private_workspace(request, user), body, request)

    @app.post("/api/minha/transcrever", response_model=TranscriptOut, dependencies=[Depends(same_site_only)])
    async def own_transcribe(request: Request, user: User = Depends(require_user)) -> TranscriptOut:
        return await transcribe_in(state(request), request)

    @app.post("/api/minha/compromissos", response_model=EventOut, status_code=201,
              dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def own_add(draft: EventDraft, request: Request, user: User = Depends(require_user)) -> EventOut:
        return add_to(state(request), private_workspace(request, user), draft)

    @app.put("/api/minha/compromissos/{event_id}", response_model=EventOut,
             dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def own_update(event_id: int, draft: EventDraft, request: Request, user: User = Depends(require_user)) -> EventOut:
        return update_in(state(request), private_workspace(request, user), event_id, draft)

    @app.delete("/api/minha/compromissos/{event_id}", dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def own_delete(event_id: int, request: Request, user: User = Depends(require_user)) -> dict:
        return delete_from(state(request), private_workspace(request, user), event_id)

    @app.post("/api/minha/importar", response_model=ImportOut,
              dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def own_import(body: ImportIn, request: Request, user: User = Depends(require_user)) -> ImportOut:
        s = state(request)
        ws = private_workspace(request, user)
        try:
            events = import_events(body.conteudo, ws.first_day, ws.last_day)
        except ImportError_ as error:
            raise HTTPException(422, str(error))
        with s.lock:
            if ws.store.count() + len(events) > MAX_EVENTS_PER_AGENDA:
                raise HTTPException(409, "A importação passaria do limite de compromissos da agenda.")
            vectors = ws.assistant.embedder.passages([e.search_text() for e in events]) if events else []
            added = ws.store.add(events, vectors, IMPORTED) if events else []
        return ImportOut(encontrados=len(events), importados=len(added))

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
