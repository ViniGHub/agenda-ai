"""Connecting a Google account: Google Calendar (read and write events) and Gmail (send only).

Separate from signing in. The login asks only for name and e-mail; connecting asks, once, for
  calendar.events  see and edit events (not the calendar's settings or sharing)
  gmail.send       send e-mail (it cannot read any)
Google's consent page lets the person untick either one, so each feature checks its own scope.

Google answers the consent with a refresh token, which lets the server act later without the
person, as the 8 o'clock e-mail needs. It is stored encrypted (Fernet: AES plus HMAC) with a key
derived from the session secret, so a copy of the database alone cannot be used against the
account. Disconnecting revokes it at Google and deletes it here.

The Google APIs are reached through plain HTTPS calls (requests): the official client library
would add tens of megabytes to the image for five endpoints.
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Callable, Protocol
from urllib.parse import quote

log = logging.getLogger(__name__)

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
CONNECT_SCOPES = ("openid", "email", CALENDAR_SCOPE, GMAIL_SEND_SCOPE)

TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
MAX_PAGES = 20  # 250 events per page: far more than a personal calendar holds in the agenda's window


class GoogleError(Exception):
    """A Google API call failed. `status` is the HTTP status (0 when Google could not be reached)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class GoogleAccessLost(GoogleError):
    """The refresh token stopped working (revoked in the Google account, or expired): connect again."""


@dataclass(frozen=True)
class Connection:
    owner: str  # same key as the private agenda (Google account id)
    email: str
    refresh_token: str
    scopes: frozenset[str]
    daily: bool = True  # the 8 o'clock e-mail
    last_daily: date | None = None  # the day it was last sent (a retried job does not send twice)
    synced_at: datetime | None = None

    @property
    def calendar(self) -> bool:
        return CALENDAR_SCOPE in self.scopes

    @property
    def mail(self) -> bool:
        return GMAIL_SEND_SCOPE in self.scopes


class ConnectionStore(Protocol):
    def get(self, owner: str) -> Connection | None: ...

    def save(self, connection: Connection) -> None:
        """Inserts or replaces the owner's connection."""
        ...

    def delete(self, owner: str) -> None: ...

    def daily(self) -> list[Connection]:
        """Connections with the morning e-mail turned on."""
        ...


class MemoryConnections:
    """ConnectionStore for tests and local runs without a database (gone on restart)."""

    def __init__(self) -> None:
        self._items: dict[str, Connection] = {}
        self._lock = threading.Lock()

    def get(self, owner):
        with self._lock:
            return self._items.get(owner)

    def save(self, connection):
        with self._lock:
            self._items[connection.owner] = connection

    def delete(self, owner):
        with self._lock:
            self._items.pop(owner, None)

    def daily(self):
        with self._lock:
            return [c for c in self._items.values() if c.daily]


class TokenCipher:
    """Encrypts refresh tokens at rest. The key is derived (HKDF) from the session secret, for this use only."""

    def __init__(self, secret: str):
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF

        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"agenda-ia/google-token").derive(secret.encode())
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    def encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str:
        from cryptography.fernet import InvalidToken

        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as error:  # another key (the session secret changed) or a tampered value
            raise ValueError("token ilegível") from error


class GoogleApi(Protocol):
    """The five calls the app makes on the person's behalf."""

    def list_events(self, time_min: datetime, time_max: datetime) -> list[dict]:
        """Every occurrence starting in the window (recurring events expanded), in time order."""
        ...

    def insert_event(self, body: dict) -> dict: ...

    def patch_event(self, event_id: str, body: dict) -> dict: ...

    def delete_event(self, event_id: str) -> None:
        """An event already gone counts as deleted."""
        ...

    def send_mail(self, message: bytes) -> None:
        """Sends a complete MIME message from the person's Gmail."""
        ...


class RestGoogle:
    """GoogleApi over HTTPS, for one account. Keeps the short-lived access token until it expires."""

    def __init__(self, refresh_token: str, client_id: str, client_secret: str, http=None):
        import requests

        self._refresh_token, self._client_id, self._client_secret = refresh_token, client_id, client_secret
        self._http = http or requests.Session()
        self._access: str | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def _token(self, renew: bool = False) -> str:
        with self._lock:
            if self._access and not renew and time.monotonic() < self._expires:
                return self._access
            try:
                response = self._http.post(TOKEN_URL, data={
                    "client_id": self._client_id, "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token, "grant_type": "refresh_token",
                }, timeout=15)
            except Exception as error:
                raise GoogleError(0, "Não consegui falar com o Google.") from error
            if response.status_code in (400, 401):  # invalid_grant: revoked or expired
                raise GoogleAccessLost(response.status_code, "O acesso ao Google foi desfeito. Conecte de novo.")
            if response.status_code != 200:
                raise GoogleError(response.status_code, "O Google não renovou o acesso agora.")
            data = response.json()
            self._access = str(data["access_token"])
            self._expires = time.monotonic() + max(0, int(data.get("expires_in", 3600)) - 60)
            return self._access

    def _call(self, method: str, url: str, *, params: dict | None = None, body: dict | None = None,
              missing_ok: bool = False) -> dict:
        for attempt in (1, 2):
            headers = {"Authorization": f"Bearer {self._token(renew=attempt == 2)}"}
            try:
                response = self._http.request(method, url, params=params, json=body, headers=headers, timeout=20)
            except Exception as error:
                raise GoogleError(0, "Não consegui falar com o Google.") from error
            if response.status_code == 401 and attempt == 1:
                continue  # the access token expired early: renew once
            break
        if missing_ok and response.status_code in (404, 410):
            return {}
        if response.status_code >= 400:
            try:
                reason = str(response.json()["error"]["message"])
            except Exception:
                reason = ""
            log.warning("Google %s %s: %s %s", method, url.split("?")[0], response.status_code, reason[:200])
            raise GoogleError(response.status_code, reason or f"erro {response.status_code}")
        return response.json() if response.content else {}

    def list_events(self, time_min, time_max):
        items: list[dict] = []
        params = {"timeMin": time_min.isoformat(), "timeMax": time_max.isoformat(), "singleEvents": "true",
                  "orderBy": "startTime", "maxResults": "250"}
        for _ in range(MAX_PAGES):
            page = self._call("GET", EVENTS_URL, params=params)
            items.extend(page.get("items", []))
            if not page.get("nextPageToken"):
                break
            params = {**params, "pageToken": page["nextPageToken"]}
        return items

    def insert_event(self, body):
        # sendUpdates=none: never e-mail anyone from here.
        return self._call("POST", EVENTS_URL, params={"sendUpdates": "none"}, body=body)

    def patch_event(self, event_id, body):
        return self._call("PATCH", f"{EVENTS_URL}/{quote(event_id, safe='')}", params={"sendUpdates": "none"}, body=body)

    def delete_event(self, event_id):
        self._call("DELETE", f"{EVENTS_URL}/{quote(event_id, safe='')}", params={"sendUpdates": "none"}, missing_ok=True)

    def send_mail(self, message):
        self._call("POST", SEND_URL, body={"raw": base64.urlsafe_b64encode(message).decode()})


def revoke_token(token: str) -> None:
    import requests

    try:
        requests.post(REVOKE_URL, data={"token": token}, timeout=10)
    except Exception:  # best effort: the token is deleted here either way
        log.warning("não consegui revogar o token no Google")


class GoogleService:
    """Connections plus a ready GoogleApi per account (cached, so its access token is reused)."""

    def __init__(self, connections: ConnectionStore, api_for: Callable[[str], GoogleApi],
                 revoke: Callable[[str], None] = revoke_token):
        self.connections = connections
        self._api_for = api_for
        self._revoke = revoke
        self._apis: dict[str, tuple[str, GoogleApi]] = {}
        self._lock = threading.Lock()

    def connection(self, owner: str) -> Connection | None:
        return self.connections.get(owner)

    def api(self, connection: Connection) -> GoogleApi:
        with self._lock:
            cached = self._apis.get(connection.owner)
            if cached is None or cached[0] != connection.refresh_token:
                cached = (connection.refresh_token, self._api_for(connection.refresh_token))
                self._apis[connection.owner] = cached
            return cached[1]

    def calendar_for(self, owner: str) -> GoogleApi | None:
        """The account's API when it is connected with calendar access, else None."""
        connection = self.connections.get(owner)
        return self.api(connection) if connection and connection.calendar else None

    def connect(self, owner: str, email: str, refresh_token: str, scopes: frozenset[str]) -> Connection:
        previous = self.connections.get(owner)
        connection = Connection(owner, email, refresh_token, scopes,
                                daily=previous.daily if previous else True,
                                last_daily=previous.last_daily if previous else None)
        self.connections.save(connection)
        if previous and previous.refresh_token != refresh_token:
            self._revoke(previous.refresh_token)
        return connection

    def disconnect(self, owner: str) -> None:
        connection = self.connections.get(owner)
        if connection is None:
            return
        self.forget(owner)
        self._revoke(connection.refresh_token)

    def forget(self, owner: str) -> None:
        """Drops the connection without calling Google (its access is already gone)."""
        self.connections.delete(owner)
        with self._lock:
            self._apis.pop(owner, None)

    def update(self, owner: str, **changes) -> Connection | None:
        connection = self.connections.get(owner)
        if connection is None:
            return None
        connection = replace(connection, **changes)
        self.connections.save(connection)
        return connection
