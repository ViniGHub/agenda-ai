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
