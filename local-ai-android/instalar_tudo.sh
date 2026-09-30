#!/usr/bin/env bash
# =====================================================================
#  INSTALA TUDO - Linux Mint XFCE (base Ubuntu 24.04) - GTX 960 + Ryzen
#  Driver NVIDIA 580, CUDA 12.6, telemetria/GPU, IA local (llama.cpp),
#  servidor (chat/busca/build), Android SDK, Tailscale, serviços no boot.
#  Uso (SEM sudo):  bash instalar_tudo.sh
#  Pode rodar de novo se algo falhar. Log: ~/localai-install.log
# =====================================================================
set -uo pipefail
LOG="$HOME/localai-install.log"
exec > >(tee -a "$LOG") 2>&1

BASE="$HOME/localai"
SRV="$BASE/server"
CUDA_DIR="/usr/local/cuda-12.6"
MODEL="$HOME/models/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf"
FAILED=()

[ "$(id -u)" -ne 0 ] || { echo "Rode SEM sudo: bash instalar_tudo.sh"; exit 1; }
step() { echo; echo "################ $1"; shift; "$@" || { echo "!!! FALHOU: $1"; FAILED+=("$1"); }; }

echo "==> Digite a senha uma vez; ela fica ativa durante a instalação"
sudo -v || exit 1
( while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done ) 2>/dev/null &

FREE_GB=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
[ "$FREE_GB" -ge 40 ] || { echo "Só há ${FREE_GB} GB livres; preciso de 40 GB."; exit 1; }

# ------------------------------------------------------------ 1. Pacotes
instalar_pacotes() {
  sudo apt update
  sudo apt install -y python3-venv python3-pip git cmake build-essential curl unzip \
    openjdk-17-jdk pipx flatpak lm-sensors psensor xfce4-sensors-plugin libcurl4-openssl-dev
}

# ------------------------------------------------------------- 2. Driver
instalar_driver() {
  # 580 é o ÚLTIMO driver com suporte à GTX 960 (Maxwell). 590+ não reconhece a placa.
  if dpkg -l | grep -qE '^ii\s+nvidia-driver-(59|6)[0-9]'; then
    echo "Removendo driver 590+ (não suporta a GTX 960)"
    sudo apt purge -y '^nvidia-driver-59.*' '^nvidia-driver-6.*' '^libnvidia-.*-59.*' '^nvidia-dkms-59.*' || true
    sudo apt autoremove -y || true
  fi
  sudo apt install -y nvidia-driver-580 nvidia-settings
}

# --------------------------------------------------------------- 3. CUDA
instalar_cuda() {
  if [ ! -x "$CUDA_DIR/bin/nvcc" ]; then
    curl -L --fail -o /tmp/cuda-keyring.deb \
      https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb &&
    sudo dpkg -i /tmp/cuda-keyring.deb &&
    sudo apt update &&
    sudo apt install -y cuda-toolkit-12-6   # não traz driver, então não briga com o 580
  fi
}

# ------------------------------------------- 4. Telemetria e controle GPU
instalar_telemetria() {
  sudo sensors-detect --auto || true
  flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  flatpak install -y flathub io.missioncenter.MissionCenter com.leinardi.gwe
  pipx install 'glances[web,gpu]' || true
  pipx ensurepath || true
  # Coolbits 28 = controle de ventoinha + offset de clock
  sudo mkdir -p /etc/X11/xorg.conf.d
  sudo tee /etc/X11/xorg.conf.d/20-nvidia-coolbits.conf >/dev/null <<'XCONF'
Section "Device"
    Identifier "NVIDIA GPU"
    Driver "nvidia"
    Option "Coolbits" "28"
EndSection
XCONF
}

# ---------------------------------------------------- 5. Servidor (Python)
instalar_servidor() {
  mkdir -p "$SRV" && cd "$SRV" || return 1
  cat > requirements.txt <<'EOF'
fastapi==0.115.*
uvicorn[standard]==0.32.*
httpx==0.27.*
python-multipart==0.0.*
EOF
  cat > config.py <<'EOF'
"""Configuração do servidor, lida de variáveis de ambiente (arquivo .env via setup)."""
import os
from pathlib import Path

# Token que o app do celular envia no cabeçalho "Authorization: Bearer <token>"
API_TOKEN = os.environ.get("LOCALAI_TOKEN", "")

# Endereço do llama-server (llama.cpp), que expõe uma API compatível com a da OpenAI
LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8081")

# Endereço opcional de uma instância SearXNG (pode rodar no Positivo)
SEARXNG_URL = os.environ.get("SEARXNG_URL", "")

# Onde ficam os projetos recebidos para compilar
WORK_DIR = Path(os.environ.get("LOCALAI_WORK", str(Path.home() / "localai-work")))

# Tempo máximo de uma compilação, em segundos
BUILD_TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", "1200"))

# Tamanho máximo do projeto enviado, em MB
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))
EOF
  cat > main.py <<'EOF'
"""Servidor do PC: conversa com o modelo, pesquisa na web e compila projetos Android.

Rotas:
  GET  /health   -> testa se o servidor está no ar (sem senha)
  POST /chat     -> repassa a conversa ao llama-server (com streaming)
  POST /search   -> pesquisa via SearXNG
  POST /fetch    -> baixa uma página e devolve o texto
  POST /build    -> recebe um .zip de projeto Gradle e devolve o APK debug
"""
import hmac
import re
import shutil
import subprocess
import uuid
import zipfile
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import config

app = FastAPI(title="Local AI Server")
config.WORK_DIR.mkdir(parents=True, exist_ok=True)


def require_token(authorization: str = Header(default="")) -> None:
    if not config.API_TOKEN:
        raise HTTPException(500, "LOCALAI_TOKEN não configurado no servidor")
    expected = f"Bearer {config.API_TOKEN}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Token inválido")


@app.get("/health")
def health():
    return {"ok": True}


class ChatRequest(BaseModel):
    messages: list[dict]
    max_tokens: int = 512
    temperature: float = 0.7


@app.post("/chat", dependencies=[Depends(require_token)])
async def chat(req: ChatRequest):
    payload = {**req.model_dump(), "stream": True}

    async def stream():
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream(
                    "POST", f"{config.LLAMA_URL}/v1/chat/completions", json=payload
                ) as r:
                    if r.status_code != 200:
                        yield f'data: {{"error": "llama-server respondeu {r.status_code}"}}\n\n'.encode()
                        return
                    async for chunk in r.aiter_raw():
                        yield chunk
            except httpx.ConnectError:
                yield b'data: {"error": "llama-server desligado"}\n\n'

    return StreamingResponse(stream(), media_type="text/event-stream")


class SearchRequest(BaseModel):
    query: str
    limit: int = 5


@app.post("/search", dependencies=[Depends(require_token)])
async def search(req: SearchRequest):
    if not config.SEARXNG_URL:
        raise HTTPException(503, "SEARXNG_URL não configurado")
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            f"{config.SEARXNG_URL}/search", params={"q": req.query, "format": "json"}
        )
    r.raise_for_status()
    results = r.json().get("results", [])[: req.limit]
    return [
        {"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content")}
        for x in results
    ]


class FetchRequest(BaseModel):
    url: str
    max_chars: int = 6000


@app.post("/fetch", dependencies=[Depends(require_token)])
async def fetch(req: FetchRequest):
    if not re.match(r"^https?://", req.url):
        raise HTTPException(400, "URL deve começar com http:// ou https://")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(req.url, headers={"User-Agent": "LocalAI/0.1"})
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", r.text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return {"url": str(r.url), "text": text[: req.max_chars]}


def _safe_extract(zip_path: Path, dest: Path) -> None:
    """Extrai o zip recusando caminhos que saiam da pasta de destino."""
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            target = (dest / member).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise HTTPException(400, "Zip com caminho inválido")
        z.extractall(dest)


@app.post("/build", dependencies=[Depends(require_token)])
async def build(project: UploadFile = File(...)):
    job = config.WORK_DIR / uuid.uuid4().hex
    src = job / "src"
    src.mkdir(parents=True)
    zip_path = job / "project.zip"

    size = 0
    with zip_path.open("wb") as f:
        while chunk := await project.read(1024 * 1024):
            size += len(chunk)
            if size > config.MAX_UPLOAD_MB * 1024 * 1024:
                shutil.rmtree(job, ignore_errors=True)
                raise HTTPException(413, "Projeto grande demais")
            f.write(chunk)

    try:
        _safe_extract(zip_path, src)
    except zipfile.BadZipFile:
        shutil.rmtree(job, ignore_errors=True)
        raise HTTPException(400, "Arquivo não é um zip válido")

    # O projeto pode vir dentro de uma subpasta única; procura o gradlew
    roots = [p.parent for p in src.rglob("gradlew")]
    if not roots:
        shutil.rmtree(job, ignore_errors=True)
        raise HTTPException(400, "gradlew não encontrado no projeto")
    root = min(roots, key=lambda p: len(p.parts))
    (root / "gradlew").chmod(0o755)

    try:
        proc = subprocess.run(
            ["./gradlew", "assembleDebug", "--no-daemon", "--console=plain"],
            cwd=root, capture_output=True, text=True, timeout=config.BUILD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "Compilação excedeu o tempo limite")

    if proc.returncode != 0:
        # Devolve o final do log para a IA poder ler o erro e corrigir o código
        raise HTTPException(422, {"error": "Falha na compilação", "log": proc.stdout[-4000:] + proc.stderr[-2000:]})

    apks = sorted(root.rglob("*-debug.apk"))
    if not apks:
        raise HTTPException(500, "Compilou, mas nenhum APK debug foi encontrado")
    return FileResponse(apks[0], media_type="application/vnd.android.package-archive", filename=apks[0].name)
EOF
  cat > run.sh <<'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
set -a; source .env; set +a
exec .venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
EOF
  cat > "$BASE/start_llm.sh" <<'EOF'
#!/usr/bin/env bash
# NGL = camadas na GPU. Com 2 GB de VRAM comece em 8 e suba de 2 em 2
# olhando o nvidia-smi até ficar perto de 1800 MiB.
NGL="${NGL:-8}"
MODEL="${MODEL:-$HOME/models/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf}"
exec "$HOME/llama.cpp/build/bin/llama-server" -m "$MODEL" -ngl "$NGL" -c 4096 -t 6 \
  --host 127.0.0.1 --port 8081
EOF
  chmod +x run.sh "$BASE/start_llm.sh"
  python3 -m venv .venv && .venv/bin/pip install --upgrade pip &&
  .venv/bin/pip install -r requirements.txt || return 1
  if [ ! -f .env ]; then
    TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    printf 'LOCALAI_TOKEN=%s\nLLAMA_URL=http://127.0.0.1:8081\nSEARXNG_URL=\n' "$TOKEN" > .env
    chmod 600 .env
  fi
}

# ----------------------------------------------------------- 6. Android SDK
instalar_android_sdk() {
  local SDK="$HOME/Android/Sdk" JH=/usr/lib/jvm/java-17-openjdk-amd64
  mkdir -p "$SDK/cmdline-tools"
  curl -L --fail -o /tmp/cmdtools.zip \
    https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip || return 1
  unzip -q -o /tmp/cmdtools.zip -d "$SDK/cmdline-tools"
  rm -rf "$SDK/cmdline-tools/latest"
  mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest"
  export JAVA_HOME="$JH"
  yes | "$SDK/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null || true
  "$SDK/cmdline-tools/latest/bin/sdkmanager" "platform-tools" "platforms;android-34" "build-tools;34.0.0" || return 1
  grep -q '^ANDROID_HOME=' "$SRV/.env" || printf 'ANDROID_HOME=%s\nJAVA_HOME=%s\n' "$SDK" "$JH" >> "$SRV/.env"
}

# -------------------------------------------------- 7. llama.cpp + modelo
instalar_llama() {
  [ -x "$CUDA_DIR/bin/nvcc" ] || { echo "CUDA ausente"; return 1; }
  export PATH="$CUDA_DIR/bin:$PATH"
  cd "$HOME"
  [ -d llama.cpp ] || git clone https://github.com/ggml-org/llama.cpp || return 1
  cd llama.cpp && (git pull --ff-only || true)
  echo "==> Compilando (15 a 40 minutos; é normal)"
  cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=52 \
    -DCMAKE_CUDA_COMPILER="$CUDA_DIR/bin/nvcc" &&
  cmake --build build --config Release -j 4 --target llama-server || return 1
  mkdir -p "$HOME/models"
  if [ ! -f "$MODEL" ]; then
    echo "==> Baixando o modelo (~4,7 GB; retoma se cair)"
    curl -L --fail -C - -o "$MODEL" \
      https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf
  fi
}

# ------------------------------------------------------------- 8. Tailscale
instalar_tailscale() {
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
}

# ---------------------------------------------------- 9. Serviços no boot
instalar_servicos() {
  sudo tee /etc/systemd/system/localai-llm.service >/dev/null <<UNIT
[Unit]
Description=Local AI - llama-server
After=network.target

[Service]
User=$USER
Environment=NGL=8
ExecStart=/usr/bin/env bash $BASE/start_llm.sh
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
ExecStart=/usr/bin/env bash $SRV/run.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable localai-llm.service localai-server.service  # sobem no próximo boot
}

step "1/9 Pacotes"                  instalar_pacotes
step "2/9 Driver NVIDIA 580"        instalar_driver
step "3/9 CUDA 12.6"                instalar_cuda
step "4/9 Telemetria e GPU"         instalar_telemetria
step "5/9 Servidor"                 instalar_servidor
step "6/9 Android SDK"              instalar_android_sdk
step "7/9 llama.cpp + modelo"       instalar_llama
step "8/9 Tailscale"                instalar_tailscale
step "9/9 Serviços automáticos"     instalar_servicos

echo
echo "=============================================================="
if [ ${#FAILED[@]} -eq 0 ]; then echo " TUDO INSTALADO."
else echo " Falharam (veja $LOG):"; printf '   - %s\n' "${FAILED[@]}"; fi
echo
echo " SEU TOKEN (vai no app do celular):"
grep '^LOCALAI_TOKEN=' "$SRV/.env" 2>/dev/null | cut -d= -f2
echo
echo " Agora: 1) REINICIE o PC"
echo "        2) nvidia-smi             (deve listar a GTX 960)"
echo "        3) sudo tailscale up      (abra o link e faça login)"
echo "        4) curl http://localhost:8080/health"
echo "=============================================================="
