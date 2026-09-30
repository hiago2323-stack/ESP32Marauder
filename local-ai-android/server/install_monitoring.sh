#!/usr/bin/env bash
# Telemetria (CPU, GPU, RAM, disco) e controle da GPU NVIDIA no Linux Mint XFCE.
set -euo pipefail

sudo apt update
sudo apt install -y lm-sensors psensor xfce4-sensors-plugin pipx flatpak

echo "==> Detectando sensores (responda ENTER/yes se perguntar algo)"
sudo sensors-detect --auto || true

echo "==> Mission Center (painel gráfico de CPU/GPU/RAM/disco) e GreenWithEnvy (GPU)"
flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install -y flathub io.missioncenter.MissionCenter com.leinardi.gwe

echo "==> Glances: painel web para ver a telemetria do celular"
pipx install 'glances[web,gpu]' || true
pipx ensurepath

echo "==> Liberando overclock e controle de ventoinha da NVIDIA (Coolbits 28)"
sudo mkdir -p /etc/X11/xorg.conf.d
sudo tee /etc/X11/xorg.conf.d/20-nvidia-coolbits.conf >/dev/null <<'XCONF'
Section "Device"
    Identifier "NVIDIA GPU"
    Driver "nvidia"
    Option "Coolbits" "28"
EndSection
XCONF
echo "==> Reinicie o PC para o Coolbits valer."
