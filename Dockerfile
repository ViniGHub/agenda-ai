# One container: FastAPI serves the API and the built React app (single origin, no CORS).
# Deployed on Google Cloud Run (deploy/cloud-run.sh); the GROQ_API_KEY comes from Secret Manager.

# ---------- 1. build the React app ----------
FROM node:22-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# ---------- 2. Python app ----------
FROM python:3.12-slim

# Run as an unprivileged user.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HOME=/home/user/.cache/huggingface \
    PYTHONUNBUFFERED=1
WORKDIR /home/user/app

COPY --chown=user pyproject.toml README.md ./
COPY --chown=user src ./src
# CPU-only PyTorch: the default wheel brings ~2 GB of CUDA libraries a CPU-only container cannot use.
RUN pip install --no-cache-dir --user torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir --user -e ".[api]"

# Download the embedding model at build time, so a cold start does not wait for it,
# and never reach the Hugging Face Hub at runtime.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"
ENV HF_HUB_OFFLINE=1

COPY --chown=user --from=web /web/dist ./web/dist

# Cloud Run tells the container which port to listen on through $PORT (8080); 7860 is the local default.
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn agenda_ia.api:app --host 0.0.0.0 --port ${PORT:-7860}"]
