#!/usr/bin/env bash
# Publica o agenda-ia no Google Cloud Run. Rode no Cloud Shell, na raiz do repositório:
#
#   bash deploy/cloud-run.sh
#
# Pode rodar de novo a cada atualização: o que já existe (segredos, contas de serviço) é reaproveitado.
# Variáveis opcionais:
#   PROJECT           projeto (padrão: o do gcloud)
#   REGION            região (padrão: us-east1)
#   GOOGLE_CLIENT_ID  ID do cliente OAuth do login com Google; se vier vazio, o serviço mantém o atual
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-east1}"
SERVICE="agenda-ia"
SA_NAME="agenda-ia-run"
SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
BUILD_SA_NAME="agenda-ia-build"
BUILD_SA="$BUILD_SA_NAME@$PROJECT.iam.gserviceaccount.com"
GROQ_SECRET="groq-api-key"
SESSION_SECRET="agenda-ia-sessao"
ALLOWED_SECRET="agenda-ia-permitidos"

if [ -z "$PROJECT" ]; then
  echo "Defina o projeto antes: gcloud config set project ID_DO_PROJETO" >&2
  exit 1
fi
echo "Projeto: $PROJECT | região: $REGION | serviço: $SERVICE"

secret_exists() { gcloud secrets describe "$1" --project "$PROJECT" >/dev/null 2>&1; }
create_secret() { gcloud secrets create "$1" --project "$PROJECT" --replication-policy=automatic --data-file=- >/dev/null; }

echo "1/5 Ativando as APIs (Cloud Run, Cloud Build, Artifact Registry, Secret Manager)..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com --project "$PROJECT"

echo "2/5 Segredos..."
if secret_exists "$GROQ_SECRET"; then
  echo "    chave do Groq: já existe (para trocar: gcloud secrets versions add $GROQ_SECRET --data-file=-)"
else
  # Digitada por você: não aparece na tela nem fica no histórico do shell.
  read -rsp "    Cole a chave do Groq e aperte Enter: " KEY; echo
  printf '%s' "$KEY" | create_secret "$GROQ_SECRET"
  unset KEY
fi
if secret_exists "$SESSION_SECRET"; then
  echo "    chave da sessão: já existe"
else
  # Assina os cookies de login. Gerada aqui, nunca exibida; trocá-la desconecta todo mundo.
  head -c 48 /dev/urandom | base64 | tr -d '\n' | create_secret "$SESSION_SECRET"
  echo "    chave da sessão: criada"
fi
if secret_exists "$ALLOWED_SECRET"; then
  echo "    e-mails com acesso: já existe (para trocar: gcloud secrets versions add $ALLOWED_SECRET --data-file=-)"
else
  # Fica fora do repositório público: é a lista de quem entra na área privada.
  read -rp "    E-mails com acesso à área privada (separados por vírgula; vazio = ninguém): " ALLOWED
  # Secret Manager does not store empty values; "ninguem" is not an e-mail, so nobody gets in.
  printf '%s' "${ALLOWED:-ninguem}" | create_secret "$ALLOWED_SECRET"
  unset ALLOWED
fi

echo "3/5 Conta de serviço do app, que só pode ler esses segredos..."
if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" --display-name "agenda-ia (Cloud Run)"
fi
for secret in "$GROQ_SECRET" "$SESSION_SECRET" "$ALLOWED_SECRET"; do
  gcloud secrets add-iam-policy-binding "$secret" --project "$PROJECT" \
    --member "serviceAccount:$SA" --role roles/secretmanager.secretAccessor >/dev/null
done

echo "4/5 Conta de serviço do build..."
# Without this, Cloud Build runs as the Compute Engine default account, which may lack access
# to the uploaded sources and is also the identity of any VM in the project. A dedicated account
# with only the Cloud Run Builder role keeps the build isolated.
if ! gcloud iam service-accounts describe "$BUILD_SA" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$BUILD_SA_NAME" --project "$PROJECT" --display-name "agenda-ia (build)"
  NEW_BUILD_SA=1
fi
gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$BUILD_SA" \
  --role roles/run.builder --condition=None >/dev/null
if [ "${NEW_BUILD_SA:-0}" = 1 ]; then
  echo "    esperando 60 s para a permissão nova propagar..."
  sleep 60
fi

echo "5/5 Build e deploy (leva de 5 a 10 minutos)..."
EXTRA=()
if [ -n "${GOOGLE_CLIENT_ID:-}" ]; then
  # Public value (it goes to the browser); only changed when passed, otherwise the service keeps it.
  EXTRA+=(--update-env-vars "GOOGLE_CLIENT_ID=$GOOGLE_CLIENT_ID")
fi
# min-instances 0: desliga quando ninguém usa (custo zero parado).
# max-instances 1: teto de gasto, mesmo sob abuso; a API ainda limita 20 perguntas por visitante.
gcloud run deploy "$SERVICE" --project "$PROJECT" --region "$REGION" --source . \
  --build-service-account "projects/$PROJECT/serviceAccounts/$BUILD_SA" \
  --service-account "$SA" \
  --set-secrets "GROQ_API_KEY=$GROQ_SECRET:latest,AGENDA_IA_SESSAO_SEGREDO=$SESSION_SECRET:latest,AGENDA_IA_PERMITIDOS=$ALLOWED_SECRET:latest" \
  --memory 2Gi --cpu 1 --cpu-boost --min-instances 0 --max-instances 1 \
  --concurrency 10 --timeout 120 --allow-unauthenticated --quiet "${EXTRA[@]}"

echo
echo "Publicado em: $(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
