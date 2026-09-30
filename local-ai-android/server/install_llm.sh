#!/usr/bin/env bash
# Compila o llama.cpp com CUDA (GTX 960 = arquitetura 52) e baixa um modelo.
# Não precisa da GPU ativa: pode rodar antes de reiniciar o PC.
set -euo pipefail

CUDA_DIR="${CUDA_DIR:-/usr/local/cuda-12.6}"
[ -x "$CUDA_DIR/bin/nvcc" ] || { echo "CUDA não encontrado em $CUDA_DIR (rode install_all.sh)"; exit 1; }
export PATH="$CUDA_DIR/bin:$PATH"

sudo apt install -y build-essential cmake git curl libcurl4-openssl-dev

cd "$HOME"
[ -d llama.cpp ] || git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
git pull --ff-only || true

echo "==> Compilando (demora de 15 a 40 minutos; é normal)"
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=52 \
  -DCMAKE_CUDA_COMPILER="$CUDA_DIR/bin/nvcc"
cmake --build build --config Release -j 4 --target llama-server

mkdir -p "$HOME/models"
MODEL="$HOME/models/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
if [ ! -f "$MODEL" ]; then
  echo "==> Baixando o modelo (uns 4,7 GB; pode retomar se cair)"
  curl -L --fail -C - -o "$MODEL" \
    "https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
fi
echo "==> llama.cpp pronto."
