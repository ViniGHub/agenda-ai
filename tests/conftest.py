import re
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = date(2026, 11, 16)  # a Monday; the sample agenda is built around this week


@pytest.fixture
def sample_ics(tmp_path: Path) -> Path:
    from agenda_ia.sample import build

    path = tmp_path / "agenda.ics"
    path.write_bytes(build(BASE).to_ical())
    return path


class FakeEmbedder:
    """Hashed bag of word stems: deterministic and instant, so tests never download the real model."""

    dim = 512  # large enough that unrelated words rarely share a slot

    def passages(self, texts):
        return np.stack([self._vector(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)

    def query(self, text):
        return self._vector(text)

    def _vector(self, text):
        vector = np.zeros(self.dim, np.float32)
        for word in re.findall(r"\w+", text.lower()):
            vector[zlib.crc32(word[:5].encode()) % self.dim] += 1.0
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


class FakeModel:
    """Returns canned replies in order and records what it was sent. An Exception reply is raised."""

    def __init__(self, *replies):
        from agenda_ia.llm import Usage

        self.replies = list(replies)
        self.sent = []
        self.usage = Usage()

    def complete_json(self, system, user, max_tokens=1024):
        self.sent.append((system, user))
        self.usage.calls += 1
        self.usage.prompt_tokens += 100
        self.usage.completion_tokens += 10
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def sample_db(sample_ics, fake_embedder):
    """The sample agenda in an in-memory SqliteEventStore (shareable across threads, like the API's)."""
    from agenda_ia.calendar_loader import load_events
    from agenda_ia.store import SqliteEventStore

    events = load_events(sample_ics, date(2026, 11, 1), date(2027, 2, 1))
    return SqliteEventStore.in_memory(events, fake_embedder.passages([e.search_text() for e in events]), fake_embedder.dim)


class FakeTranscriber:
    """Returns a fixed text (or raises it) and records what it was sent."""

    def __init__(self, text="marca dentista quinta às 15h"):
        self.text = text
        self.received = []

    def transcribe(self, audio, filename):
        self.received.append((len(audio), filename))
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


class FakeGoogle:
    """GoogleApi in memory: one calendar of items and an outbox. Set `fail` to make every call raise it."""

    def __init__(self, items=()):
        self.items = {item["id"]: dict(item) for item in items}
        self.sent = []
        self.calls = []
        self.fail = None
        self._next = 1

    def _call(self, name):
        self.calls.append(name)
        if self.fail is not None:
            raise self.fail

    def list_events(self, time_min, time_max):
        self._call("list")
        return [dict(item) for item in self.items.values()]

    def insert_event(self, body):
        self._call("insert")
        google_id = f"g{self._next}"
        self._next += 1
        self.items[google_id] = {**body, "id": google_id, "etag": '"1"', "iCalUID": f"{google_id}@google.com",
                                 "status": "confirmed"}
        return dict(self.items[google_id])

    def patch_event(self, event_id, body):
        from agenda_ia.google_link import GoogleError

        self._call("patch")
        if event_id not in self.items:
            raise GoogleError(404, "Not Found")
        item = self.items[event_id]
        item.update(body)
        item["etag"] = f'"{int(item["etag"].strip(chr(34))) + 1}"'
        return dict(item)

    def delete_event(self, event_id):
        self._call("delete")
        self.items.pop(event_id, None)

    def send_mail(self, message):
        self._call("send")
        self.sent.append(message)
