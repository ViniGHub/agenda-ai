"""The HTTP layer: the FastAPI app (server.py) and sign-in (auth.py)."""

from .server import app, create_app  # noqa: F401  (uvicorn agenda_ia.api:app)
