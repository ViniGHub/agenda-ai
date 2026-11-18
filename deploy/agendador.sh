#!/usr/bin/env bash
# Cria (ou atualiza) a tarefa do Cloud Scheduler que manda o e-mail das 8h. Rode no Cloud Shell,
# na raiz do repositório, depois de publicar o serviço (bash deploy/cloud-run.sh):
#
#   bash deploy/agendador.sh
#
# O que faz:
#   1. liga a API do Cloud Scheduler;
#   2. cria a conta de serviço agenda-ia-agendador, sem nenhum papel: ela só assina a chamada
#      (um token OIDC do Google), e o app confere quem assinou antes de fazer qualquer coisa;
#   3. cria ou atualiza a tarefa agenda-ia-resumo-diario: todo dia às 8h de São Paulo,
#      POST no endereço do Cloud Run em /api/tarefas/resumo-diario.
#
# Variáveis opcionais: PROJECT, REGION (padrão us-east1), HORARIO (cron, padrão "0 8 * * *").
# Custo: o Cloud Scheduler dá 3 tarefas grátis por conta de faturamento; esta é uma.
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-east1}"
SERVICE="agenda-ia"
JOB="agenda-ia-resumo-diario"
SA_NAME="agenda-ia-agendador"
SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
HORARIO="${HORARIO:-0 8 * * *}"
AUDIENCE="agenda-ia-tarefas"  # o mesmo valor de TASKS_AUDIENCE em src/agenda_ia/api.py

if [ -z "$PROJECT" ]; then
  echo "Defina o projeto antes: gcloud config set project ID_DO_PROJETO" >&2
  exit 1
fi

echo "1/3 Ativando a API do Cloud Scheduler..."
gcloud services enable cloudscheduler.googleapis.com --project "$PROJECT"

echo "2/3 Conta de serviço que assina as chamadas..."
if ! gcloud iam service-accounts describe "$SA" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SA_NAME" --project "$PROJECT" --display-name "agenda-ia (agendador)"
fi

URL="$(gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format 'value(status.url)')"
if [ -z "$URL" ]; then
  echo "O serviço $SERVICE não foi encontrado: rode antes bash deploy/cloud-run.sh" >&2
  exit 1
fi

echo "3/3 Tarefa $JOB ($HORARIO, America/Sao_Paulo)..."
ARGS=(--project "$PROJECT" --location "$REGION" --schedule "$HORARIO" --time-zone "America/Sao_Paulo"
      --uri "$URL/api/tarefas/resumo-diario" --http-method POST
      --oidc-service-account-email "$SA" --oidc-token-audience "$AUDIENCE"
      --attempt-deadline 180s --max-retry-attempts 2)
if gcloud scheduler jobs describe "$JOB" --project "$PROJECT" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$JOB" "${ARGS[@]}" >/dev/null
  echo "    atualizada"
else
  gcloud scheduler jobs create http "$JOB" "${ARGS[@]}" >/dev/null
  echo "    criada"
fi

echo
echo "Pronto. Para testar agora, sem esperar as 8h (manda o e-mail de hoje, se ainda não foi):"
echo "  gcloud scheduler jobs run $JOB --location $REGION && sleep 30 && \\"
echo "  gcloud logging read 'resource.type=\"cloud_run_revision\" AND httpRequest.requestUrl:\"resumo-diario\"' --limit 1 --format 'value(httpRequest.status)'"
