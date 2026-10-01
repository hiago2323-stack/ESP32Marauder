#!/usr/bin/env bash
# Inicia o servidor na porta 8080 (somente neste PC (rede: etapa futura))
set -euo pipefail
cd "$(dirname "$0")"
set -a; source .env; set +a
exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080
