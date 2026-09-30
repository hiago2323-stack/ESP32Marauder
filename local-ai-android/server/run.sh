#!/usr/bin/env bash
# Inicia o servidor na porta 8080 (acessível pela rede local e pelo Tailscale)
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
exec .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
