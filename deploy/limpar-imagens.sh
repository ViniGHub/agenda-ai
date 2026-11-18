#!/usr/bin/env bash
# Mantém no Artifact Registry só as imagens mais novas do agenda-ia (padrão: 2), para o
# armazenamento ficar perto da cota gratuita de 0,5 GB. Rode no Cloud Shell:
#
#   bash deploy/limpar-imagens.sh
#
# O que faz:
#   1. liga uma regra de limpeza no repositório: o Google apaga sozinho, cerca de uma vez por dia,
#      toda imagem que não esteja entre as mais novas;
#   2. apaga agora as que já passaram disso, sem esperar a regra.
#
# A mais nova é a que está no ar; a anterior serve para voltar uma revisão sem build. Revisões mais
# antigas param de funcionar (não há mais imagem para elas), o que não afeta o site.
# Variáveis opcionais: PROJECT, REGION (padrão us-east1), MANTER (padrão 2).
set -euo pipefail

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${REGION:-us-east1}"
REPO="cloud-run-source-deploy"
MANTER="${MANTER:-2}"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/agenda-ia"

if [ -z "$PROJECT" ]; then
  echo "Defina o projeto antes: gcloud config set project ID_DO_PROJETO" >&2
  exit 1
fi
if [ "$MANTER" -lt 1 ]; then
  echo "MANTER precisa ser pelo menos 1: a imagem mais nova é a que está no ar." >&2
  exit 1
fi

size() {
  # The size is not a field of the repository: gcloud prints it as an extra "Repository Size:" line.
  gcloud artifacts repositories describe "$REPO" --project "$PROJECT" --location "$REGION" 2>&1 \
    | sed -n 's/^Repository Size: //p'
}
echo "Repositório $REPO: $(size)"

echo "1/2 Regra de limpeza automática (manter as $MANTER mais novas)..."
POLICY="$(mktemp)"
trap 'rm -f "$POLICY"' EXIT
cat > "$POLICY" <<EOF
[
  { "name": "manter-as-mais-novas", "action": { "type": "Keep" }, "mostRecentVersions": { "keepCount": $MANTER } },
  { "name": "apagar-o-resto", "action": { "type": "Delete" }, "condition": { "tagState": "ANY" } }
]
EOF
# Regras "Keep" vencem as "Delete": só sai o que não está entre as mais novas.
gcloud artifacts repositories set-cleanup-policies "$REPO" --project "$PROJECT" --location "$REGION" \
  --policy "$POLICY" --no-dry-run >/dev/null

echo "2/2 Apagando agora as imagens além das $MANTER mais novas..."
mapfile -t OLD < <(gcloud artifacts docker images list "$IMAGE" --sort-by=~CREATE_TIME --format 'value(version)' \
  | tail -n +"$((MANTER + 1))")
for digest in "${OLD[@]}"; do
  [ -n "$digest" ] || continue
  if gcloud artifacts docker images delete "$IMAGE@$digest" --delete-tags --quiet >/dev/null 2>&1; then
    echo "    apagada ${digest:0:19}…"
  else
    echo "    não consegui apagar ${digest:0:19}… (a regra automática tenta de novo)" >&2
  fi
done
echo "    ${#OLD[@]} imagem(ns) apagada(s)."

echo "Repositório $REPO: $(size) (o espaço das camadas soltas pode levar alguns minutos para ser liberado)"
