#!/usr/bin/env bash
# Instala TUDO de uma vez no Linux Mint XFCE (base Ubuntu 24.04):
#   driver NVIDIA 580, CUDA 12.6, telemetria, servidor, llama.cpp + modelo,
#   Android SDK, Tailscale e serviços que sobem sozinhos no boot.
# Uso (NÃO use sudo antes):  bash install_all.sh
# Log completo em ~/localai-install.log. Pode rodar de novo se algo falhar.
set -uo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
LOG="$HOME/localai-install.log"
exec > >(tee -a "$LOG") 2>&1

[ "$(id -u)" -ne 0 ] || { echo "Rode sem sudo: bash install_all.sh"; exit 1; }
FAILED=()
step() { echo; echo "################ $1"; shift; "$@" || { echo "!!! FALHOU: $*"; FAILED+=("$*"); }; }

echo "==> Vou pedir a senha uma vez e mantê-la ativa durante a instalação"
sudo -v || exit 1
( while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done ) 2>/dev/null &

FREE_GB=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
[ "$FREE_GB" -ge 40 ] || { echo "Só há ${FREE_GB} GB livres; preciso de pelo menos 40 GB."; exit 1; }

# ---------------------------------------------------------------- 1. Driver
install_driver() {
  # A série 580 é a última com suporte à GTX 960 (Maxwell). 590+ não reconhece a placa.
  if dpkg -l | grep -qE '^ii\s+nvidia-driver-(59|6)[0-9]'; then
    echo "Driver 590+ encontrado: removendo, pois não suporta a GTX 960."
    sudo apt purge -y '^nvidia-driver-59.*' '^nvidia-driver-6.*' '^libnvidia-.*-59.*' '^nvidia-dkms-59.*' || true
    sudo apt autoremove -y || true
  fi
  sudo apt update
  sudo apt install -y nvidia-driver-580 nvidia-settings
}

# ------------------------------------------------------------ 2. CUDA 12.6
install_cuda() {
  if [ ! -x /usr/local/cuda-12.6/bin/nvcc ]; then
    curl -L --fail -o /tmp/cuda-keyring.deb \
      https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb
    sudo dpkg -i /tmp/cuda-keyring.deb
    sudo apt update
    # cuda-toolkit NÃO traz driver, então não briga com o 580
    sudo apt install -y cuda-toolkit-12-6
  fi
}

install_tailscale() {
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
}

# ------------------------------------------------------ 3. Serviços no boot
install_services() {
  sudo tee /etc/systemd/system/localai-llm.service >/dev/null <<UNIT
[Unit]
Description=Local AI - llama-server
After=network.target

[Service]
User=$USER
Environment=NGL=8
ExecStart=/usr/bin/env bash $HERE/start_llm.sh
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
  sudo tee /etc/systemd/system/localai-server.service >/dev/null <<UNIT
[Unit]
Description=Local AI - servidor (chat, busca, build)
After=network.target localai-llm.service

[Service]
User=$USER
ExecStart=/usr/bin/env bash $HERE/run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  # enable sem start: a GPU só fica ativa depois do reinício
  sudo systemctl enable localai-llm.service localai-server.service
}

step "1/8 Driver NVIDIA 580"            install_driver
step "2/8 CUDA 12.6"                    install_cuda
step "3/8 Telemetria e controle da GPU" bash "$HERE/install_monitoring.sh"
step "4/8 Servidor (token)"             bash "$HERE/setup.sh"
step "5/8 Android SDK"                  bash "$HERE/install_android_sdk.sh"
step "6/8 llama.cpp + modelo"           bash "$HERE/install_llm.sh"
step "7/8 Tailscale"                    install_tailscale
step "8/8 Serviços automáticos"         install_services

echo
echo "=============================================================="
if [ ${#FAILED[@]} -eq 0 ]; then
  echo " TUDO INSTALADO."
else
  echo " Terminou, mas estas etapas falharam (veja $LOG):"
  printf '   - %s\n' "${FAILED[@]}"
fi
echo
echo " Seu TOKEN (coloque no app do celular):"
grep '^LOCALAI_TOKEN=' "$HERE/.env" 2>/dev/null | cut -d= -f2
echo
echo " Agora: 1) REINICIE o PC"
echo "        2) rode:  nvidia-smi        (deve listar a GTX 960)"
echo "        3) rode:  sudo tailscale up (abra o link e faça login)"
echo "        4) teste: curl http://localhost:8080/health"
echo "=============================================================="
