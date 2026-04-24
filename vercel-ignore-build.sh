#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────
# Vercel Ignored Build Step — Groundwork Command Center
# ───────────────────────────────────────────────────────────────
# Objetivo: evitar deploys desnecessários do Vercel quando o
# commit modifica apenas arquivos de dados (JSON, XLSX) gerados
# pelo workflow automático gwms-sync.yml que roda a cada 10min.
#
# Exit codes (contrato do Vercel):
#   exit 0 → NÃO fazer deploy (skip)
#   exit 1 → FAZER deploy (proceed)
#
# Regra: se o diff contém SOMENTE arquivos .json / .xlsx → skip
#        caso contrário → deploy
#
# Referência: https://vercel.com/docs/projects/overview#ignored-build-step
# ───────────────────────────────────────────────────────────────

set -eo pipefail

echo "🔍 Vercel ignore-build check"
echo "  branch: ${VERCEL_GIT_COMMIT_REF:-unknown}"
echo "  commit: ${VERCEL_GIT_COMMIT_SHA:-unknown}"

# Lista de arquivos alterados no commit atual vs anterior.
# Fallback: se não tem HEAD^ (ex: primeiro commit), força deploy.
if ! CHANGED=$(git diff --name-only HEAD^ HEAD 2>/dev/null); then
  echo "⚠️  Sem histórico anterior — fazendo deploy por segurança"
  exit 1
fi

if [ -z "$CHANGED" ]; then
  echo "⚠️  Nenhum arquivo detectado no diff — fazendo deploy por segurança"
  exit 1
fi

echo "📝 Arquivos alterados neste commit:"
echo "$CHANGED" | sed 's/^/    /'

# Filtra: quais arquivos NÃO são dados (.json/.xlsx)
# Qualquer coisa além disso força o deploy.
CODE_FILES=$(echo "$CHANGED" | grep -vE '\.(json|xlsx)$' || true)

if [ -z "$CODE_FILES" ]; then
  echo "🟢 Apenas arquivos de dados (.json/.xlsx) mudaram — PULANDO deploy"
  echo "   (economia de cota: Vercel Hobby tem limite de 100 deploys/dia)"
  exit 0
fi

echo "🔵 Arquivos de código alterados — FAZENDO deploy:"
echo "$CODE_FILES" | sed 's/^/    /'
exit 1
