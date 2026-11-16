# One container: FastAPI serves the API and the built React app (single origin, no CORS).
# Used by Hugging Face Spaces (sdk: docker); the GROQ_API_KEY comes from the Space's secrets.

# ---------- 1. build the React app ----------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- 2. Python app ----------
FROM python:3.12-slim

# Spaces runs the container as user 1000.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user pyproject.toml README.md ./
COPY --chown=user src ./src
# CPU-only PyTorch: the default wheel brings ~2 GB of CUDA libraries the free CPU Space cannot use.
RUN pip install --no-cache-dir --user torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir --user -e ".[api]"

# Download the embedding model at build time, so a cold start does not wait for it.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

COPY --chown=user --from=web /web/dist ./web/dist

EXPOSE 7860
CMD ["uvicorn", "agenda_ia.api:app", "--host", "0.0.0.0", "--port", "7860"]
