"""Sign-in with Google, restricted to an allowlist, kept in a signed session cookie.

Flow: the browser gets an ID token (a JWT signed by Google) from the official
"Sign in with Google" button and posts it to /api/login. We verify it here
(signature, audience = our client ID, issuer, expiry, verified e-mail), check
the e-mail against the allowlist and answer with an HttpOnly cookie. The token
itself is never stored.

Configuration (environment variables; none of them goes into the repository):
  GOOGLE_CLIENT_ID          OAuth client ID of the web app (public, shown to the browser)
  AGENDA_IA_PERMITIDOS      allowed e-mails, comma separated
  AGENDA_IA_SESSAO_SEGREDO  key that signs the session cookie
"""

from __future__ import annotations

import logging
import os
import secrets
from dataclasses import dataclass, field
from typing import Callable

from itsdangerous import BadSignature, URLSafeTimedSerializer

log = logging.getLogger(__name__)

COOKIE_NAME = "agenda_sessao"
SESSION_SECONDS = 8 * 60 * 60
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


def google_verifier(token: str, audience: str) -> dict:
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    # Checks the signature against Google's public keys, the audience, the issuer and the expiry.
    return id_token.verify_oauth2_token(token, google_requests.Request(), audience)


@dataclass
class AuthSettings:
    client_id: str | None
    allowed: frozenset[str]
    secret: str
    verifier: TokenVerifier = field(default=google_verifier)

    @property
    def enabled(self) -> bool:
        return bool(self.client_id and self.allowed)

    @classmethod
    def from_env(cls) -> "AuthSettings":
        allowed = frozenset(e.strip().lower() for e in os.getenv("AGENDA_IA_PERMITIDOS", "").split(",") if e.strip())
        secret = os.getenv("AGENDA_IA_SESSAO_SEGREDO", "")
        if not secret:
            # Fine for local development: sessions just do not survive a restart.
            log.warning("AGENDA_IA_SESSAO_SEGREDO ausente: usando uma chave temporária")
            secret = secrets.token_urlsafe(48)
        return cls(os.getenv("GOOGLE_CLIENT_ID") or None, allowed, secret)

    def _serializer(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.secret, salt="agenda-ia-sessao")

    def login(self, credential: str) -> User:
        if not self.enabled:
            raise AuthError(503, "O login não está configurado neste servidor.")
        try:
            claims = self.verifier(credential, self.client_id or "")
        except ValueError:
            raise AuthError(401, "Não consegui confirmar o login com o Google. Tente de novo.")
        email = str(claims.get("email", "")).lower()
        if not email or not claims.get("email_verified"):
            raise AuthError(401, "A conta Google precisa ter o e-mail verificado.")
        if email not in self.allowed:
            raise AuthError(403, "Esta conta não tem acesso à área privada. Use o modo de teste com a agenda fictícia.")
        return User(email=email, name=str(claims.get("name") or email.split("@")[0]), sub=str(claims.get("sub", "")))

    def cookie_for(self, user: User) -> str:
        return self._serializer().dumps({"email": user.email, "name": user.name, "sub": user.sub})

    def user_from_cookie(self, cookie: str | None) -> User | None:
        if not cookie:
            return None
        try:
            data = self._serializer().loads(cookie, max_age=SESSION_SECONDS)
        except BadSignature:  # also covers expired cookies
            return None
        email = str(data.get("email", "")).lower()
        if email not in self.allowed:  # removed from the allowlist since logging in
            return None
        return User(email=email, name=str(data.get("name", "")), sub=str(data.get("sub", "")))
