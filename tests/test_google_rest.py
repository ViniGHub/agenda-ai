"""RestGoogle against a scripted HTTP session: the calls it makes and how it reads Google's answers."""

import base64
import json
from datetime import datetime

import pytest

from agenda_ia.config import TZ
from agenda_ia.google_link import EVENTS_URL, SEND_URL, TOKEN_URL, GoogleAccessLost, GoogleError, RestGoogle


class Answer:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body
        self.content = b"" if body is None else json.dumps(body).encode()

    def json(self):
        return self._body


class Session:
    """Replies in order to each call and records them."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def post(self, url, data=None, timeout=None):
        self.calls.append(("POST", url, data))
        return self.answers.pop(0)

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append((method, url, {"params": params, "json": json, "auth": headers["Authorization"]}))
        return self.answers.pop(0)


TOKEN = Answer(200, {"access_token": "acesso-1", "expires_in": 3600})


def test_listing_follows_pages_with_one_access_token():
    http = Session(TOKEN, Answer(200, {"items": [{"id": "a"}], "nextPageToken": "p2"}), Answer(200, {"items": [{"id": "b"}]}))
    google = RestGoogle("refresh", "cliente", "segredo", http)
    start = datetime(2026, 11, 1, tzinfo=TZ)
    assert [i["id"] for i in google.list_events(start, start.replace(month=12))] == ["a", "b"]
    assert http.calls[0] == ("POST", TOKEN_URL, {"client_id": "cliente", "client_secret": "segredo",
                                                 "refresh_token": "refresh", "grant_type": "refresh_token"})
    first, second = http.calls[1][2], http.calls[2][2]
    assert first["params"]["singleEvents"] == "true" and first["params"]["timeMin"] == "2026-11-01T00:00:00-03:00"
    assert second["params"]["pageToken"] == "p2" and second["auth"] == "Bearer acesso-1"


def test_an_expired_access_token_is_renewed_once():
    http = Session(TOKEN, Answer(401, {"error": {"message": "expired"}}), Answer(200, {"access_token": "acesso-2"}),
                   Answer(200, {"id": "g1", "etag": '"1"'}))
    google = RestGoogle("refresh", "c", "s", http)
    assert google.insert_event({"summary": "x"})["id"] == "g1"
    assert http.calls[-1][2]["auth"] == "Bearer acesso-2" and http.calls[-1][2]["params"] == {"sendUpdates": "none"}


def test_revoked_access_and_other_errors():
    with pytest.raises(GoogleAccessLost):
        RestGoogle("r", "c", "s", Session(Answer(400, {"error": "invalid_grant"}))).delete_event("x")
    google = RestGoogle("r", "c", "s", Session(TOKEN, Answer(403, {"error": {"message": "Forbidden"}})))
    with pytest.raises(GoogleError) as error:
        google.patch_event("x", {})
    assert error.value.status == 403


def test_deleting_what_is_already_gone_is_fine_and_ids_are_quoted():
    http = Session(TOKEN, Answer(410, {"error": {"message": "Gone"}}))
    RestGoogle("r", "c", "s", http).delete_event("abc_2026/x")
    assert http.calls[1][1] == f"{EVENTS_URL}/abc_2026%2Fx"


def test_mail_goes_as_base64url():
    http = Session(TOKEN, Answer(200, {"id": "m1"}))
    RestGoogle("r", "c", "s", http).send_mail("Subject: oi\n\nolá".encode())
    method, url, sent = http.calls[1]
    assert (method, url) == ("POST", SEND_URL)
    assert base64.urlsafe_b64decode(sent["json"]["raw"]).decode() == "Subject: oi\n\nolá"
