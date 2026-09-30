#!/usr/bin/env bash
# Compila o llama.cpp com CUDA (GTX 960 = arquitetura 52) e baixa um modelo.
# Uso:  bash install_llm.sh
set -euo pipefail

echo "==> Conferindo a placa de vídeo"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv || {
  echo "nvidia-smi falhou: instale o driver NVIDIA pelo Gerenciador de Drivers e reinicie."; exit 1; }

echo "==> Instalando compiladores e CUDA"
sudo apt update
# gcc-12 é necessário porque o CUDA do Ubuntu 24.04 (12.0) não aceita o gcc-13
sudo apt install -y build-essential cmake git curl libcurl4-openssl-dev gcc-12 g++-12 nvidia-cuda-toolkit

cd "$HOME"
[ -d llama.cpp ] || git clone https://github.com/ggml-org/llama.cpp
cd llama.cpp
git pull --ff-only || true

echo "==> Compilando (demora de 15 a 40 minutos no Ryzen; é normal)"
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=52 \
  -DCMAKE_C_COMPILER=gcc-12 -DCMAKE_CXX_COMPILER=g++-12 -DCMAKE_CUDA_HOST_COMPILER=g++-12
cmake --build build --config Release -j 4 --target llama-server

mkdir -p "$HOME/models"
MODEL="$HOME/models/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
if [ ! -f "$MODEL" ]; then
  echo "==> Baixando o modelo (uns 4,7 GB)"
  curl -L --fail -C - -o "$MODEL" \
    "https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
fi
echo "==> Pronto. Inicie com:  bash start_llm.sh"
