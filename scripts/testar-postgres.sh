#!/usr/bin/env bash
# Roda os testes de armazenamento contra um Postgres com pgvector descartável, no Docker.
# Útil no Cloud Shell (que tem Docker) ou em qualquer máquina com Docker:
#
#   bash scripts/testar-postgres.sh
#
# O container é apagado no fim. Instala só o necessário para esses testes (sem PyTorch).
set -euo pipefail

NAME=agenda-ia-pg-teste
PORT="${PORT_TESTE:-55432}"
VENV="${VENV_TESTE:-/tmp/agenda-ia-venv}"

docker rm -f "$NAME" >/dev/null 2>&1 || true
echo "Subindo Postgres com pgvector..."
docker run -d --rm --name "$NAME" -e POSTGRES_PASSWORD=teste -p "$PORT:5432" pgvector/pgvector:pg17 >/dev/null
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 60); do
  if docker exec "$NAME" psql -U postgres -tAc "select 1" >/dev/null 2>&1; then break; fi
  sleep 1
done
sleep 3  # the image restarts once after its first initialization

if [ ! -x "$VENV/bin/pytest" ]; then
  echo "Preparando o ambiente Python em $VENV..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q --no-deps --ignore-requires-python -e .
  "$VENV/bin/pip" install -q numpy icalendar recurring-ical-events pydantic python-dotenv sqlite-vec tzdata \
    "psycopg[binary]" psycopg-pool cryptography pytest
fi

echo "Rodando os testes do Postgres..."
TEST_DATABASE_URL="postgresql://postgres:teste@localhost:$PORT/postgres" \
  "$VENV/bin/pytest" -q tests/test_stores.py -k postgres "$@"
