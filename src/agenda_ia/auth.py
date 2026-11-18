"""Sign-in with Google, restricted to an allowlist, kept in a signed session cookie.

Two ways in, both ending in an ID token (a JWT signed by Google) that we verify here
(signature, audience = our client ID, issuer, expiry, verified e-mail) and check
against the allowlist. The token itself is never stored.

- Redirect (production): /api/login/google sends the browser to Google's sign-in page and
  Google sends it back to /api/login/google/retorno with a one-time code, which the server
  trades for the ID token (OAuth authorization code flow). Works inside the app installed on
  an iPhone, where the popup below cannot report back. A random "state" and "nonce", kept in
  the session cookie, tie the answer to the browser that asked for it.
- Popup (local development, no client secret): the official "Sign in with Google" button posts
  the ID token to /api/login.

The same redirect, with purpose "agenda", connects Google Calendar and Gmail (google_link.py):
more scopes, offline access (a refresh token) and the same return address, so the OAuth client
needs a single redirect URI.

One signed cookie, "__session", carries everything the server keeps per browser: the signed-in
person and the token of their private copy of the demo. One cookie, with that exact name, because
Firebase Hosting (the clean address in front of Cloud Run) strips every other cookie on the way in.
The login part keeps its own clock, so re-signing the cookie for the demo never extends a login.

Configuration (environment variables; none of them goes into the repository):
  GOOGLE_CLIENT_ID          OAuth client ID of the web app (public, shown to the browser)
  GOOGLE_CLIENT_SECRET      that client's secret; turns the redirect sign-in on
  AGENDA_IA_URL             public address, for the redirect URI (default http://localhost:8000)
  AGENDA_IA_PERMITIDOS      allowed e-mails, comma separated
  AGENDA_IA_SESSAO_SEGREDO  key that signs the session cookie
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlencode

from itsdangerous import BadSignature, URLSafeTimedSerializer

log = logging.getLogger(__name__)

COOKIE_NAME = "__session"  # the only cookie Firebase Hosting forwards to Cloud Run
SESSION_SECONDS = 30 * 24 * 60 * 60  # "remember this device": the installed app should not ask every day
OAUTH_SECONDS = 10 * 60  # a sign-in started longer ago than this is refused
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_PATH = "/api/login/google/retorno"
LOGIN_SCOPES = ("openid", "email", "profile")
CSRF_HEADER = "x-requested-with"  # a custom header: cross-site forms cannot send it without a CORS preflight
CSRF_VALUE = "agenda-ia"


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class User:
    email: str
    name: str
    sub: str  # Google's stable account id; the owner key for private data


# Takes the raw ID token and the expected audience, returns the verified claims or raises ValueError.
TokenVerifier = Callable[[str, str], dict]
# Trades the one-time code for tokens: (code, client_id, client_secret, redirect_uri) -> token response.
CodeExchanger = Callable[[str, str, str, str], dict]


def google_verifier(token: str, audience: str) -> dict:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    # Checks the signature against Google's public keys, the audience, the issuer and the expiry.
    return id_token.verify_oauth2_token(token, google_requests.Request(), audience)


def google_exchange(code: str, client_id: str, client_secret: str, redirect_uri: str) -> dict:
    import requests

    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={"code": code, "client_id": client_id, "client_secret": client_secret,
              "redirect_uri": redirect_uri, "grant_type": "authorization_code"},
        timeout=15,
    )
    if response.status_code != 200:
        raise ValueError(f"o Google recusou o código ({response.status_code})")
    return response.json()


@dataclass
class AuthSettings:
    client_id: str | None
    allowed: frozenset[str]
    secret: str
    verifier: TokenVerifier = field(default=google_verifier)
    client_secret: str | None = None
    base_url: str = "http://localhost:8000"
    exchanger: CodeExchanger = field(default=google_exchange)

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.allowed)

    @property
    def redirect_enabled(self) -> bool:
        return self.enabled and bool(self.client_secret)

    @property
    def redirect_uri(self) -> str:
        return self.base_url.rstrip("/") + REDIRECT_PATH

    @classmethod
    def from_env(cls) -> "AuthSettings":
        allowed = frozenset(e.strip().lower() for e in os.getenv("AGENDA_IA_PERMITIDOS", "").split(",") if e.strip())
        secret = os.getenv("AGENDA_IA_SESSAO_SEGREDO", "")
        if not secret:
            # Fine for local development: sessions just do not survive a restart.
            log.warning("AGENDA_IA_SESSAO_SEGREDO ausente: usando uma chave temporária")
            secret = secrets.token_urlsafe(48)
        return cls(
            os.getenv("GOOGLE_CLIENT_ID") or None, allowed, secret,
            client_secret=os.getenv("GOOGLE_CLIENT_SECRET") or None,
            base_url=os.getenv("AGENDA_IA_URL") or "http://localhost:8000",
        )

    def _serializer(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.secret, salt="agenda-ia-sessao")

    def login(self, credential: str) -> User:
        """Popup sign-in: the browser hands over the ID token."""
        if not self.enabled:
            raise AuthError(503, "O login não está configurado neste servidor.")
        try:
            claims = self.verifier(credential, self.client_id or "")
        except ValueError:
            raise AuthError(401, "Não consegui confirmar o login com o Google. Tente de novo.")
        return self._user_from(claims)

    def start_redirect(self, session: dict, purpose: str = "login", scopes: tuple[str, ...] = LOGIN_SCOPES,
                       login_hint: str | None = None) -> tuple[str, dict]:
        """Google's address for this attempt, and the session with its state, nonce and purpose.

        purpose "login": sign in. Anything else (google_link uses "agenda") asks for offline access,
        so Google returns a refresh token, and shows the consent page again so it always does."""
        if not self.redirect_enabled:
            raise AuthError(503, "O login não está configurado neste servidor.")
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        params = {
            "client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": " ".join(scopes), "state": state, "nonce": nonce,
        }
        if purpose == "login":
            params["prompt"] = "select_account"
        else:
            params.update(access_type="offline", prompt="consent", include_granted_scopes="true")
        if login_hint:
            params["login_hint"] = login_hint
        attempt = {"state": state, "nonce": nonce, "at": int(time.time()), "para": purpose}
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}", {**session, "oauth": attempt}

    @staticmethod
    def purpose_of(session: dict) -> str:
        attempt = session.get("oauth")
        return str(attempt.get("para", "login")) if isinstance(attempt, dict) else "login"

    def finish_redirect(self, session: dict, code: str, state: str) -> User:
        """Google sent the browser back from a sign-in: the person, if allowed."""
        claims, _ = self.finish_oauth(session, code, state)
        return self._user_from(claims)

    def finish_oauth(self, session: dict, code: str, state: str) -> tuple[dict, dict]:
        """Checks the answer belongs to the attempt this browser started, trades the code and verifies
        the ID token. Returns (verified claims, Google's token response)."""
        attempt = session.get("oauth")
        if not isinstance(attempt, dict) or not state or not code:
            raise AuthError(400, "O login veio incompleto. Tente de novo.")
        if not secrets.compare_digest(str(attempt.get("state", "")), state):
            raise AuthError(400, "Este login não foi começado neste navegador. Tente de novo.")
        if time.time() - float(attempt.get("at", 0)) > OAUTH_SECONDS:
            raise AuthError(400, "O login demorou demais. Tente de novo.")
        try:
            tokens = self.exchanger(code, self.client_id or "", self.client_secret or "", self.redirect_uri)
            claims = self.verifier(str(tokens.get("id_token", "")), self.client_id or "")
        except ValueError:
            raise AuthError(401, "Não consegui confirmar o login com o Google. Tente de novo.")
        if not secrets.compare_digest(str(claims.get("nonce", "")), str(attempt.get("nonce", ""))):
            raise AuthError(401, "Não consegui confirmar o login com o Google. Tente de novo.")
        return claims, tokens

    def _user_from(self, claims: dict) -> User:
        email = str(claims.get("email", "")).lower()
        if not email or not claims.get("email_verified"):
            raise AuthError(401, "A conta Google precisa ter o e-mail verificado.")
        if email not in self.allowed:
            raise AuthError(403, "Esta conta não tem acesso à área privada. Use o modo de teste com a agenda fictícia.")
        return User(email=email, name=str(claims.get("name") or email.split("@")[0]), sub=str(claims.get("sub", "")))

    # ----- the session cookie: {"user": {...}, "demo": "token"}, either part optional -----

    def read_session(self, cookie: str | None) -> dict:
        """The cookie's contents, or {} when it is missing, forged or older than a session."""
        if not cookie:
            return {}
        try:
            data = self._serializer().loads(cookie, max_age=SESSION_SECONDS)
        except BadSignature:  # also covers expired cookies
            return {}
        return data if isinstance(data, dict) else {}

    def session_cookie(self, session: dict) -> str:
        return self._serializer().dumps(session)

    def with_user(self, session: dict, user: User) -> dict:
        return {**session, "user": {"email": user.email, "name": user.name, "sub": user.sub, "at": int(time.time())}}

    def user_in(self, session: dict) -> User | None:
        data = session.get("user")
        if not isinstance(data, dict):
            return None
        try:
            signed_in_at = float(data.get("at", 0))
        except (TypeError, ValueError):
            return None
        if time.time() - signed_in_at > SESSION_SECONDS:
            return None
        email = str(data.get("email", "")).lower()
        if email not in self.allowed:  # removed from the allowlist since logging in
            return None
        return User(email=email, name=str(data.get("name", "")), sub=str(data.get("sub", "")))

    def cookie_for(self, user: User) -> str:
        return self.session_cookie(self.with_user({}, user))

    def user_from_cookie(self, cookie: str | None) -> User | None:
        return self.user_in(self.read_session(cookie))
