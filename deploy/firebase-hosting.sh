#!/usr/bin/env bash
# Põe o Firebase Hosting na frente do Cloud Run: um endereço limpo (https://SITE.web.app) com o
# mesmo app atrás. Rode no Cloud Shell, na raiz do repositório, depois do deploy/cloud-run.sh:
#
#   bash deploy/firebase-hosting.sh               # site "agenda-ia" -> https://agenda-ia.web.app
#   SITE=outro-nome bash deploy/firebase-hosting.sh
#
# Antes da primeira vez, adicione o Firebase ao projeto uma única vez no console
# (console.firebase.google.com > Criar projeto > "adicionar a um projeto do Google Cloud").
# É lá que os termos do Firebase são aceitos; este script não aceita nada por você.
#
# Nenhum arquivo é enviado: a versão publicada só tem a regra "todo pedido vai para o Cloud Run".
# Por isso também não precisa do Firebase CLI: o gcloud do Cloud Shell já tem o acesso, e a API
# REST do Hosting faz o resto. Pode rodar de novo quando quiser; cada execução publica outra versão.
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-east1}"
SERVICE="${SERVICE:-agenda-ia}"
SITE="${SITE:-agenda-ia}"
HOSTING="https://firebasehosting.googleapis.com/v1beta1"

if [ -z "$PROJECT" ]; then
  echo "Defina o projeto antes: gcloud config set project ID_DO_PROJETO" >&2
  exit 1
fi

# call MÉTODO URL [JSON]: imprime a resposta; em erro HTTP, mostra a mensagem e falha.
call() {
  local method=$1 url=$2 body=${3:-}
  local args=(-sS -X "$method" "$url"
    -H "Authorization: Bearer $(gcloud auth print-access-token)"
    -H "x-goog-user-project: $PROJECT"
    -H "Content-Type: application/json"
    -w $'\n%{http_code}')
  [ -n "$body" ] && args+=(--data "$body")
  local out status
  out=$(curl "${args[@]}")
  status=${out##*$'\n'}
  out=${out%$'\n'*}
  if [ "$status" -ge 400 ]; then
    printf '%s\n' "$out" >&2
    return 1
  fi
  printf '%s' "$out"
}

echo "Projeto: $PROJECT | site: $SITE | serviço: $SERVICE ($REGION)"

echo "1/4 Conferindo o Firebase no projeto..."
gcloud services enable firebase.googleapis.com firebasehosting.googleapis.com --project "$PROJECT"
if ! call GET "https://firebase.googleapis.com/v1beta1/projects/$PROJECT" >/dev/null 2>&1; then
  echo "O Firebase ainda não está neste projeto. Adicione no console (console.firebase.google.com)," >&2
  echo "escolhendo o projeto $PROJECT, e rode o script de novo." >&2
  exit 1
fi

echo "2/4 Site $SITE.web.app..."
if ! call GET "$HOSTING/projects/$PROJECT/sites/$SITE" >/dev/null 2>&1; then
  if ! call POST "$HOSTING/projects/$PROJECT/sites?siteId=$SITE" '{}' >/dev/null; then
    echo "Não consegui criar o site \"$SITE\" (o nome pode já ser de outra pessoa)." >&2
    echo "Tente outro: SITE=outro-nome bash deploy/firebase-hosting.sh" >&2
    exit 1
  fi
fi

echo "3/4 Versão que repassa tudo para o Cloud Run..."
CONFIG=$(printf '{"config":{"rewrites":[{"glob":"**","run":{"serviceId":"%s","region":"%s"}}]}}' "$SERVICE" "$REGION")
VERSION=$(call POST "$HOSTING/sites/$SITE/versions" "$CONFIG" | python3 -c 'import json, sys; print(json.load(sys.stdin)["name"])')
call PATCH "$HOSTING/$VERSION?update_mask=status" '{"status":"FINALIZED"}' >/dev/null

echo "4/4 Publicando..."
call POST "$HOSTING/sites/$SITE/releases?versionName=$VERSION" '{}' >/dev/null

echo
echo "Publicado em: https://$SITE.web.app"
echo "Para o login com Google funcionar nesse endereço, inclua https://$SITE.web.app nas"
echo "\"Origens JavaScript autorizadas\" do cliente OAuth (Google Auth Platform > Clientes)."
