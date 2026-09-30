#!/usr/bin/env bash
# Inicia o modelo. Só aceita conexões do próprio PC (o server/ faz a ponte).
# NGL = quantas camadas vão para a GPU. Com 2 GB de VRAM, comece com 8 e
# aumente de 2 em 2 olhando o "nvidia-smi" até ficar perto de 1800 MiB.
NGL="${NGL:-8}"
MODEL="${MODEL:-$HOME/models/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf}"
exec "$HOME/llama.cpp/build/bin/llama-server" \
  -m "$MODEL" -ngl "$NGL" -c 4096 -t 6 --host 127.0.0.1 --port 8081
