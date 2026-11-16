import importlib.util
import re
import zlib
from datetime import date
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = date(2026, 11, 16)  # a Monday; the sample agenda is built around this week


def _load_generator():
    spec = importlib.util.spec_from_file_location("gerar_agenda_exemplo", ROOT / "scripts" / "gerar_agenda_exemplo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample_ics(tmp_path: Path) -> Path:
    path = tmp_path / "agenda.ics"
    path.write_bytes(_load_generator().build(BASE).to_ical())
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
