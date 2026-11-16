#!/usr/bin/env bash
# Publica o agenda-ia no Google Cloud Run. Rode no Cloud Shell, na raiz do repositório:
#
#   bash deploy/cloud-run.sh
#
# Pode rodar de novo a cada atualização: o que já existe (segredo, conta de serviço) é reaproveitado.
# Variáveis opcionais: PROJECT (padrão: o projeto do gcloud) e REGION (padrão: us-east1).
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-east1}"
SERVICE="agenda-ia"
SA_NAME="agenda-ia-run"
SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
SECRET="groq-api-key"

if [ -z "$PROJECT" ]; then
  echo "Defina o projeto antes: gcloud config set project ID_DO_PROJETO" >&2
  exit 1
fi
echo "Projeto: $PROJECT | região: $REGION | serviço: $SERVICE"

echo "1/4 Ativando as APIs (Cloud Run, Cloud Build, Artifact Registry, Secret Manager)..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com --project "$PROJECT"

echo "2/4 Segredo com a chave do Groq..."
if gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  echo "    já existe; para trocar a chave: gcloud secrets versions add $SECRET --data-file=-"
else
  # A chave é digitada por você: não aparece na tela nem fica no histórico do shell.
  read -rsp "    Cole a chave do Groq e aperte Enter: " KEY
  echo
  printf '%s' "$KEY" | gcloud secrets create "$SECRET" --project "$PROJECT" \
    --replication-policy=automatic --data-file=-
  unset KEY
fi

echo "3/4 Conta de serviço que só pode ler esse segredo..."
if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" --display-name "agenda-ia (Cloud Run)"
fi
gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT" \
  --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor >/dev/null

echo "4/4 Build e deploy (leva de 5 a 10 minutos)..."
# min-instances 0: desliga quando ninguém usa (custo zero parado).
# max-instances 1: teto de gasto, mesmo sob abuso; a API ainda limita 20 perguntas por visitante.
gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --source . \
  --service-account "$SA" --set-secrets "GROQ_API_KEY=$SECRET:latest" \
  --memory 2Gi --cpu 1 --cpu-boost --min-instances 0 --max-instances 1 \
  --concurrency 10 --timeout 120 --allow-unauthenticated --quiet

echo
echo "Publicado em: $(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
