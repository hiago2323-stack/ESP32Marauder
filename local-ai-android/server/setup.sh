#!/usr/bin/env bash
# Instala o servidor no Linux Mint/Ubuntu. Rode com:  bash setup.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "==> Instalando pacotes do sistema"
sudo apt update
sudo apt install -y python3-venv python3-pip git cmake build-essential openjdk-17-jdk unzip curl

echo "==> Criando ambiente Python"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
  TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
  cat > .env <<ENV
LOCALAI_TOKEN=$TOKEN
LLAMA_URL=http://127.0.0.1:8081
SEARXNG_URL=
ENV
  chmod 600 .env
  echo "==> Token criado em server/.env (guarde, o app do celular vai pedir):"
  echo "    $TOKEN"
fi

echo "==> Pronto. Para iniciar:  bash run.sh"
