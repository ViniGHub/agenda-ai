"""HTTP layer (FastAPI). It only translates HTTP <-> Assistant and the stores; the RAG logic lives elsewhere.

    uvicorn agenda_ia.api:app --port 8000           # API (and the built React app, if web/dist exists)

Two workspaces share the same question pipeline:
  /api/demo/...   the fictitious agenda, open to anyone. Events a visitor adds go to a private copy
                  of the demo, kept in memory for a couple of hours and tied to the session cookie.
  /api/minha/...  the signed-in person's own agenda (Google sign-in + allowlist, see api/auth.py).
                  Stored in Postgres when DATABASE_URL is set (Neon), otherwise in memory.

Both have .../interpretar: one sentence in, the event form filled in out. It saves nothing; the
person checks the form and sends it to .../compromissos like any other new event.

The chat (.../perguntar) also takes new appointments. An explicit order with a day and a time
("marca dentista quinta às 15h") is saved at once and the reply offers an undo; a message that only
describes an appointment ("dentista quinta às 15h") comes back as a question; a missing day or
time comes back as a form to complete. AnswerOut.acao says which one happened.

Voice messages: .../transcrever takes the recorded audio (raw body, Content-Type audio/*) and
returns the text; the page then sends that text to .../perguntar like a typed message.

Google Calendar and Gmail (private workspace only, see google/link.py and google/sync.py):
  /api/minha/google/conectar   goes to Google's consent page and back (same return address as the login)
  /api/minha/google/...        status, sync, the 8 o'clock e-mail on/off, send it now, disconnect
  /api/tarefas/resumo-diario   called by Cloud Scheduler at 08:00 with a Google-signed OIDC token
Once connected, writes in the private agenda go to Google Calendar first.

Start-up: the server answers from its first second. Loading the embedding model and indexing the
demo (30 to 60 s when Cloud Run wakes the container) runs in a background thread; /api/config,
/api/me and the login do not need it, the other routes wait for it, and /api/pronto tells the page
which step is running (see Startup).

Locally, AGENDA_ICS=agenda_real/minha.ics loads your real .ics into an in-memory private workspace.
The response models below are the contract with the front-end; web/src/api/types.ts mirrors them.
"""

from __future__ import annotations

import inspect
import logging
import mimetypes
import os
import secrets
import tempfile
import threading
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from datetime import time as dt_time  # the module name "time" is taken by the clock below
from pathlib import Path
from typing import Callable, Literal

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from ..chat.assistant import Assistant
from .auth import COOKIE_NAME, CSRF_HEADER, CSRF_VALUE, REDIRECT_PATH, SESSION_SECONDS, AuthError, AuthSettings, User
from ..events.calendar_loader import format_when, load_events
from ..config import ROOT, TZ
from ..google.daily_email import SEND_HOUR, compose, message, todays_events
from ..chat.drafting import NoEventError, Suggestion, draft_from_text, explicit_request
from ..events.editing import MAX_IMPORT_CHARS, EventDraft, ImportError_, import_events
from ..google.link import (CALENDAR_SCOPE, CONNECT_SCOPES, GMAIL_SEND_SCOPE, Connection, GoogleAccessLost,
                          GoogleApi, GoogleError, GoogleService)
from ..google.sync import push_new, push_update, sync
from ..ai.llm import LLMError, UnavailableModel
from ..ai.speech import AUDIO_TYPES, Transcriber, UnavailableTranscriber
from ..storage.store import BASE, CREATED, IMPORTED, SqliteEventStore, StoredEvent

log = logging.getLogger(__name__)

WEB_DIST = ROOT / "web" / "dist"
mimetypes.add_type("application/manifest+json", ".webmanifest")  # the installable app's manifest
MAX_QUESTION_CHARS = 300
DEMO_SESSION_SECONDS = 2 * 60 * 60
DEMO_MAX_SESSIONS = 200
MAX_EVENTS_PER_AGENDA = 3_000
MAX_AUDIO_BYTES = 2_000_000  # a minute of speech is ~250 KB in Opus and ~1 MB in AAC
MIN_AUDIO_BYTES = 500
TASKS_AUDIENCE = "agenda-ia-tarefas"  # the audience Cloud Scheduler puts in its OIDC token (deploy/agendador.sh)


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
    origem: str  # "base" (part of the agenda), "cadastro" (added by the person), "importado" or "google"
    google: bool = False  # also in the person's Google Calendar


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


class ReadyOut(BaseModel):
    pronto: bool
    etapa: str  # servidor | modelo | exemplo | banco | pronto | falhou (see Startup)
    segundos: float  # since this server instance started loading


class ConfigOut(BaseModel):
    login: bool
    google_client_id: str | None
    login_redirect: bool = False  # sign in by going to Google and back (needed in the iPhone app)


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


class GoogleOut(BaseModel):
    disponivel: bool  # the server can connect Google accounts
    conectado: bool
    email: str | None = None
    agenda: bool = False  # allowed to read and write Google Calendar
    gmail: bool = False  # allowed to send e-mail
    resumo_diario: bool = False  # the 8 o'clock e-mail is on
    resumo_hora: str = SEND_HOUR
    sincronizado_em: datetime | None = None


class GoogleSettingsIn(BaseModel):
    resumo_diario: bool


class SyncOut(BaseModel):
    novos: int
    atualizados: int
    apagados: int
    sincronizado_em: datetime


class SummaryOut(BaseModel):
    enviado_para: str
    compromissos: int


class TaskOut(BaseModel):
    enviados: int
    falhas: int


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
        google=stored.google_id is not None,
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
    google: GoogleService | None = None  # None: the server has no OAuth client secret

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


def load_state(progress: Callable[[str], None] = lambda stage: None) -> AppState:
    """Loads the model once, indexes the demo agenda in memory and connects to the database if configured.
    progress(stage) reports each step to the page while the server wakes up."""
    from ..ai.embeddings import E5Embedder
    from ..ai.llm import GroqModel
    from ..ai.speech import GroqTranscriber
    from ..events.sample import build as build_sample

    today = datetime.now(TZ).date()
    first_day, last_day = today - timedelta(days=60), today + timedelta(days=120)
    progress("modelo")
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

    progress("exemplo")
    sample_path = Path(tempfile.gettempdir()) / "agenda_ia_exemplo.ics"
    sample_path.write_bytes(build_sample(today).to_ical())
    demo = memory_workspace(sample_path, "exemplo")

    db = None
    if os.getenv("DATABASE_URL"):
        from ..storage.postgres import PostgresDatabase

        progress("banco")
        db = PostgresDatabase(os.environ["DATABASE_URL"], embedder.dim)

        def new_private(user: User) -> Workspace:
            store = db.for_owner(user.sub or user.email)
            return Workspace(Assistant(store, embedder, model), "privada", first_day, last_day, persistent=True)
    else:
        # Without a database the private agenda lives in memory. Locally, AGENDA_ICS fills it with your .ics.
        own_ics = Path(os.environ["AGENDA_ICS"]) if os.getenv("AGENDA_ICS") else None

        def new_private(user: User) -> Workspace:
            return memory_workspace(own_ics, "arquivo" if own_ics else "privada")

    return AppState(demo, llm_model, new_private, transcriber=transcriber, google=google_service(db))


def google_service(db) -> GoogleService | None:
    """Connections in Postgres (tokens encrypted) when there is a database, otherwise in memory."""
    from ..google.link import MemoryConnections, RestGoogle, TokenCipher

    client_id, client_secret = os.getenv("GOOGLE_CLIENT_ID"), os.getenv("GOOGLE_CLIENT_SECRET")
    if not (client_id and client_secret):
        return None
    secret = os.getenv("AGENDA_IA_SESSAO_SEGREDO")
    if db is not None and secret:
        from ..storage.postgres import PostgresConnections

        connections = PostgresConnections(db, TokenCipher(secret))
    else:
        connections = MemoryConnections()
    return GoogleService(connections, lambda token: RestGoogle(token, client_id, client_secret))


STAGES = ("servidor", "modelo", "exemplo", "banco", "pronto", "falhou")
READY_WAIT = 50.0  # seconds a request waits for the start-up load (Firebase Hosting gives up at 60)
TASK_READY_WAIT = 100.0  # the 8 o'clock task calls Cloud Run directly (timeout 120 s) and usually wakes it up
STAGE_WAIT = 20.0  # longest /api/pronto holds a request waiting for the next step


class Startup:
    """Loads the app state in a background thread, so the server answers from its first second.

    Cloud Run starts the container when a request arrives and nothing is running. Loading the
    embedding model and indexing the demo takes 30 to 60 seconds; meanwhile the cover, /api/me and
    the login answer at once, and routes that need the state wait for it (up to READY_WAIT).

    /api/pronto is a long poll: it answers when the step changes. The page shows each step, and an
    open request is also what keeps Cloud Run giving CPU to the instance while it loads (between
    requests it is throttled to almost nothing)."""

    def __init__(self) -> None:
        self.stage = "servidor"
        self.state: AppState | None = None
        self.error: str | None = None
        self.started = time.monotonic()
        self._changed = threading.Condition()

    def set_stage(self, stage: str) -> None:
        with self._changed:
            self.stage = stage
            self._changed.notify_all()

    def run(self, load: Callable) -> None:
        try:
            state = load(self.set_stage) if inspect.signature(load).parameters else load()
        except Exception as error:  # the server stays up and says so, instead of a revision that never starts
            log.exception("a carga inicial falhou")
            self.error = error.__class__.__name__
            self.set_stage("falhou")
            return
        self.state = state
        self.set_stage("pronto")

    def wait_change(self, known: str, timeout: float) -> None:
        with self._changed:
            self._changed.wait_for(lambda: self.stage != known, timeout=timeout)

    def get(self, timeout: float | None = None) -> AppState:
        with self._changed:
            self._changed.wait_for(lambda: self.stage in ("pronto", "falhou"),
                                   timeout=READY_WAIT if timeout is None else timeout)
        if self.state is not None:
            return self.state
        if self.error is not None:
            raise HTTPException(503, "O servidor não conseguiu iniciar. Tente de novo em alguns minutos.")
        raise HTTPException(503, "O servidor ainda está acordando. Tente de novo em instantes.")


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
    load: Callable[..., AppState] = load_state,
    limiter: RateLimiter | None = None,
    auth: AuthSettings | None = None,
) -> FastAPI:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")  # local development only; in production the variables come from Cloud Run
    limiter = limiter or RateLimiter(int(os.getenv("AGENDA_IA_LIMITE", "20")), 600)
    write_limiter = RateLimiter(int(os.getenv("AGENDA_IA_LIMITE_ESCRITA", "30")), 600)
    login_limiter = RateLimiter(10, 600)
    audio_limiter = RateLimiter(int(os.getenv("AGENDA_IA_LIMITE_AUDIO", "15")), 600)
    sync_limiter = RateLimiter(10, 600)  # per account
    summary_limiter = RateLimiter(5, 600)  # "send it now", per account
    auth = auth or AuthSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Loads in the background: uvicorn starts answering at once (see Startup).
        app.state.startup = Startup()
        threading.Thread(target=app.state.startup.run, args=(load,), name="carga-inicial", daemon=True).start()
        yield

    app = FastAPI(title="agenda-ia", version="0.6.0", lifespan=lifespan)

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
        """The loaded state; waits for the start-up load (sync routes only: it blocks the thread)."""
        return request.app.state.startup.get()

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

    def owner_of(user: User) -> str:
        return user.sub or user.email  # the same key as the private agenda

    # ----- Google -----

    def google_calendar(s: AppState, owner: str | None) -> GoogleApi | None:
        """Where a write must go first: the person's Google Calendar, when connected with calendar access."""
        return s.google.calendar_for(owner) if s.google is not None and owner else None

    @contextmanager
    def google_errors(s: AppState, owner: str):
        try:
            yield
        except GoogleAccessLost:
            if s.google is not None:
                s.google.forget(owner)  # useless now; the page offers to connect again
            raise HTTPException(409, "O acesso ao Google Agenda foi desfeito. Conecte de novo.")
        except GoogleError as error:
            if error.status == 403:
                raise HTTPException(403, "O Google Agenda recusou essa alteração. Se o compromisso é de outra pessoa, altere por lá.")
            raise HTTPException(502, "Não consegui falar com o Google Agenda agora. Tente de novo.")

    def run_sync(s: AppState, ws: Workspace, connection: Connection):
        api = s.google.api(connection)
        with s.lock, google_errors(s, connection.owner):
            report = sync(ws.store, ws.assistant.embedder, api, ws.first_day, ws.last_day)
        now = datetime.now(TZ)
        s.google.update(connection.owner, synced_at=now)
        return report, now

    def send_summary(s: AppState, connection: Connection, day: date, mark: bool) -> int:
        """Today's appointments to the account's own inbox. Returns how many there were."""
        with s.lock:
            ws = s.private_for(User(email=connection.email, name="", sub=connection.owner))
        if connection.calendar:
            try:
                run_sync(s, ws, connection)
            except HTTPException as error:
                if error.status_code == 409:
                    raise  # access lost: the e-mail cannot go either
                log.warning("resumo: sincronização falhou (%s); usando a cópia local", error.detail)
        with s.lock:
            events = todays_events(ws.store, day)
        subject, text, page = compose(events, day, auth.base_url)
        with google_errors(s, connection.owner):
            s.google.api(connection).send_mail(message(connection.email, subject, text, page))
        if mark:
            s.google.update(connection.owner, last_daily=day)
        return len(events)

    def google_out(s: AppState, user: User) -> GoogleOut:
        if s.google is None or not auth.redirect_enabled:
            return GoogleOut(disponivel=False, conectado=False)
        c = s.google.connection(owner_of(user))
        if c is None:
            return GoogleOut(disponivel=True, conectado=False)
        return GoogleOut(disponivel=True, conectado=True, email=c.email, agenda=c.calendar, gmail=c.mail,
                         resumo_diario=c.daily and c.mail, sincronizado_em=c.synced_at)

    def connected(s: AppState, user: User) -> Connection:
        if s.google is None:
            raise HTTPException(503, "A conexão com o Google não está configurada neste servidor.")
        connection = s.google.connection(owner_of(user))
        if connection is None:
            raise HTTPException(409, "Conecte o Google Agenda primeiro.")
        return connection

    def google_connected(request: Request, session: dict, code: str, state_: str, error: str) -> RedirectResponse:
        """The way back from Google's consent page (purpose "agenda")."""
        s = state(request)
        user = auth.user_in(session)
        if user is None:  # signed out in the meantime
            session.pop("oauth", None)
            response = RedirectResponse("/", status_code=302)
            save_session(response, session)
            return response
        if error:
            outcome = "cancelado"
        elif s.google is None:
            outcome = "indisponivel"
        else:
            try:
                claims, tokens = auth.finish_oauth(session, code, state_)
            except AuthError:
                outcome = "falhou"
            else:
                granted = frozenset(str(tokens.get("scope", "")).split()) & {CALENDAR_SCOPE, GMAIL_SEND_SCOPE}
                refresh_token = str(tokens.get("refresh_token") or "")
                if str(claims.get("sub", "")) != user.sub:
                    outcome = "outra-conta"  # the agenda belongs to the account that signed in
                elif not refresh_token:
                    outcome = "falhou"
                elif not granted:
                    outcome = "sem-permissao"
                else:
                    s.google.connect(owner_of(user), str(claims.get("email") or user.email).lower(), refresh_token, granted)
                    outcome = "conectado"
        session.pop("oauth", None)
        response = RedirectResponse(f"/?google={outcome}#minha", status_code=302)
        save_session(response, session)
        return response

    def check_scheduler(request: Request) -> None:
        """Only Cloud Scheduler may run tasks: a Google-signed OIDC token for our service account."""
        expected = os.getenv("AGENDA_IA_AGENDADOR", "").strip().lower()
        if not expected:
            raise HTTPException(503, "As tarefas agendadas não estão configuradas.")
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(401, "Falta o token.")
        try:
            claims = auth.verifier(header[7:].strip(), TASKS_AUDIENCE)
        except ValueError:
            raise HTTPException(401, "Token inválido.")
        if str(claims.get("email", "")).lower() != expected or not claims.get("email_verified"):
            raise HTTPException(403, "Quem chamou não é o agendador.")

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
                  saving_workspace: Callable[[], Workspace], owner: str | None = None) -> AnswerOut:
        """saving_workspace: where a new appointment goes (the demo makes the visitor's copy only then).
        owner: set in the private agenda, whose writes may go to Google Calendar."""
        if not limiter.allow(client_id(request)):
            raise HTTPException(429, "Limite de perguntas atingido. Tente de novo em alguns minutos.")
        question = body.pergunta.strip()
        with s.lock:  # one question at a time: shared SQLite connections and a per-minute token quota
            r = ws.assistant.ask(question, mode=body.modo)
        text, action = r.answer.text, None
        if r.suggestion is not None:
            text, action = note_from_chat(s, r.suggestion, question, request, saving_workspace,
                                          may_save=body.modo == "auto", owner=owner)
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
                       saving_workspace: Callable[[], Workspace], may_save: bool,
                       owner: str | None = None) -> tuple[str, ActionOut]:
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
            saved = add_to(s, saving_workspace(), event, owner)
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

    def add_to(s: AppState, ws: Workspace, draft: EventDraft, owner: str | None = None) -> EventOut:
        event = draft.to_event()
        api = google_calendar(s, owner)
        with s.lock:
            if ws.store.count() >= MAX_EVENTS_PER_AGENDA:
                raise HTTPException(409, "A agenda atingiu o limite de compromissos.")
            if api is not None and owner:
                with google_errors(s, owner):
                    added = push_new(ws.store, ws.assistant.embedder, api, event, CREATED)
            else:
                vector = ws.assistant.embedder.passages([event.search_text()])
                added = ws.store.add([event], vector, CREATED)
        if not added:
            raise HTTPException(409, "Esse compromisso já existe.")
        return event_out(added[0])

    def update_in(s: AppState, ws: Workspace, event_id: int, draft: EventDraft, owner: str | None = None) -> EventOut:
        missing = HTTPException(404, "Compromisso não encontrado, ou ele faz parte da agenda original.")
        with s.lock:
            current = ws.store.get_events([event_id]).get(event_id)
            if current is None or current.origin == BASE:
                raise missing
            event = replace(draft.to_event(), uid=current.event.uid)  # same identity, new fields
            vector = ws.assistant.embedder.passages([event.search_text()])[0]
            api = google_calendar(s, owner) if current.google_id else None
            if api is not None and owner:
                with google_errors(s, owner):
                    updated = push_update(ws.store, api, current, event, vector)
            else:
                updated = ws.store.update(event_id, event, vector)
        if updated is None:
            raise missing
        return event_out(updated)

    def delete_from(s: AppState, ws: Workspace, event_id: int, owner: str | None = None) -> dict:
        with s.lock:
            current = ws.store.get_events([event_id]).get(event_id)
            api = google_calendar(s, owner) if current is not None and current.google_id else None
            if api is not None and owner and current is not None and current.origin != BASE:
                with google_errors(s, owner):
                    api.delete_event(str(current.google_id))
            if not ws.store.delete(event_id):
                raise HTTPException(404, "Compromisso não encontrado, ou ele faz parte da agenda original.")
        return {"apagado": event_id}

    # ----- start-up and session (these answer before the state is loaded) -----

    @app.get("/api/pronto", response_model=ReadyOut)
    def ready(request: Request, etapa: str = "") -> ReadyOut:
        """etapa: the step the page already knows; the answer waits (up to STAGE_WAIT) for the next one."""
        startup: Startup = request.app.state.startup
        if etapa in STAGES and etapa not in ("pronto", "falhou"):
            startup.wait_change(etapa, STAGE_WAIT)
        return ReadyOut(pronto=startup.stage == "pronto", etapa=startup.stage,
                        segundos=round(time.monotonic() - startup.started, 1))

    @app.get("/api/config", response_model=ConfigOut)
    def get_config() -> ConfigOut:
        return ConfigOut(login=auth.enabled, google_client_id=auth.client_id if auth.enabled else None,
                         login_redirect=auth.redirect_enabled)

    # Redirect sign-in. Errors come back to the cover as ?erro_login=..., which the page explains.
    @app.get("/api/login/google")
    def google_start(request: Request) -> RedirectResponse:
        if not login_limiter.allow(client_id(request)):
            return RedirectResponse("/?erro_login=limite", status_code=302)
        try:
            url, session = auth.start_redirect(session_of(request))
        except AuthError:
            return RedirectResponse("/?erro_login=indisponivel", status_code=302)
        response = RedirectResponse(url, status_code=302)
        save_session(response, session)
        return response

    @app.get(REDIRECT_PATH)
    def google_return(request: Request, code: str = "", state: str = "", error: str = "") -> RedirectResponse:
        session = session_of(request)
        if auth.purpose_of(session) == "agenda":
            return google_connected(request, session, code, state, error)
        if error:  # the person closed or refused Google's page
            reason = "cancelado"
        else:
            try:
                user = auth.finish_redirect(session, code, state)
            except AuthError as failure:
                reason = "sem-acesso" if failure.status == 403 else "falhou"
            else:
                session.pop("oauth", None)
                response = RedirectResponse("/#minha", status_code=302)
                save_session(response, auth.with_user(session, user))
                return response
        session.pop("oauth", None)
        response = RedirectResponse(f"/?erro_login={reason}", status_code=302)
        save_session(response, session)
        return response

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
        return await transcribe_in(await run_in_threadpool(state, request), request)

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
        return answer_in(state(request), ws, body, request, lambda: ws, owner_of(user))

    @app.post("/api/minha/interpretar", response_model=SuggestionOut, dependencies=[Depends(same_site_only)])
    def own_interpret(body: SentenceIn, request: Request, user: User = Depends(require_user)) -> SuggestionOut:
        return interpret_in(state(request), private_workspace(request, user), body, request)

    @app.post("/api/minha/transcrever", response_model=TranscriptOut, dependencies=[Depends(same_site_only)])
    async def own_transcribe(request: Request, user: User = Depends(require_user)) -> TranscriptOut:
        return await transcribe_in(await run_in_threadpool(state, request), request)

    @app.post("/api/minha/compromissos", response_model=EventOut, status_code=201,
              dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def own_add(draft: EventDraft, request: Request, user: User = Depends(require_user)) -> EventOut:
        return add_to(state(request), private_workspace(request, user), draft, owner_of(user))

    @app.put("/api/minha/compromissos/{event_id}", response_model=EventOut,
             dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def own_update(event_id: int, draft: EventDraft, request: Request, user: User = Depends(require_user)) -> EventOut:
        return update_in(state(request), private_workspace(request, user), event_id, draft, owner_of(user))

    @app.delete("/api/minha/compromissos/{event_id}", dependencies=[Depends(same_site_only), Depends(limit_writes)])
    def own_delete(event_id: int, request: Request, user: User = Depends(require_user)) -> dict:
        return delete_from(state(request), private_workspace(request, user), event_id, owner_of(user))

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

    # ----- Google Calendar and the 8 o'clock e-mail (private workspace) -----

    @app.get("/api/minha/google", response_model=GoogleOut)
    def google_status(request: Request, user: User = Depends(require_user)) -> GoogleOut:
        return google_out(state(request), user)

    @app.get("/api/minha/google/conectar")
    def google_connect(request: Request) -> RedirectResponse:
        """A link, not a fetch: the page goes to Google's consent page and comes back to REDIRECT_PATH."""
        user = current_user(request)
        if user is None:
            return RedirectResponse("/", status_code=302)
        if state(request).google is None or not auth.redirect_enabled:
            return RedirectResponse("/?google=indisponivel#minha", status_code=302)
        if not login_limiter.allow(client_id(request)):
            return RedirectResponse("/?google=limite#minha", status_code=302)
        url, session = auth.start_redirect(session_of(request), purpose="agenda", scopes=CONNECT_SCOPES,
                                           login_hint=user.email)
        response = RedirectResponse(url, status_code=302)
        save_session(response, session)
        return response

    @app.put("/api/minha/google", response_model=GoogleOut, dependencies=[Depends(same_site_only)])
    def google_settings(body: GoogleSettingsIn, request: Request, user: User = Depends(require_user)) -> GoogleOut:
        s = state(request)
        connected(s, user)
        s.google.update(owner_of(user), daily=body.resumo_diario)
        return google_out(s, user)

    @app.post("/api/minha/google/sincronizar", response_model=SyncOut, dependencies=[Depends(same_site_only)])
    def google_sync(request: Request, user: User = Depends(require_user)) -> SyncOut:
        s = state(request)
        connection = connected(s, user)
        if not connection.calendar:
            raise HTTPException(409, "A conexão não deu acesso à agenda. Conecte de novo e deixe a agenda marcada.")
        if not sync_limiter.allow(connection.owner):
            raise HTTPException(429, "Sincronizei há pouco. Tente de novo em alguns minutos.")
        report, when = run_sync(s, private_workspace(request, user), connection)
        return SyncOut(novos=report.added, atualizados=report.updated, apagados=report.removed, sincronizado_em=when)

    @app.post("/api/minha/google/resumo", response_model=SummaryOut, dependencies=[Depends(same_site_only)])
    def google_summary_now(request: Request, user: User = Depends(require_user)) -> SummaryOut:
        s = state(request)
        connection = connected(s, user)
        if not connection.mail:
            raise HTTPException(409, "A conexão não deu permissão para enviar e-mail. Conecte de novo e deixe o Gmail marcado.")
        if not summary_limiter.allow(connection.owner):
            raise HTTPException(429, "Muitos e-mails seguidos. Tente de novo em alguns minutos.")
        count = send_summary(s, connection, datetime.now(TZ).date(), mark=False)
        return SummaryOut(enviado_para=connection.email, compromissos=count)

    @app.delete("/api/minha/google", response_model=GoogleOut, dependencies=[Depends(same_site_only)])
    def google_disconnect(request: Request, user: User = Depends(require_user)) -> GoogleOut:
        """Events read from Google leave this agenda (they stay in Google); the token is revoked."""
        s = state(request)
        if s.google is not None:
            ws = private_workspace(request, user)
            with s.lock:
                ws.store.forget_google()
            s.google.disconnect(owner_of(user))
        return google_out(s, user)

    @app.post("/api/tarefas/resumo-diario", response_model=TaskOut)
    def daily_summary_task(request: Request) -> TaskOut:
        check_scheduler(request)
        # A cold start takes about a minute (measured: 55 s), more than READY_WAIT.
        s = request.app.state.startup.get(timeout=TASK_READY_WAIT)
        sent = failed = 0
        if s.google is not None:
            day = datetime.now(TZ).date()
            for connection in s.google.connections.daily():
                if not connection.mail or connection.last_daily == day:
                    continue  # no permission, or already sent today (the job retried)
                try:
                    send_summary(s, connection, day, mark=True)
                    sent += 1
                except HTTPException as error:
                    failed += 1
                    log.warning("resumo diário não enviado: %s", error.detail)
        return TaskOut(enviados=sent, falhas=failed)

    if WEB_DIST.is_dir():
        app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
    return app


app = create_app()
