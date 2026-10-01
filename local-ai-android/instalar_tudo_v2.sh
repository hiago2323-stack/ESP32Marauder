#!/usr/bin/env bash
# =====================================================================
#  INSTALA TUDO - Linux Mint XFCE (base Ubuntu 22.04 ou 24.04) - GTX 960 + Ryzen
#  Driver NVIDIA 580, CUDA 12.6, telemetria/GPU, IA local (llama.cpp, modelo 3B),
#  servidor com conversa por TEXTO e VOZ (100% local), pesquisa web, MEMÓRIA que
#  cresce com o tempo,
#  compilação Android, serviços no boot. (Tailscale/celular: fica pra depois.)
#  Uso (SEM sudo):  bash instalar_tudo.sh
#  Modelo: padrão 7B (mais inteligente, ~5 palavras/s na GTX 960).
#  Para o 3B (mais leve e rápido):  MODELO=3b bash instalar_tudo_v2.sh
#  Pode rodar de novo (também por cima de uma instalação anterior: ele aproveita
#  o que já existe, mantém o token e as memórias). Log: ~/localai-install.log
# =====================================================================
set -uo pipefail
LOG="$HOME/localai-install.log"
exec > >(tee -a "$LOG") 2>&1

USER="${USER:-$(id -un)}"   # nome do usuário (algumas sessões não definem)
BASE="$HOME/localai"
SRV="$BASE/server"
CUDA_DIR="/usr/local/cuda-12.6"
MODELO="${MODELO:-7b}"
case "$MODELO" in
  3b) MODEL_FILE=Qwen2.5-3B-Instruct-Q4_K_M.gguf
      MODEL_URL=https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/$MODEL_FILE
      NGL_PADRAO=auto; MODEL_TAM="~1,9 GB" ;;
  *)  MODEL_FILE=Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf
      MODEL_URL=https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/$MODEL_FILE
      NGL_PADRAO=auto; MODEL_TAM="~4,7 GB" ;;
esac
MODEL="$HOME/models/$MODEL_FILE"
FAILED=()

[ "$(id -u)" -ne 0 ] || { echo "Rode SEM sudo: bash instalar_tudo.sh"; exit 1; }
# Baixa para um arquivo temporário (.part) e só renomeia no final: um download interrompido
# nunca é confundido com um arquivo completo. Pula se o arquivo final já existe.
baixar() {  # baixar URL DESTINO
  [ -s "$2" ] && return 0
  curl -L --fail -C - -o "$2.part" "$1" && mv "$2.part" "$2"
}
step() { echo; echo "################ $1"; shift; "$@" || { echo "!!! FALHOU: $1"; FAILED+=("$1"); }; }

echo "==> Digite a senha uma vez; ela fica ativa durante a instalação"
sudo -v || exit 1
( while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done ) >/dev/null 2>&1 &

FREE_GB=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc '0-9')
[ "$FREE_GB" -ge 40 ] || { echo "Só há ${FREE_GB} GB livres; preciso de 40 GB."; exit 1; }

# ------------------------------------------------------------ 1. Pacotes
instalar_pacotes() {
  sudo apt update
  sudo apt install -y python3-venv python3-pip git cmake build-essential curl unzip \
    openjdk-17-jdk pipx flatpak lm-sensors psensor xfce4-sensors-plugin libcurl4-openssl-dev
}

# O repositório CUDA da NVIDIA também tem pacotes de DRIVER (com versões diferentes das do
# Ubuntu). Se o apt misturar os dois, o driver quebra. Esta regra bloqueia os pacotes de
# driver desse repositório: o driver vem SÓ do Ubuntu; do repositório CUDA vem só o toolkit.
bloquear_driver_do_repo_cuda() {
  sudo tee /etc/apt/preferences.d/cuda-sem-driver >/dev/null <<'PIN'
Package: nvidia-* libnvidia-* xserver-xorg-video-nvidia-* libxnvctrl*
Pin: origin developer.download.nvidia.com
Pin-Priority: -1
PIN
}

# ------------------------------------------------------------- 2. Driver
instalar_driver() {
  bloquear_driver_do_repo_cuda
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
  bloquear_driver_do_repo_cuda
  if [ ! -x "$CUDA_DIR/bin/nvcc" ]; then
    local base repo
    base=$(. /etc/os-release; echo "${UBUNTU_CODENAME:-}")
    case "$base" in
      jammy) repo=ubuntu2204 ;;
      noble) repo=ubuntu2404 ;;
      *) echo "Base Ubuntu '$base' não suportada por este instalador."; return 1 ;;
    esac
    curl -L --fail -o /tmp/cuda-keyring.deb \
      "https://developer.download.nvidia.com/compute/cuda/repos/$repo/x86_64/cuda-keyring_1.1-1_all.deb" &&
    sudo dpkg -i /tmp/cuda-keyring.deb &&
    sudo apt update &&
    sudo apt install -y cuda-toolkit-12-6   # só o toolkit; o driver fica por conta do Ubuntu
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
faster-whisper==1.2.*
piper-tts==1.8.*
kokoro-onnx==0.6.*
ddgs==9.*
nvidia-ml-py==13.*
pillow>=10
EOF
  cat > config.py <<'EOF'
"""Configuração do servidor, lida de variáveis de ambiente (arquivo .env via instalador)."""
import os
from pathlib import Path

HOME = Path.home()

# Token exigido de quem NÃO está no próprio PC (o PC local dispensa o token)
API_TOKEN = os.environ.get("LOCALAI_TOKEN", "")
# Quem chega pela VPN Tailscale (100.64.0.0/10) já foi autenticado pela sua conta: entra sem token.
# Para exigir o token também na VPN: TAILNET_SEM_TOKEN=0
TAILNET_SEM_TOKEN = os.environ.get("TAILNET_SEM_TOKEN", "1") != "0"

# Endereço do llama-server (llama.cpp), que expõe uma API compatível com a da OpenAI
LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8081")

# Endereço opcional de uma instância SearXNG; vazio = usa DuckDuckGo (biblioteca ddgs)
SEARXNG_URL = os.environ.get("SEARXNG_URL", "")

# Onde ficam os projetos recebidos para compilar
WORK_DIR = Path(os.environ.get("LOCALAI_WORK", str(HOME / "localai-work")))

# Tempo máximo de uma compilação, em segundos
BUILD_TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", "1200"))

# Tamanho máximo do projeto enviado, em MB
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))

# Voz -> texto (faster-whisper, roda na CPU). Opções: tiny, base, small, medium
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")  # se não carregar, usa o "small"
WHISPER_FALLBACK = os.environ.get("WHISPER_FALLBACK", "small")
# Dica de vocabulário: faz o reconhecimento acertar termos técnicos (medido: 96% -> 99% de acerto)
STT_DICA = os.environ.get(
    "STT_DICA",
    "Conversa em português do Brasil sobre tecnologia. Termos: ESP32, Android, APK, Tailscale, Gradle, Arduino, "
    "Wi-Fi, Bluetooth, firmware, GPU, CPU, Python, Linux, Kotlin, Java.",
)

# Texto -> voz, voz FEMININA (Kokoro, pt-BR). Se os arquivos não existirem, cai no Piper (masculina).
# Vozes pt-BR do Kokoro: pf_dora (feminina), pm_alex e pm_santa (masculinas)
KOKORO_MODEL = Path(os.environ.get("KOKORO_MODEL", str(HOME / "models" / "kokoro-v1.0.onnx")))
KOKORO_VOICES = Path(os.environ.get("KOKORO_VOICES", str(HOME / "models" / "voices-v1.0.bin")))
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "pf_dora")
TTS_SPEED = float(os.environ.get("TTS_SPEED", "1.0"))

# Texto -> voz reserva (Piper). Arquivo .onnx da voz (o .onnx.json fica ao lado)
PIPER_VOICE = Path(os.environ.get("PIPER_VOICE", str(HOME / "models" / "pt_BR-faber-medium.onnx")))

SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "Você é uma IA local que roda no computador do usuário. Responda sempre em português do Brasil, "
    "de forma clara, direta e correta. Quando a mensagem trouxer um bloco [CONTEXTO], use-o: ele tem "
    "memórias do usuário e resultados de pesquisa na web; cite os endereços das fontes usadas. "
    "Se não tiver certeza ou não souber, diga isso em vez de inventar. Responda direto ao ponto, sem sermões, "
    "sem avisos desnecessários e sem rodeios: trate o usuário como um adulto capaz.",
)

# Banco da memória de longo prazo (o que o usuário ensina e o que a IA aprende)
MEMORY_DB = Path(os.environ.get("MEMORY_DB", str(HOME / "localai" / "memory.db")))

# ---- Criação de apps Android pela IA ----
# Onde ficam os apps criados (APK + código) e o Gradle usado para compilar
APPS_DIR = Path(os.environ.get("APPS_DIR", str(HOME / "localai" / "apps")))
GRADLE_BIN = os.environ.get("GRADLE_BIN", str(HOME / "gradle" / "gradle-8.7" / "bin" / "gradle"))
# Quantas vezes a IA tenta corrigir o código quando a compilação falha
MAX_FIX_ATTEMPTS = int(os.environ.get("MAX_FIX_ATTEMPTS", "2"))
# Limite de palavras (tokens) que a IA pode escrever por tentativa
GEN_MAX_TOKENS = int(os.environ.get("GEN_MAX_TOKENS", "2500"))

# arduino-cli (compila firmware ESP32)
ARDUINO_CLI = os.environ.get("ARDUINO_CLI", str(HOME / "bin" / "arduino-cli"))

# ---- Desempenho e precisão ----
MODELS_DIR = Path(os.environ.get("MODELS_DIR", str(HOME / "models")))
MODELO_ENV = Path(os.environ.get("MODELO_ENV", str(HOME / "localai" / "modelo.env")))   # perfil escolhido na tela (o start_llm.sh lê este arquivo)

# ---- Anexos, imagens e recursos ----
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", str(HOME / "localai" / "uploads")))
OCIOSO_MIN = int(os.environ.get("OCIOSO_MIN", "10"))   # minutos parado até descarregar um modelo de voz da RAM

# Gerador de imagens (stable-diffusion.cpp): SD-Turbo quantizado (~2 GB), 1 a 4 passos
IMG_MODEL = Path(os.environ.get("IMG_MODEL", str(HOME / "models" / "imagens" / "sd_turbo-f16-q8_0.gguf")))
IMG_URL = "https://huggingface.co/Green-Sky/SD-Turbo-GGUF/resolve/main/sd_turbo-f16-q8_0.gguf"
IMG_TAM = 2023745376

# ---- Biblioteca local (documentação e código de referência; fica no disco grande via link ~/biblioteca) ----
BIBLIOTECA_DIR = Path(os.environ.get("BIBLIOTECA_DIR", str(HOME / "biblioteca")))
EOF
  cat > main.py <<'EOF'
"""Servidor do PC: conversa com o modelo, pesquisa na web e compila projetos Android.

Rotas:
  GET  /         -> tela de conversa (texto e voz) para usar no navegador do PC
  GET  /health   -> testa se o servidor está no ar (sem senha)
  POST /chat     -> repassa a conversa ao llama-server (streaming), com pesquisa web opcional
  POST /stt      -> voz -> texto (faster-whisper, local)
  POST /tts      -> texto -> voz (Piper, local), devolve WAV
  GET/POST /memory, DELETE /memory/{id} -> memória de longo prazo (o que a IA aprendeu)
  POST /search   -> pesquisa na web (SearXNG ou DuckDuckGo)
  POST /fetch    -> baixa uma página e devolve o texto
  POST /app/generate -> a IA cria um app Android a partir de uma descrição (streaming de progresso)
  GET  /apps, GET /apk/{id} -> lista e baixa os apps criados
  POST /build    -> recebe um .zip de projeto Gradle e devolve o APK debug

Conexões vindas do próprio PC (127.0.0.1) não precisam de token; as de fora precisam.
"""
import asyncio
import ipaddress
from contextlib import asynccontextmanager
import hmac
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid
import wave
import zipfile
from datetime import date
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel

import androidgen
import biblioteca
import codegen
import config
import entregas
import esp32gen
import imagens
import memory
import perfis
import recursos
import telemetria
import uploads

@asynccontextmanager
async def ciclo_de_vida(_app):
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(uploads.limpa_antigos)
    vigia = asyncio.create_task(recursos.vigia())   # descarrega da RAM os modelos de voz parados
    indexador = asyncio.create_task(_indexa_biblioteca())
    yield
    vigia.cancel()
    indexador.cancel()


async def _indexa_biblioteca() -> None:
    """Mantém o índice da biblioteca em dia (devagar e em segundo plano; os downloads novos entram sozinhos)."""
    await asyncio.sleep(45)  # deixa o servidor e o modelo subirem primeiro
    while True:
        try:
            await asyncio.to_thread(biblioteca.indexar)
        except Exception:
            pass
        await asyncio.sleep(20 * 60)


app = FastAPI(title="Local AI Server", lifespan=ciclo_de_vida)
config.WORK_DIR.mkdir(parents=True, exist_ok=True)
config.APPS_DIR.mkdir(parents=True, exist_ok=True)
config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _token_ok(candidato: str) -> bool:
    return bool(candidato) and bool(config.API_TOKEN) and hmac.compare_digest(candidato, config.API_TOKEN)


_TAILNET = [ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48")]


def _na_tailnet(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in rede for rede in _TAILNET)


def require_token(request: Request, authorization: str = Header(default="")) -> None:
    """O próprio PC (127.0.0.1) entra sem senha; qualquer outro precisa do token,
    no cabeçalho Authorization (Bearer) ou no cookie 'localai_token'."""
    if request.client and request.client.host in LOCAL_HOSTS:
        return
    if config.TAILNET_SEM_TOKEN and request.client and _na_tailnet(request.client.host):
        return
    if not config.API_TOKEN:
        raise HTTPException(500, "LOCALAI_TOKEN não configurado no servidor")
    bearer = authorization[7:].strip() if authorization.startswith("Bearer ") else ""
    if _token_ok(bearer) or _token_ok(request.cookies.get("localai_token", "")):
        return
    raise HTTPException(401, "Token inválido")


@app.get("/")
def index(request: Request, token: str = ""):
    # Abrir /?token=XXXX (o app do celular faz isso) grava o token num cookie e limpa o endereço
    if token:
        if _token_ok(token):
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie("localai_token", token, max_age=60 * 60 * 24 * 365, httponly=True, samesite="strict")
            return resp
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


def _tailscale() -> dict:
    """Estado do Tailscale: {'estado': 'Running'|'NeedsLogin'|'Stopped'|'ausente', 'ips': [...], 'dns': 'nome'}."""
    try:
        r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0 and not r.stdout.strip():
            return {"estado": "parado", "ips": [], "dns": ""}
        j = json.loads(r.stdout)
        eu = j.get("Self") or {}
        return {"estado": j.get("BackendState", "?"), "ips": [i for i in eu.get("TailscaleIPs", []) if "." in i],
                "dns": (eu.get("DNSName") or "").rstrip(".")}
    except FileNotFoundError:
        return {"estado": "ausente", "ips": [], "dns": ""}
    except Exception:
        return {"estado": "erro", "ips": [], "dns": ""}


def _ips_do_pc() -> list[str]:
    ts = _tailscale()
    ips = [f"http://{i}:8080" for i in ts["ips"]]
    if ts["dns"]:
        ips.append(f"http://{ts['dns']}:8080")
    try:  # IP na rede de casa (Wi-Fi)
        sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sk.connect(("10.255.255.255", 1))
        ips.append(f"http://{sk.getsockname()[0]}:8080")
        sk.close()
    except Exception:
        pass
    return list(dict.fromkeys(ips))


@app.get("/pair")
def pair(request: Request):
    """Dados para conectar o app do celular. SÓ responde para quem está no próprio PC."""
    if not (request.client and request.client.host in LOCAL_HOSTS):
        raise HTTPException(403, "Só disponível no próprio PC")
    return {"urls": _ips_do_pc(), "token": config.API_TOKEN, "tailscale": _tailscale()}


@app.get("/health")
def health():
    return {"ok": True}


# ----------------------------------------------------------------- pesquisa web
def _web_search_sync(query: str, limit: int) -> list[dict]:
    if config.SEARXNG_URL:
        r = httpx.get(f"{config.SEARXNG_URL}/search", params={"q": query, "format": "json"}, timeout=20)
        r.raise_for_status()
        items = r.json().get("results", [])[:limit]
        return [{"title": x.get("title"), "url": x.get("url"), "snippet": x.get("content")} for x in items]
    from ddgs import DDGS
    items = []
    for attempt in range(3):  # o DuckDuckGo às vezes devolve lista vazia; tenta de novo
        try:
            items = DDGS().text(query, max_results=limit)
        except Exception:
            if attempt == 2:
                raise
            items = []
        if items:
            break
        time.sleep(0.7)
    return [{"title": x.get("title"), "url": x.get("href"), "snippet": x.get("body")} for x in items]


class ChatRequest(BaseModel):
    messages: list[dict]
    max_tokens: int = 700
    temperature: float = 0.4   # mais baixo = respostas mais consistentes e precisas
    web: bool = False
    anexos: list[str] = []     # ids de arquivos enviados (código, .ino, análise de .apk/.bin...)


def _sse_error(msg: str) -> bytes:
    return f"data: {json.dumps({'error': msg})}\n\n".encode()


async def _build_messages(req: ChatRequest) -> tuple[list[dict], int]:
    """Monta a conversa para o modelo.

    VELOCIDADE: a mensagem de sistema é sempre a mesma (só muda a data, 1x por dia). As memórias e a
    pesquisa web vão dentro da ÚLTIMA mensagem do usuário. Assim o início da conversa fica idêntico
    entre um turno e outro, e o llama-server reaproveita o que já calculou (cache do prompt), em vez
    de reprocessar tudo a cada pergunta.
    """
    system = f"{config.SYSTEM_PROMPT}\nData de hoje: {date.today().isoformat()}."
    msgs = [{"role": "system", "content": system}] + [dict(m) for m in req.messages]
    ultimo = next((i for i in range(len(msgs) - 1, -1, -1) if msgs[i].get("role") == "user"), None)
    if ultimo is None:
        return msgs, 0
    pergunta = msgs[ultimo]["content"]
    partes = []

    mems = await asyncio.to_thread(memory.search, pergunta, 4)
    if mems:
        partes.append("Memórias (podem estar desatualizadas):\n" + "\n".join(f"- {m['text'][:500]}" for m in mems))

    for aid in req.anexos[:4]:
        ctx = uploads.contexto_do_anexo(aid)
        if ctx:
            partes.append(ctx[:9000])

    try:  # a biblioteca local vale com ou sem pesquisa na web
        lib = await asyncio.to_thread(biblioteca.contexto_chat, pergunta)
    except Exception:
        lib = ""
    if lib:
        partes.append(lib)

    n_web = 0
    if req.web:
        try:
            # poucos resultados e trechos curtos: cada palavra a mais deixa a resposta mais lenta
            results = await asyncio.to_thread(_web_search_sync, pergunta, 3)
        except Exception as e:  # sem internet, bloqueio etc.: segue sem a pesquisa
            results = []
            partes.append(f"(A pesquisa na web falhou: {type(e).__name__}.)")
        if results:
            n_web = len(results)
            partes.append("Pesquisa na web:\n" + "\n".join(
                f"[{i+1}] {r['title']} - {r['url']}\n{(r['snippet'] or '')[:260]}" for i, r in enumerate(results)))

    if req.web and n_web == 0:  # sem internet (ou sem resultados): a Wikipedia offline do disco grande ajuda
        try:
            wiki = await asyncio.to_thread(biblioteca.wikipedia, pergunta)
        except Exception:
            wiki = []
        if wiki:
            partes.append("Wikipedia (offline):\n" + "\n".join(f"- {w['titulo']}: {w['texto']}" for w in wiki))

    if partes:
        msgs[ultimo]["content"] = "[CONTEXTO]\n" + "\n\n".join(partes) + "\n[FIM DO CONTEXTO]\n\n" + pergunta
    return msgs, n_web


@app.post("/chat", dependencies=[Depends(require_token)])
async def chat(req: ChatRequest):
    messages, n_web = await _build_messages(req)
    payload = {"messages": messages, "max_tokens": req.max_tokens, "temperature": req.temperature,
               "top_p": 0.9, "repeat_penalty": 1.05, "cache_prompt": True, "stream": True}

    async def stream():
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                async with client.stream(
                    "POST", f"{config.LLAMA_URL}/v1/chat/completions", json=payload
                ) as r:
                    if r.status_code == 503:
                        yield _sse_error("O modelo ainda está carregando na memória. Espere um pouco e tente de novo.")
                        return
                    if r.status_code != 200:
                        yield _sse_error(f"O modelo respondeu com erro {r.status_code}.")
                        return
                    async for chunk in r.aiter_raw():
                        yield chunk
            except httpx.ConnectError:
                yield _sse_error("O modelo (llama-server) está desligado ou reiniciando. Espere carregar e tente de novo.")
            except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout):
                yield _sse_error(
                    "O modelo parou no meio da resposta (provável falta de memória da placa de vídeo). "
                    "Ele reinicia sozinho; espere carregar e tente de novo."
                )

    # X-Web-Results: quantos resultados da web foram usados (0 = a pesquisa falhou ou não foi pedida)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Web-Results": str(n_web)})


# ------------------------------------------------- modelo de linguagem e telemetria
class PerfilReq(BaseModel):
    id: str


@app.get("/llm", dependencies=[Depends(require_token)])
async def llm_info():
    return {"atual": perfis.atual(), "estado": await perfis.estado_llm(), "perfis": perfis.lista()}


@app.post("/llm/select", dependencies=[Depends(require_token)])
async def llm_select(req: PerfilReq):
    try:
        await asyncio.to_thread(perfis.seleciona, req.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return {"ok": True}


@app.post("/llm/download", dependencies=[Depends(require_token)])
async def llm_download(req: PerfilReq):
    try:
        perfis.baixar(req.id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/telemetry", dependencies=[Depends(require_token)])
async def telemetry():
    dado = dict(await asyncio.to_thread(telemetria.ler))
    try:  # estado do modelo de linguagem (para o painel em tempo real do app)
        async with httpx.AsyncClient(timeout=0.8) as c:
            r = await c.get(f"{config.LLAMA_URL}/health")
        dado["llm"] = "ok" if r.status_code == 200 else "carregando"
    except Exception:
        dado["llm"] = "off"
    return dado


# ------------------------------------------- criar apps / firmware / arquivos
def _stream_job(rodar):
    """Roda uma criação (apps, firmware) e manda o progresso como eventos SSE.
    Se o cliente desconectar (botão Parar), cancela a IA e a compilação."""
    async def stream():
        fila: asyncio.Queue = asyncio.Queue()

        async def emit(ev: dict):
            await fila.put(ev)

        async def roda():
            try:
                await rodar(emit)
            except Exception as e:  # nada deve derrubar a conexão sem avisar
                await fila.put({"type": "error", "msg": f"Erro inesperado: {type(e).__name__}: {e}"})
            finally:
                await fila.put(None)

        tarefa = asyncio.create_task(roda())
        try:
            while (ev := await fila.get()) is not None:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
        finally:
            tarefa.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream")


class AppRequest(BaseModel):
    description: str = ""
    board: str = "esp32"
    base_entrega: str | None = None   # modificar algo que a IA já criou
    base_upload: str | None = None    # modificar/compilar um arquivo anexado
    avancado: bool = False            # app Android com vários arquivos (layouts XML, AndroidX)
    alvo: str = "web"                 # /codigo/generate: web | python
    tamanho: str = "512x512"          # /imagem/generate
    passos: int = 4
    forca: float = 0.6
    melhorar: bool = True


def _base_de(req: AppRequest) -> dict:
    """Resolve o 'código base' de um pedido de modificação: {'codigo','arquivos','nome','modo'}."""
    base = {"codigo": None, "arquivos": None, "nome": None, "modo": None}
    if req.base_entrega:
        m = entregas.meta(req.base_entrega)
        if not m:
            raise HTTPException(404, "Não achei o item que você quer modificar.")
        base.update(codigo=m.get("code"), arquivos=m.get("arquivos_fonte"), nome=m.get("name"), modo=m.get("modo"))
    elif req.base_upload:
        m = uploads.meta(req.base_upload)
        if not m:
            raise HTTPException(404, "Não achei o arquivo anexado. Anexe de novo.")
        t = uploads.texto(req.base_upload, 60000)
        if t and m["tipo"] in ("ino", "texto"):
            base.update(codigo=t, arquivos={m["name"]: t}, nome=Path(m["name"]).stem)
    return base


async def _com_referencias(desc: str) -> str:
    """Junta ao pedido alguns exemplos/documentação da biblioteca local (se houver e se casarem com o pedido)."""
    if not desc:
        return desc
    try:
        return desc + await asyncio.to_thread(biblioteca.referencias, desc)
    except Exception:
        return desc


@app.get("/biblioteca", dependencies=[Depends(require_token)])
async def biblioteca_status():
    return await asyncio.to_thread(biblioteca.status)


@app.post("/biblioteca/indexar", dependencies=[Depends(require_token)])
async def biblioteca_indexar(forcar: bool = False):
    if biblioteca._estado["rodando"]:
        return {"ok": True, "ja_rodando": True}
    threading.Thread(target=biblioteca.indexar, args=(forcar,), daemon=True).start()
    return {"ok": True}


@app.get("/biblioteca/buscar", dependencies=[Depends(require_token)])
async def biblioteca_buscar(q: str):
    achados = await asyncio.to_thread(biblioteca.busca, q, 5)
    wiki = await asyncio.to_thread(biblioteca.wikipedia, q)
    return {"trechos": [{"fonte": a["fonte"], "caminho": a["caminho"], "texto": a["texto"][:700]} for a in achados],
            "wikipedia": wiki}


@app.post("/app/generate", dependencies=[Depends(require_token)])
async def app_generate(req: AppRequest):
    desc = req.description.strip()[:1500]
    base = _base_de(req)
    if not desc:
        raise HTTPException(400, "Descreva o app que você quer.")
    desc = await _com_referencias(desc)
    if req.avancado or base["modo"] == "avancado":
        return _stream_job(lambda emit: codegen.gera_app_avancado(desc, base["arquivos"], base["nome"], emit))
    return _stream_job(lambda emit: androidgen.gera_app(desc, emit, base["codigo"], base["nome"]))


@app.post("/esp32/generate", dependencies=[Depends(require_token)])
async def esp32_generate(req: AppRequest):
    desc = req.description.strip()[:1500]
    base = _base_de(req)
    if not desc and not base["codigo"]:
        raise HTTPException(400, "Descreva o firmware que você quer, ou anexe um .ino para compilar.")
    desc = await _com_referencias(desc)
    return _stream_job(lambda emit: esp32gen.gera_firmware(desc, req.board, emit, base["codigo"], base["nome"]))


@app.post("/codigo/generate", dependencies=[Depends(require_token)])
async def codigo_generate(req: AppRequest):
    desc = req.description.strip()[:1500]
    if not desc:
        raise HTTPException(400, "Descreva o que você quer criar.")
    base = _base_de(req)
    desc = await _com_referencias(desc)
    return _stream_job(lambda emit: codegen.gera_codigo(req.alvo, desc, base["arquivos"], base["nome"], emit))


@app.post("/imagem/generate", dependencies=[Depends(require_token)])
async def imagem_generate(req: AppRequest):
    desc = req.description.strip()[:1200]
    if not desc:
        raise HTTPException(400, "Descreva a imagem que você quer.")
    init = req.base_upload
    if req.base_entrega:  # modificar uma imagem que a IA criou: copia para a pasta de anexos
        m = entregas.meta(req.base_entrega)
        arq = entregas.caminho(req.base_entrega, m["files"][0]["name"]) if m and m.get("files") else None
        if arq is None:
            raise HTTPException(404, "Não achei a imagem que você quer modificar.")
        init = uploads.salva(arq.name, shutil.copy(arq, config.WORK_DIR / f"copia-{arq.name}"))["id"]
    return _stream_job(lambda emit: imagens.gera_imagem(desc, init, req.tamanho, req.passos, req.forca, req.melhorar, emit))


@app.get("/imagem/modelo", dependencies=[Depends(require_token)])
async def imagem_modelo():
    return imagens.estado()


@app.post("/imagem/modelo", dependencies=[Depends(require_token)])
async def imagem_modelo_baixar():
    try:
        imagens.baixar()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ----------------------------------------------------------------------------- anexos
@app.post("/upload", dependencies=[Depends(require_token)])
async def upload(arquivo: UploadFile = File(...)):
    nome = arquivo.filename or "arquivo"
    if uploads.tipo_de(nome) is None:
        raise HTTPException(400, "Tipo de arquivo não aceito. Aceito: imagens, .ino, .bin, .apk e arquivos de código/texto.")
    tmp = config.WORK_DIR / f"up-{uuid.uuid4().hex[:8]}"
    tam = 0
    with tmp.open("wb") as f:
        while pedaco := await arquivo.read(1024 * 1024):
            tam += len(pedaco)
            if tam > config.MAX_UPLOAD_MB * 1024 * 1024:
                f.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"Arquivo grande demais (máximo {config.MAX_UPLOAD_MB} MB).")
            f.write(pedaco)
    try:
        meta = await asyncio.to_thread(uploads.salva, nome, tmp)
    except ValueError as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    # APK criado por esta IA? então dá para modificar pelo código-fonte
    pacote = (meta["analise"] or {}).get("pacote")
    entrega = uploads.encontra_app_por_pacote(pacote) if pacote else None
    return {**meta, "entrega": entrega}


@app.get("/uploads/{up_id}/{nome}", dependencies=[Depends(require_token)])
async def upload_baixa(up_id: str, nome: str):
    m = uploads.meta(up_id)
    p = uploads.caminho(up_id)
    if not m or p is None or nome != m["name"]:
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(p, filename=nome)


@app.delete("/upload/{up_id}", dependencies=[Depends(require_token)])
async def upload_apaga(up_id: str):
    await asyncio.to_thread(uploads.apaga, up_id)
    return {"ok": True}


@app.get("/boards", dependencies=[Depends(require_token)])
async def boards():
    return [{"id": k, "label": v[0]} for k, v in esp32gen.PLACAS.items()]


@app.get("/files", dependencies=[Depends(require_token)])
async def files_list():
    return await asyncio.to_thread(entregas.lista)


@app.get("/files/{item_id}/{nome}", dependencies=[Depends(require_token)])
async def files_download(item_id: str, nome: str):
    f = entregas.caminho(item_id, nome)
    if f is None:
        raise HTTPException(404, "Arquivo não encontrado")
    return FileResponse(f, media_type=entregas.tipo_mime(nome), filename=nome)


# ------------------------------------------------------------------- memória
class MemoryIn(BaseModel):
    text: str
    source: str = "usuario"


@app.get("/memory", dependencies=[Depends(require_token)])
async def memory_list():
    return await asyncio.to_thread(memory.list_all)


@app.post("/memory", dependencies=[Depends(require_token)])
async def memory_add(item: MemoryIn):
    try:
        return await asyncio.to_thread(memory.add, item.text, item.source[:20])
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/memory/{mem_id}", dependencies=[Depends(require_token)])
async def memory_delete(mem_id: int):
    await asyncio.to_thread(memory.delete, mem_id)
    return {"ok": True}


# ------------------------------------------------------------------ voz (local)
_whisper = None
_piper = None
_kokoro = None
_voice_lock = threading.Lock()


def _descarrega_whisper():
    global _whisper
    if _voice_lock.acquire(blocking=False):   # se está em uso agora, deixa para depois
        try:
            _whisper = None
        finally:
            _voice_lock.release()


def _descarrega_kokoro():
    global _kokoro
    if _voice_lock.acquire(blocking=False):
        try:
            _kokoro = None
        finally:
            _voice_lock.release()


def _carrega_whisper():
    from faster_whisper import WhisperModel
    nucleos = max(2, (os.cpu_count() or 4) // 2)  # núcleos físicos: rende mais que usar todos os threads
    try:
        return WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8", cpu_threads=nucleos)
    except Exception:  # sem o modelo grande (não baixou / pouca memória): usa o pequeno
        return WhisperModel(config.WHISPER_FALLBACK, device="cpu", compute_type="int8", cpu_threads=nucleos)


def _stt_sync(path: str) -> str:
    global _whisper
    with _voice_lock:
        if _whisper is None:
            _whisper = _carrega_whisper()
            recursos.registra("whisper", _descarrega_whisper)
        recursos.usou("whisper")
        segments, _ = _whisper.transcribe(
            path, language="pt", beam_size=5, vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            initial_prompt=config.STT_DICA, condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
        recursos.registra("kokoro", _descarrega_kokoro)
    recursos.usou("kokoro")
    return _kokoro


def _tts_kokoro_sync(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    import numpy as np
    k = _get_kokoro()
    if voice not in k.get_voices():
        voice = config.KOKORO_VOICE
    speed = min(max(speed or config.TTS_SPEED, 0.6), 1.5)
    samples, rate = k.create(text, voice=voice, speed=speed, lang="pt-br")
    pcm = (np.clip(samples, -1, 1) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _tts_sync(text: str, voice: str | None = None, speed: float | None = None) -> bytes:
    global _piper
    with _voice_lock:
        if config.KOKORO_MODEL.exists() and config.KOKORO_VOICES.exists():
            try:
                return _tts_kokoro_sync(text, voice, speed)
            except Exception:  # se o Kokoro falhar, usa a voz reserva em vez de ficar mudo
                pass
        if _piper is None:
            if not config.PIPER_VOICE.exists():
                raise FileNotFoundError(f"Voz não encontrada: {config.PIPER_VOICE}")
            from piper import PiperVoice
            _piper = PiperVoice.load(str(config.PIPER_VOICE))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            _piper.synthesize_wav(text, w)
        return buf.getvalue()


@app.post("/stt", dependencies=[Depends(require_token)])
async def stt(audio: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(await audio.read())
        path = f.name
    try:
        text = await asyncio.to_thread(_stt_sync, path)
    finally:
        Path(path).unlink(missing_ok=True)
    return {"text": text}


class TtsRequest(BaseModel):
    text: str
    voice: str | None = None
    speed: float | None = None


# Vozes oferecidas na tela. As de outros idiomas leem português com sotaque.
VOZES_PT = {
    "pf_dora": "Dora: feminina, português do Brasil (recomendada)",
    "pm_alex": "Alex: masculina, português do Brasil",
    "pm_santa": "Santa: masculina, português do Brasil",
}
VOZES_FEMININAS_OUTRAS = {
    "af_heart": "Heart (americana)", "af_bella": "Bella (americana)", "af_nicole": "Nicole (americana, suave)",
    "af_sarah": "Sarah (americana)", "af_sky": "Sky (americana)", "af_alloy": "Alloy (americana)",
    "af_nova": "Nova (americana)", "af_kore": "Kore (americana)", "bf_emma": "Emma (britânica)",
    "bf_isabella": "Isabella (britânica)", "bf_alice": "Alice (britânica)", "ef_dora": "Dora (espanhola)",
    "ff_siwis": "Siwis (francesa)", "if_sara": "Sara (italiana)",
}


def _voices_sync() -> list[dict]:
    if not (config.KOKORO_MODEL.exists() and config.KOKORO_VOICES.exists()):
        return [{"id": "", "label": "Faber: masculina (voz reserva)", "group": "Reserva"}]
    with _voice_lock:
        disponiveis = set(_get_kokoro().get_voices())
    out = [{"id": v, "label": n, "group": "Português do Brasil"} for v, n in VOZES_PT.items() if v in disponiveis]
    out += [{"id": v, "label": n, "group": "Femininas de outros idiomas (leem com sotaque)"}
            for v, n in VOZES_FEMININAS_OUTRAS.items() if v in disponiveis]
    return out


@app.get("/voices", dependencies=[Depends(require_token)])
async def voices():
    return await asyncio.to_thread(_voices_sync)


@app.post("/tts", dependencies=[Depends(require_token)])
async def tts(req: TtsRequest):
    text = req.text.strip()[:2000]
    if not text:
        raise HTTPException(400, "Texto vazio")
    try:
        wav = await asyncio.to_thread(_tts_sync, text, req.voice, req.speed)
    except FileNotFoundError as e:
        raise HTTPException(503, str(e))
    return Response(wav, media_type="audio/wav")


class SearchRequest(BaseModel):
    query: str
    limit: int = 5


@app.post("/search", dependencies=[Depends(require_token)])
async def search(req: SearchRequest):
    try:
        return await asyncio.to_thread(_web_search_sync, req.query, req.limit)
    except Exception as e:
        raise HTTPException(502, f"Falha na pesquisa: {type(e).__name__}")


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
    if roots:
        root = min(roots, key=lambda p: len(p.parts))
        (root / "gradlew").chmod(0o755)
        cmd = ["./gradlew"]
    else:  # sem wrapper: usa o Gradle instalado no servidor
        sets = [p.parent for p in src.rglob("settings.gradle*")]
        if not sets:
            shutil.rmtree(job, ignore_errors=True)
            raise HTTPException(400, "Não achei gradlew nem settings.gradle no projeto")
        root = min(sets, key=lambda p: len(p.parts))
        cmd = [config.GRADLE_BIN]

    try:
        proc = subprocess.run(
            cmd + ["assembleDebug", "--no-daemon", "--console=plain"],
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
  cat > memory.py <<'EOF'
"""Memória de longo prazo: guarda o que o usuário ensina e o que a IA aprende na web.

Usa SQLite com busca de texto (FTS5), sem modelos extras: leve para CPU e RAM.
O modelo em si não muda; ele recebe as memórias relevantes junto com cada pergunta.
"""
import re
import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_db: sqlite3.Connection | None = None

STOP = set("""
a o as os um uma uns umas de do da dos das em no na nos nas por para com sem sob sobre e ou mas que se
como qual quais quem onde quando porque pra pro ao aos isso isto esse essa esses essas este esta estes
estas ele ela eles elas eu voce você nos nós meu minha meus minhas seu sua seus suas foi ser sao são
tem ter era tinha vai vou ja já mais muito muita tambem também so só me te lhe nao não sim favor
""".split())


def _conn() -> sqlite3.Connection:
    global _db
    if _db is None:
        config.MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(config.MEMORY_DB, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute(
            "CREATE TABLE IF NOT EXISTS memories("
            "id INTEGER PRIMARY KEY, text TEXT NOT NULL UNIQUE, source TEXT NOT NULL, created REAL NOT NULL)"
        )
        _db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
            "text, tokenize='unicode61 remove_diacritics 2')"
        )
        _db.commit()
    return _db


def add(text: str, source: str = "usuario") -> dict:
    text = text.strip()[:1500]
    if not text:
        raise ValueError("texto vazio")
    with _lock:
        db = _conn()
        row = db.execute("SELECT id FROM memories WHERE text = ?", (text,)).fetchone()
        if row:
            return {"id": row["id"], "duplicate": True}
        cur = db.execute(
            "INSERT INTO memories(text, source, created) VALUES (?, ?, ?)", (text, source, time.time())
        )
        db.execute("INSERT INTO memories_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
        db.commit()
        return {"id": cur.lastrowid, "duplicate": False}


def delete(mem_id: int) -> None:
    with _lock:
        db = _conn()
        db.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        db.execute("DELETE FROM memories_fts WHERE rowid = ?", (mem_id,))
        db.commit()


def list_all(limit: int = 300) -> list[dict]:
    with _lock:
        rows = _conn().execute(
            "SELECT id, text, source, created FROM memories ORDER BY created DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def _fts_query(text: str) -> str:
    words = [w for w in re.findall(r"\w{3,}", text.lower()) if w not in STOP]
    terms = []
    for w in dict.fromkeys(words):  # sem repetir, mantendo a ordem
        # corta palavras longas e usa prefixo: "cachorros" encontra "cachorro"
        stem = w[:5] if len(w) > 6 else w
        terms.append(f'"{stem}"*' if len(w) > 6 else f'"{stem}"')
    return " OR ".join(terms[:10])


def search(text: str, limit: int = 4) -> list[dict]:
    q = _fts_query(text)
    if not q:
        return []
    with _lock:
        rows = _conn().execute(
            "SELECT m.id, m.text, m.source FROM memories_fts f JOIN memories m ON m.id = f.rowid "
            "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
            (q, limit),
        ).fetchall()
    return [dict(r) for r in rows]
EOF
  cat > androidgen.py <<'EOF'
"""Cria aplicativos Android a partir de uma descrição.

Fluxo: a IA escreve UM arquivo (MainActivity.java) -> o servidor monta o projeto Gradle
em volta dele -> compila -> se der erro, devolve o erro à IA para corrigir (algumas vezes).

Por que um arquivo só e sem bibliotecas: o modelo local escreve poucas palavras por
segundo, então a estrutura fixa (Gradle, manifesto) vem de um modelo pronto e a IA escreve
só a lógica. A interface é montada por código (sem XML) e usa só classes do Android.
"""
import asyncio
import json
import os
import re
import shutil
import signal
import time
import uuid
from pathlib import Path

import httpx

import config
import entregas
import recursos

PACKAGE = "com.localai.app"

SYSTEM_PROMPT = """Você é um programador Android experiente. Escreva UM aplicativo Android completo em UM único arquivo Java.

REGRAS OBRIGATÓRIAS:
1. Primeira linha do arquivo: package com.localai.app;
2. Classe principal: public class MainActivity extends android.app.Activity (NÃO use AppCompatActivity, NÃO use AndroidX, NÃO use Kotlin).
3. Use SOMENTE classes do Android (android.*) e do Java (java.*). Nenhuma biblioteca externa.
4. NÃO use arquivos XML nem R.layout/R.id. Monte toda a interface por código Java (LinearLayout, TextView, Button, EditText, ScrollView, ListView com ArrayAdapter, etc.) e chame setContentView(view).
5. Inclua TODOS os imports necessários. O código precisa compilar na primeira tentativa.
6. Converta dp para pixels com um método auxiliar (ex.: (int) (valor * getResources().getDisplayMetrics().density)).
7. Rede (se precisar): HttpURLConnection dentro de uma Thread e atualize a tela com runOnUiThread.
8. Todos os textos da interface em português do Brasil.
9. Classes auxiliares podem existir no mesmo arquivo (sem "public").

FORMATO DA RESPOSTA (siga exatamente, sem explicações):
NOME: <nome curto do app>
```java
<código completo>
```"""

EXEMPLO_PEDIDO = "um contador com botões de mais e menos"
EXEMPLO_RESPOSTA = """NOME: Contador
```java
package com.localai.app;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    private int valor = 0;

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LinearLayout raiz = new LinearLayout(this);
        raiz.setOrientation(LinearLayout.VERTICAL);
        raiz.setGravity(Gravity.CENTER);
        raiz.setPadding(dp(24), dp(24), dp(24), dp(24));

        final TextView texto = new TextView(this);
        texto.setTextSize(48);
        texto.setGravity(Gravity.CENTER);
        texto.setText("0");

        Button mais = new Button(this);
        mais.setText("+1");
        mais.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                valor++;
                texto.setText(String.valueOf(valor));
            }
        });

        Button menos = new Button(this);
        menos.setText("-1");
        menos.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                valor--;
                texto.setText(String.valueOf(valor));
            }
        });

        raiz.addView(texto);
        raiz.addView(mais);
        raiz.addView(menos);
        setContentView(raiz);
    }
}
```"""

# ------------------------------------------------------------------ projeto Gradle
SETTINGS_GRADLE = """pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "app"
include ':app'
"""

ROOT_BUILD_GRADLE = """plugins {
    id 'com.android.application' version '8.5.2' apply false
}
"""

GRADLE_PROPERTIES = """org.gradle.jvmargs=-Xmx2g -Dfile.encoding=UTF-8
android.useAndroidX=false
android.nonTransitiveRClass=true
"""

APP_BUILD_GRADLE = """plugins {
    id 'com.android.application'
}

android {
    namespace 'com.localai.app'
    compileSdk 34

    defaultConfig {
        applicationId "%(app_id)s"
        minSdk 24
        targetSdk 34
        versionCode 1
        versionName "1.0"
    }

    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }

    lint {
        abortOnError false
        checkReleaseBuilds false
    }
}
"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET" />
    <application
        android:label="%(label)s"
        android:allowBackup="true"
        android:usesCleartextTraffic="true"
        android:theme="@android:style/Theme.Material.Light.DarkActionBar">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""


def slugify(nome: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return (s or "app")[:16]


def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def escreve_projeto(pasta: Path, nome: str, app_id: str, codigo: str) -> None:
    (pasta / "app/src/main/java/com/localai/app").mkdir(parents=True, exist_ok=True)
    (pasta / "settings.gradle").write_text(SETTINGS_GRADLE)
    (pasta / "build.gradle").write_text(ROOT_BUILD_GRADLE)
    (pasta / "gradle.properties").write_text(GRADLE_PROPERTIES)
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    (pasta / "local.properties").write_text(f"sdk.dir={sdk}\n")
    (pasta / "app/build.gradle").write_text(APP_BUILD_GRADLE % {"app_id": app_id})
    (pasta / "app/src/main/AndroidManifest.xml").write_text(MANIFEST % {"label": xml_escape(nome)})
    (pasta / "app/src/main/java/com/localai/app/MainActivity.java").write_text(codigo)


# ------------------------------------------------------------------ resposta da IA
def extrai_resposta(texto: str) -> tuple[str, str]:
    """Devolve (nome, codigo_java). Levanta ValueError se não achar código utilizável."""
    m = re.search(r"```(?:java)?\s*\n(.*?)(?:```|$)", texto, re.S)
    if not m:
        raise ValueError("A IA não devolveu um bloco de código.")
    codigo = m.group(1).strip()
    if "class MainActivity" not in codigo:
        raise ValueError("O código não define a classe MainActivity.")
    if not re.match(r"\s*package\s+com\.localai\.app\s*;", codigo):
        codigo = re.sub(r"^\s*package\s+[\w.]+\s*;\s*", "", codigo)
        codigo = f"package {PACKAGE};\n\n" + codigo
    n = re.search(r"NOME:\s*(.+)", texto)
    nome = (n.group(1).strip() if n else "App IA")[:40] or "App IA"
    return nome, codigo + "\n"


def resumo_erros(log: str, limite: int = 1600) -> str:
    """Pega do log do Gradle só o que ajuda a corrigir: erros do javac (com contexto)."""
    linhas = log.splitlines()
    blocos = []
    for i, ln in enumerate(linhas):
        if ": error:" in ln or re.search(r"error: ", ln):
            blocos.append("\n".join(linhas[i:i + 4]))
    if blocos:
        saida = "\n".join(blocos)
    else:
        m = re.search(r"\* What went wrong:\n(.*?)(?:\n\* |\Z)", log, re.S)
        saida = m.group(1) if m else log[-limite:]
    return saida[:limite]


# ------------------------------------------------------------------ chamadas externas
async def pede_codigo(messages: list[dict], emit) -> str:
    """Chama o llama-server em streaming e junta o texto, avisando o progresso."""
    payload = {"messages": messages, "stream": True, "temperature": 0.2, "top_p": 0.9,
               "cache_prompt": True,  # o exemplo e as regras são sempre iguais: reaproveita o cálculo
               "max_tokens": config.GEN_MAX_TOKENS}
    texto, n, ultimo = "", 0, time.time()
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            async with client.stream("POST", f"{config.LLAMA_URL}/v1/chat/completions", json=payload) as r:
                if r.status_code == 503:
                    raise RuntimeError("O modelo ainda está carregando na memória. Espere um pouco e tente de novo.")
                if r.status_code != 200:
                    raise RuntimeError(f"O modelo respondeu com erro {r.status_code}.")
                async for linha in r.aiter_lines():
                    if not linha.startswith("data:"):
                        continue
                    dado = linha[5:].strip()
                    if not dado or dado == "[DONE]":
                        continue
                    pedaco = json.loads(dado)["choices"][0]["delta"].get("content") or ""
                    texto += pedaco
                    n += 1
                    if time.time() - ultimo > 2:
                        ultimo = time.time()
                        await emit({"type": "progress", "tokens": n})
        except httpx.ConnectError:
            raise RuntimeError("O modelo (llama-server) está desligado ou reiniciando.")
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout):
            raise RuntimeError("O modelo parou no meio (provável falta de memória da placa de vídeo).")
    return texto


async def compila(pasta: Path) -> tuple[bool, str, Path | None]:
    gradle = config.GRADLE_BIN
    env = dict(os.environ)
    sdk = env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT")
    if not sdk or not Path(sdk).exists():
        return False, "Android SDK não encontrado (ANDROID_HOME). Rode o instalador.", None
    env["ANDROID_HOME"] = sdk
    # start_new_session: o Gradle e os processos que ele cria ficam num grupo próprio,
    # para podermos matar tudo de uma vez ao cancelar (senão sobra Java rodando escondido)
    proc = await asyncio.create_subprocess_exec(
        *recursos.baixa_prioridade([gradle, "assembleDebug", "--no-daemon", "--console=plain", "-q",
                                    "-Dorg.gradle.workers.max=2"]),
        cwd=pasta, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )

    def mata_tudo():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        saida, _ = await asyncio.wait_for(proc.communicate(), timeout=config.BUILD_TIMEOUT)
    except asyncio.TimeoutError:
        mata_tudo()
        return False, "A compilação passou do tempo limite.", None
    except asyncio.CancelledError:  # o usuário clicou em Parar
        mata_tudo()
        raise
    log = saida.decode(errors="replace")
    if proc.returncode != 0:
        return False, log, None
    apks = sorted(pasta.rglob("*-debug.apk"))
    if not apks:
        return False, log + "\nCompilou, mas nenhum APK foi encontrado.", None
    return True, log, apks[0]


# ------------------------------------------------------------------ fluxo principal
_trava = recursos.pesado   # uma tarefa pesada por vez (RAM/CPU compartilhadas)


async def gera_app(descricao: str, emit, base_codigo: str | None = None, base_nome: str | None = None) -> None:
    """Executa o fluxo completo, mandando eventos por emit(dict)."""
    if _trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outro app. Espere ele terminar ou clique em Parar."})
        return
    async with _trava:
        mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Crie: {EXEMPLO_PEDIDO}"},
            {"role": "assistant", "content": EXEMPLO_RESPOSTA},
            {"role": "user", "content": (
                f"Código ATUAL do app:\n```java\n{base_codigo[:14000]}\n```\n\nModifique o app conforme o pedido e "
                f"devolva o arquivo COMPLETO no mesmo formato. Pedido: {descricao}") if base_codigo
             else f"Crie: {descricao}"},
        ]
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"app-{id_}"
        try:
            for tentativa in range(config.MAX_FIX_ATTEMPTS + 1):
                if tentativa == 0:
                    await emit({"type": "status", "msg": "A IA está escrevendo o código do app… (pode levar alguns minutos)"})
                else:
                    await emit({"type": "status",
                                "msg": f"Deu erro ao compilar. A IA está corrigindo (tentativa {tentativa} de {config.MAX_FIX_ATTEMPTS})…"})
                texto = await pede_codigo(mensagens, emit)
                try:
                    nome, codigo = extrai_resposta(texto)
                    if base_nome and nome == "App IA":
                        nome = base_nome
                except ValueError as e:
                    await emit({"type": "error", "msg": str(e), "log": texto[-1500:]})
                    return
                await emit({"type": "code", "text": codigo})

                if pasta.exists():
                    shutil.rmtree(pasta)
                app_id = f"com.localai.{slugify(nome)}{id_[:4]}"
                escreve_projeto(pasta, nome, app_id, codigo)
                await emit({"type": "status", "msg": "Compilando o app… (a primeira vez baixa as ferramentas e demora mais)"})
                ok, log, apk = await compila(pasta)
                if ok:
                    meta = entregas.salva(
                        id_, nome, descricao, "apk",
                        [(apk, f"{slugify(nome)}.apk", "App Android (.apk)")],
                        {"code": codigo, "app_id": app_id})
                    shutil.rmtree(pasta, ignore_errors=True)
                    await emit({"type": "done", "id": id_, "name": nome, "kind": "apk", "files": meta["files"],
                                "note": "Passe o arquivo para o celular e abra-o para instalar. Se o Android pedir, permita instalar de fontes desconhecidas."})
                    return
                erros = resumo_erros(log)
                if tentativa >= config.MAX_FIX_ATTEMPTS:
                    await emit({"type": "error", "msg": "Não consegui compilar o app depois das correções.", "log": erros})
                    return
                mensagens += [
                    {"role": "assistant", "content": texto},
                    {"role": "user", "content": (
                        "O código NÃO compilou. ERROS DE COMPILAÇÃO:\n" + erros +
                        "\n\nCorrija e devolva o arquivo COMPLETO no mesmo formato (NOME: e bloco ```java).")},
                ]
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
EOF
  cat > entregas.py <<'EOF'
"""Arquivos entregues ao usuário (.apk, .bin...): guarda no disco e lista para download.

Cada entrega vira uma pasta em APPS_DIR/<id>/ com os arquivos e um meta.json.
"""
import json
import re
import shutil
import time
from pathlib import Path

import config

ID_RE = re.compile(r"[0-9a-f]{10}")
TIPOS = {".apk": "application/vnd.android.package-archive", ".bin": "application/octet-stream",
         ".png": "image/png", ".html": "text/html", ".py": "text/x-python", ".zip": "application/zip"}


def salva(id_: str, nome: str, descricao: str, tipo: str, arquivos: list, extra: dict | None = None) -> dict:
    """arquivos: lista de (caminho_de_origem, nome_final, rotulo)."""
    pasta = config.APPS_DIR / id_
    pasta.mkdir(parents=True, exist_ok=True)
    itens = []
    for origem, nome_final, rotulo in arquivos:
        destino = pasta / nome_final
        shutil.copy(origem, destino)
        itens.append({"name": nome_final, "label": rotulo, "size": destino.stat().st_size,
                      "url": f"/files/{id_}/{nome_final}"})
    meta = {"id": id_, "name": nome, "description": descricao, "kind": tipo,
            "created": time.time(), "files": itens}
    if extra:
        meta.update(extra)
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    return meta


def lista() -> list[dict]:
    out = []
    for f in config.APPS_DIR.glob("*/meta.json"):
        try:
            m = json.loads(f.read_text())
        except Exception:
            continue
        item = {k: m.get(k) for k in ("id", "name", "description", "kind", "created", "files")}
        item["editavel"] = bool(m.get("code") or m.get("arquivos_fonte") or m.get("kind") == "img")
        out.append(item)
    return sorted(out, key=lambda m: m["created"] or 0, reverse=True)


def meta(id_: str) -> dict | None:
    """meta.json completo de uma entrega (inclui o código-fonte guardado), ou None."""
    if not ID_RE.fullmatch(id_):
        return None
    try:
        return json.loads((config.APPS_DIR / id_ / "meta.json").read_text())
    except (OSError, ValueError):
        return None


def caminho(id_: str, nome: str) -> Path | None:
    """Caminho de um arquivo entregue, ou None. Só devolve o que está no meta.json (sem '..')."""
    if not ID_RE.fullmatch(id_):
        return None
    meta = config.APPS_DIR / id_ / "meta.json"
    if not meta.exists():
        return None
    try:
        nomes = {f["name"] for f in json.loads(meta.read_text())["files"]}
    except Exception:
        return None
    if nome not in nomes:
        return None
    p = config.APPS_DIR / id_ / nome
    return p if p.exists() else None


def tipo_mime(nome: str) -> str:
    return TIPOS.get(Path(nome).suffix.lower(), "application/octet-stream")
EOF
  cat > esp32gen.py <<'EOF'
"""Cria firmware (.bin) para ESP32 a partir de uma descrição.

Fluxo igual ao dos apps Android: a IA escreve UM sketch Arduino (.ino) -> o arduino-cli
compila -> se der erro, o erro volta à IA para corrigir -> o .bin é entregue para download.
Só usa as bibliotecas que já vêm no núcleo ESP32 do Arduino (WiFi, WebServer, Wire...).
"""
import asyncio
import os
import re
import shutil
import signal
import uuid
from pathlib import Path

import androidgen
import config
import entregas
import recursos

# id da tela -> (nome para o usuário, FQBN do arduino-cli)
PLACAS = {
    "esp32": ("ESP32 (DevKit comum)", "esp32:esp32:esp32"),
    "esp32s3": ("ESP32-S3", "esp32:esp32:esp32s3"),
    "esp32c3": ("ESP32-C3", "esp32:esp32:esp32c3"),
    "esp32s2": ("ESP32-S2", "esp32:esp32:esp32s2"),
    "esp32c6": ("ESP32-C6", "esp32:esp32:esp32c6"),
}

SYSTEM_PROMPT = """Você é um programador de firmware experiente em Arduino para ESP32. Escreva UM sketch Arduino completo em UM único arquivo .ino.

REGRAS OBRIGATÓRIAS:
1. Use SOMENTE bibliotecas que já vêm no núcleo ESP32 do Arduino (WiFi.h, WebServer.h, HTTPClient.h, Wire.h, SPI.h, Preferences.h, BluetoothSerial.h etc.). NENHUMA biblioteca externa (nada de Adafruit, FastLED, PubSubClient...).
2. Defina setup() e loop(). Inicie a serial com Serial.begin(115200).
3. Inclua TODOS os #include necessários. O código precisa compilar na primeira tentativa.
4. Para o LED da placa use o pino 2 (const int LED = 2;), a menos que o usuário peça outro.
5. Comentários e textos da serial em português do Brasil.
6. Não use delay() longos que travem o programa quando houver servidor web; prefira millis().

FORMATO DA RESPOSTA (siga exatamente, sem explicações):
NOME: <nome curto do firmware>
```cpp
<código completo>
```"""

EXEMPLO_PEDIDO = "piscar o LED a cada segundo e escrever na serial"
EXEMPLO_RESPOSTA = """NOME: Pisca LED
```cpp
// Pisca o LED a cada segundo e informa na serial
const int LED = 2;
bool ligado = false;

void setup() {
  Serial.begin(115200);
  pinMode(LED, OUTPUT);
  Serial.println("Pisca LED iniciado");
}

void loop() {
  ligado = !ligado;
  digitalWrite(LED, ligado ? HIGH : LOW);
  Serial.println(ligado ? "LED ligado" : "LED desligado");
  delay(1000);
}
```"""


def extrai_resposta(texto: str) -> tuple[str, str]:
    m = re.search(r"```(?:cpp|c\+\+|arduino|ino|c)?\s*\n(.*?)(?:```|$)", texto, re.S)
    if not m:
        raise ValueError("A IA não devolveu um bloco de código.")
    codigo = m.group(1).strip()
    if "void setup" not in codigo or "void loop" not in codigo:
        raise ValueError("O código não tem as funções setup() e loop().")
    n = re.search(r"NOME:\s*(.+)", texto)
    nome = (n.group(1).strip() if n else "Firmware IA")[:40] or "Firmware IA"
    return nome, codigo + "\n"


async def compila(pasta: Path, fqbn: str) -> tuple[bool, str, Path]:
    saida_dir = pasta / "out"
    cli = config.ARDUINO_CLI
    env = dict(os.environ)
    proc = await asyncio.create_subprocess_exec(
        *recursos.baixa_prioridade([cli, "compile", "--fqbn", fqbn, "--output-dir", str(saida_dir), str(pasta / "sketch")]),
        cwd=pasta, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )

    def mata_tudo():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        saida, _ = await asyncio.wait_for(proc.communicate(), timeout=config.BUILD_TIMEOUT)
    except asyncio.TimeoutError:
        mata_tudo()
        return False, "A compilação passou do tempo limite.", saida_dir
    except asyncio.CancelledError:
        mata_tudo()
        raise
    return proc.returncode == 0, saida.decode(errors="replace"), saida_dir


async def gera_firmware(descricao: str, placa: str, emit, base_codigo: str | None = None,
                        base_nome: str | None = None) -> None:
    if placa not in PLACAS:
        await emit({"type": "error", "msg": "Placa desconhecida."})
        return
    nome_placa, fqbn = PLACAS[placa]
    if not Path(config.ARDUINO_CLI).exists():
        await emit({"type": "error", "msg": "O arduino-cli não está instalado. Rode: bash atualizar.sh"})
        return
    if androidgen._trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outra coisa. Espere terminar ou clique em Parar."})
        return
    async with androidgen._trava:
        try:
            recursos.garante_ram(1500, "compilar o firmware")
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
            return
        so_compilar = bool(base_codigo) and not descricao.strip()
        mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Crie: {EXEMPLO_PEDIDO}"},
            {"role": "assistant", "content": EXEMPLO_RESPOSTA},
            {"role": "user", "content": (
                f"Placa: {nome_placa}. Código ATUAL do sketch:\n```cpp\n{base_codigo[:14000]}\n```\n\n"
                f"Modifique conforme o pedido e devolva o sketch COMPLETO no mesmo formato. Pedido: {descricao or 'corrigir os erros'}")
             if base_codigo else f"Placa: {nome_placa}. Crie: {descricao}"},
        ]
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"esp-{id_}"
        try:
            for tentativa in range(config.MAX_FIX_ATTEMPTS + 1):
                if tentativa == 0:
                    await emit({"type": "status", "msg": "A IA está escrevendo o código do firmware… (pode levar alguns minutos)"})
                else:
                    await emit({"type": "status",
                                "msg": f"Deu erro ao compilar. A IA está corrigindo (tentativa {tentativa} de {config.MAX_FIX_ATTEMPTS})…"})
                if tentativa == 0 and so_compilar:   # sem pedido de mudança: compila o sketch como está
                    nome, codigo = base_nome or "Firmware", base_codigo
                    texto = f"NOME: {nome}\n```cpp\n{codigo}\n```"
                    await emit({"type": "status", "msg": "Compilando o seu sketch como ele está…"})
                else:
                    texto = await androidgen.pede_codigo(mensagens, emit)
                    try:
                        nome, codigo = extrai_resposta(texto)
                    except ValueError as e:
                        await emit({"type": "error", "msg": str(e), "log": texto[-1500:]})
                        return
                await emit({"type": "code", "text": codigo})

                if pasta.exists():
                    shutil.rmtree(pasta)
                (pasta / "sketch").mkdir(parents=True)
                (pasta / "sketch" / "sketch.ino").write_text(codigo)
                await emit({"type": "status", "msg": f"Compilando para {nome_placa}… (a primeira vez é mais lenta)"})
                ok, log, saida = await compila(pasta, fqbn)
                if ok:
                    slug = androidgen.slugify(nome)
                    arquivos = []
                    merged = next(iter(sorted(saida.glob("*.merged.bin"))), None)
                    app = next((p for p in sorted(saida.glob("*.ino.bin"))), None)
                    if merged:
                        arquivos.append((merged, f"{slug}-completo.bin", "Firmware completo (.bin): gravar no endereço 0x0"))
                    if app:
                        arquivos.append((app, f"{slug}-app.bin", "Só o aplicativo (.bin): para atualização OTA / endereço 0x10000"))
                    if not arquivos:
                        await emit({"type": "error", "msg": "Compilou, mas não encontrei o arquivo .bin.", "log": log[-1500:]})
                        return
                    meta = entregas.salva(id_, nome, descricao, "bin", arquivos, {"board": nome_placa, "code": codigo})
                    await emit({"type": "done", "id": id_, "name": nome, "kind": "bin", "files": meta["files"],
                                "note": f"Placa: {nome_placa}. Grave o '-completo.bin' no endereço 0x0 com o esptool ou com um gravador web (ex.: espressif.github.io/esptool-js)."})
                    return
                erros = androidgen.resumo_erros(log)
                if tentativa >= config.MAX_FIX_ATTEMPTS:
                    await emit({"type": "error", "msg": "Não consegui compilar o firmware depois das correções.", "log": erros})
                    return
                mensagens += [
                    {"role": "assistant", "content": texto},
                    {"role": "user", "content": (
                        "O código NÃO compilou. ERROS DE COMPILAÇÃO:\n" + erros +
                        "\n\nCorrija e devolva o arquivo COMPLETO no mesmo formato (NOME: e bloco ```cpp).")},
                ]
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
EOF
  cat > perfis.py <<'EOF'
"""Perfis de modelo de linguagem: trocar entre rápido, preciso e código pela própria tela.

Cada perfil é um arquivo .gguf em ~/models. A escolha vai para ~/localai/modelo.env, que o
start_llm.sh lê ao iniciar; depois o servidor reinicia o serviço do modelo (permissão
sudo restrita a esse único comando, criada pelo atualizar.sh).
"""
import asyncio
import shutil
import subprocess
from pathlib import Path

import httpx

import config

PERFIS = {
    "rapido": dict(
        nome="Rápido", modelo="Qwen2.5 3B", arquivo="Qwen2.5-3B-Instruct-Q4_K_M.gguf",
        repo="bartowski/Qwen2.5-3B-Instruct-GGUF", ngl=16, tam=1929903264,
        nota="Responde bem mais rápido, mas erra mais. Bom para conversa leve."),
    "preciso": dict(
        nome="Preciso", modelo="Qwen2.5 7B", arquivo="Qwen2.5-7B-Instruct-Q4_K_M.gguf",
        repo="bartowski/Qwen2.5-7B-Instruct-GGUF", ngl=4, tam=4683074240,
        nota="Mais correto em conversa e raciocínio, porém mais lento."),
    "codigo": dict(
        nome="Código", modelo="Qwen2.5-Coder 7B", arquivo="Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf",
        repo="bartowski/Qwen2.5-Coder-7B-Instruct-GGUF", ngl=4, tam=4683074336,
        nota="O melhor para criar apps Android e firmware."),
}

_tarefas: dict[str, asyncio.Task] = {}
_procs: dict[str, asyncio.subprocess.Process] = {}


def caminho(p: dict) -> Path:
    return config.MODELS_DIR / p["arquivo"]


def _parte(p: dict) -> Path:
    return caminho(p).with_suffix(".gguf.part")


def presente(p: dict) -> bool:
    f = caminho(p)
    return f.exists() and f.stat().st_size >= p["tam"] * 0.999


def _le_env() -> dict:
    out = {}
    try:
        for ln in config.MODELO_ENV.read_text().splitlines():
            if "=" in ln and not ln.startswith("#"):
                k, v = ln.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def atual() -> str:
    """Id do perfil em uso ('' se for um modelo escolhido à mão)."""
    nome = Path(_le_env().get("MODEL", "")).name
    for id_, p in PERFIS.items():
        if p["arquivo"] == nome:
            return id_
    return ""


def lista() -> list[dict]:
    out = []
    for id_, p in PERFIS.items():
        parte = _parte(p)
        baixando = id_ in _tarefas and not _tarefas[id_].done()
        prog = (parte.stat().st_size / p["tam"]) if baixando and parte.exists() else 0.0
        out.append({"id": id_, "nome": p["nome"], "modelo": p["modelo"], "nota": p["nota"], "tam": p["tam"],
                    "presente": presente(p), "baixando": baixando, "progresso": round(min(prog, 1.0), 3)})
    return out


def seleciona(id_: str) -> None:
    p = PERFIS.get(id_)
    if not p:
        raise ValueError("Perfil desconhecido.")
    if not presente(p):
        raise ValueError("Esse modelo ainda não foi baixado.")
    config.MODELO_ENV.parent.mkdir(parents=True, exist_ok=True)
    config.MODELO_ENV.write_text(f"MODEL={caminho(p)}\nNGL=auto\n")
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", "restart", "localai-llm.service"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r is None or r.returncode != 0:
        raise RuntimeError("Salvei a escolha, mas não consegui reiniciar o modelo sozinho. "
                           "Rode no PC: bash atualizar.sh (ele libera essa permissão) ou sudo systemctl restart localai-llm")


async def _baixa(id_: str) -> None:
    p = PERFIS[id_]
    parte = _parte(p)
    url = f"https://huggingface.co/{p['repo']}/resolve/main/{p['arquivo']}"
    proc = await asyncio.create_subprocess_exec(
        "curl", "-L", "--fail", "-C", "-", "-o", str(parte), url,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    _procs[id_] = proc
    try:
        rc = await proc.wait()
    finally:
        _procs.pop(id_, None)
    if rc == 0 and parte.exists() and parte.stat().st_size >= p["tam"] * 0.999:
        parte.rename(caminho(p))


def baixar(id_: str) -> None:
    p = PERFIS.get(id_)
    if not p:
        raise ValueError("Perfil desconhecido.")
    if presente(p):
        return
    if id_ in _tarefas and not _tarefas[id_].done():
        return
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    livre = shutil.disk_usage(config.MODELS_DIR).free
    if livre < p["tam"] * 1.1:
        raise ValueError(f"Falta espaço no disco: preciso de {p['tam'] // 2**30 + 1} GB livres.")
    _tarefas[id_] = asyncio.get_running_loop().create_task(_baixa(id_))


async def estado_llm() -> str:
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            r = await c.get(f"{config.LLAMA_URL}/health")
        return "ok" if r.status_code == 200 else "carregando"
    except Exception:
        return "desligado"
EOF
  cat > telemetria.py <<'EOF'
"""Leituras do PC para a tela: temperaturas, ventoinhas, uso da GPU e da memória."""
import os
import subprocess

import recursos
import time
from pathlib import Path

_cache: tuple[float, dict | None] = (0.0, None)


def _num(s: str):
    try:
        return float(s)
    except ValueError:
        return None


def _gpu() -> dict | None:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,fan.speed,power.draw,name",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        t, u, mu, mt, fan, pw, nome = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",", 6)]
        return {"nome": nome, "temp": _num(t), "uso": _num(u), "mem_usada": _num(mu), "mem_total": _num(mt),
                "ventoinha": _num(fan), "watts": _num(pw)}
    except (OSError, subprocess.TimeoutExpired):
        return None


def _hwmon() -> tuple[float | None, dict]:
    """Temperatura da CPU (k10temp/coretemp) e rotação das ventoinhas (RPM) de todos os sensores."""
    temp = None
    fans = {}
    for d in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            nome = (d / "name").read_text().strip()
        except OSError:
            continue
        if nome in ("k10temp", "coretemp", "zenpower") and temp is None:
            for f in sorted(d.glob("temp*_input")):
                label = ""
                try:
                    label = (d / f.name.replace("_input", "_label")).read_text().strip()
                except OSError:
                    pass
                if label in ("Tctl", "Tdie", "Package id 0") or temp is None:
                    v = _num(f.read_text().strip())
                    if v is not None:
                        temp = v / 1000
        for f in sorted(d.glob("fan*_input")):
            v = _num(f.read_text().strip()) if f.exists() else None
            if v is not None:
                fans[f"{nome}/{f.name.split('_')[0]}"] = int(v)
    return temp, fans


_ult_cpu: tuple[int, int] | None = None


def _uso_cpu() -> float | None:
    """Uso total da CPU em % desde a leitura anterior (a primeira leitura compara com 0,3 s antes)."""
    global _ult_cpu

    def ler():
        v = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(v), v[3] + (v[4] if len(v) > 4 else 0)  # total, parado (idle+iowait)

    try:
        t, ocioso = ler()
        if _ult_cpu is None:
            time.sleep(0.3)
            _ult_cpu = (t, ocioso)
            t, ocioso = ler()
        dt, di = t - _ult_cpu[0], ocioso - _ult_cpu[1]
        _ult_cpu = (t, ocioso)
        return round(max(0.0, min(100.0, 100.0 * (dt - di) / dt)), 1) if dt > 0 else None
    except (OSError, ValueError, IndexError):
        return None


def _ram() -> dict:
    m = {}
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            k, v = ln.split(":", 1)
            m[k] = int(v.split()[0]) / 1024
    except (OSError, ValueError):
        return {}
    total, livre = m.get("MemTotal", 0), m.get("MemAvailable", 0)
    return {"total": round(total), "usada": round(total - livre)}


def _controle_ventoinha() -> str:
    """'active' se o serviço de controle automático das ventoinhas (localai-fan) está rodando."""
    try:
        r = subprocess.run(["systemctl", "is-active", "localai-fan.service"], capture_output=True, text=True, timeout=2)
        return r.stdout.strip() or "inactive"
    except (OSError, subprocess.TimeoutExpired):
        return "desconhecido"


def ler() -> dict:
    global _cache
    agora = time.time()
    if _cache[1] is not None and agora - _cache[0] < 2:
        return _cache[1]
    temp, fans = _hwmon()
    dado = {"gpu": _gpu(), "cpu": {"temp": temp, "uso": _uso_cpu(), "carga": round(os.getloadavg()[0], 2), "nucleos": os.cpu_count()},
            "ventoinhas": fans, "ram": _ram(), "controle_ventoinha": _controle_ventoinha(),
            "modelos_na_ram": sorted(recursos._modelos)}
    _cache = (agora, dado)
    return dado
EOF
  cat > fanctl.py <<'EOF'
#!/usr/bin/env python3
"""Controle automático das ventoinhas: aumenta a rotação conforme a temperatura (sob demanda).

GPU NVIDIA: lê a temperatura e ajusta a ventoinha por uma curva. Tenta primeiro pelo NVML
(sem precisar de tela) e, se a placa não aceitar, pelo nvidia-settings (precisa do Coolbits).
CPU (opcional): se a placa-mãe expõe um PWM no Linux, defina FANCTL_CPU_PWM com o caminho
(ex.: /sys/class/hwmon/hwmon3/pwm2). Veja o diagnostico_fans.sh para descobrir.

SEGURANÇA: ao parar (ou se perder a leitura da temperatura) devolve o controle ao automático
da placa. A curva nunca deixa a ventoinha abaixo do mínimo.
Roda como serviço (root): localai-fan.service. Para desligar: sudo systemctl disable --now localai-fan
"""
import glob
import os
import signal
import subprocess
import sys
import time

# (temperatura em °C, ventoinha em %). Entre os pontos, interpola.
CURVA_GPU = [(40, 30), (50, 38), (60, 50), (70, 70), (78, 90), (82, 100)]
CURVA_CPU = [(40, 30), (55, 45), (65, 65), (75, 85), (85, 100)]
INTERVALO = float(os.environ.get("FANCTL_INTERVALO", "2"))
DESCE_POR_CICLO = 2          # a rotação sobe na hora, mas desce devagar (evita "serrote")
FALHAS_PARA_RESTAURAR = 3


def interpola(curva, t):
    if t <= curva[0][0]:
        return curva[0][1]
    for (t0, v0), (t1, v1) in zip(curva, curva[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return curva[-1][1]


def alvo_gpu(temp, uso):
    """Rotação desejada. Se a GPU está muito ocupada, já sobe um pouco antes de esquentar."""
    alvo = interpola(CURVA_GPU, temp)
    if uso is not None and uso >= 50:
        alvo = max(alvo, 45)
    if uso is not None and uso >= 85:
        alvo = max(alvo, 60)
    return int(round(min(100, max(30, alvo))))


class Suavizador:
    def __init__(self):
        self.atual = None

    def passo(self, alvo):
        if self.atual is None or alvo >= self.atual:
            self.atual = alvo
        else:
            self.atual = max(alvo, self.atual - DESCE_POR_CICLO)
        return self.atual


# ----------------------------------------------------------------------------- GPU
def _smi(campo):
    r = subprocess.run(["nvidia-smi", f"--query-gpu={campo}", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, timeout=5)
    return float(r.stdout.strip().splitlines()[0])


class GpuNvidia:
    """Leitura por nvidia-smi; escrita por NVML (preferido) ou nvidia-settings (reserva)."""

    def __init__(self):
        self.metodo = None
        self.nvml = None
        self.h = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml, self.h = pynvml, pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            self.nvml = None

    def temp(self):
        return _smi("temperature.gpu")

    def uso(self):
        try:
            return _smi("utilization.gpu")
        except Exception:
            return None

    def _xauth(self):
        for c in glob.glob("/home/*/.Xauthority") + glob.glob("/var/run/lightdm/root/:0"):
            return c
        return None

    def _via_nvml(self, pct):
        self.nvml.nvmlDeviceSetFanSpeed_v2(self.h, 0, int(pct))

    def _via_settings(self, pct):
        env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
        xa = self._xauth()
        if xa:
            env["XAUTHORITY"] = xa
        r = subprocess.run(["nvidia-settings", "-a", "[gpu:0]/GPUFanControlState=1",
                            "-a", f"[fan:0]/GPUTargetFanSpeed={int(pct)}"],
                           capture_output=True, text=True, timeout=10, env=env)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:200])

    def definir(self, pct):
        tentativas = [("nvml", self._via_nvml)] if self.nvml else []
        tentativas.append(("nvidia-settings", self._via_settings))
        if self.metodo:  # já sabemos qual funciona
            tentativas = [t for t in tentativas if t[0] == self.metodo]
        erro = None
        for nome, fn in tentativas:
            try:
                fn(pct)
                if self.metodo != nome:
                    print(f"[fanctl] GPU: controle da ventoinha via {nome}", flush=True)
                self.metodo = nome
                return
            except Exception as e:  # tenta o próximo método
                erro = e
        raise RuntimeError(f"Nenhum método controla a ventoinha da GPU: {erro}")

    def restaurar(self):
        """Devolve a ventoinha ao controle automático da placa."""
        try:
            if self.metodo == "nvml" and self.nvml:
                self.nvml.nvmlDeviceSetDefaultFanSpeed_v2(self.h, 0)
            elif self.metodo == "nvidia-settings":
                env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
                xa = self._xauth()
                if xa:
                    env["XAUTHORITY"] = xa
                subprocess.run(["nvidia-settings", "-a", "[gpu:0]/GPUFanControlState=0"],
                               capture_output=True, timeout=10, env=env)
        except Exception as e:
            print(f"[fanctl] aviso ao restaurar a GPU: {e}", flush=True)
        self.metodo = None


# ----------------------------------------------------------------------------- CPU (opcional)
def temp_cpu():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            nome = open(f"{d}/name").read().strip()
            if nome in ("k10temp", "coretemp", "zenpower"):
                return int(open(f"{d}/temp1_input").read()) / 1000
        except (OSError, ValueError):
            continue
    return None


class CpuPwm:
    """Ventoinha ligada a um PWM da placa-mãe (ex.: /sys/class/hwmon/hwmon3/pwm2)."""

    def __init__(self, pwm):
        self.pwm = pwm
        self.enable = pwm + "_enable"
        self.original = None

    def definir(self, pct):
        if self.original is None:
            self.original = open(self.enable).read().strip()
            open(self.enable, "w").write("1")  # 1 = manual
        open(self.pwm, "w").write(str(int(round(255 * pct / 100))))

    def restaurar(self):
        if self.original is not None:
            try:
                open(self.enable, "w").write(self.original)
            except OSError as e:
                print(f"[fanctl] aviso ao restaurar a CPU: {e}", flush=True)
            self.original = None


# ----------------------------------------------------------------------------- laço principal
class Controlador:
    def __init__(self, gpu, cpu=None, leitura_cpu=temp_cpu):
        self.gpu, self.cpu, self.leitura_cpu = gpu, cpu, leitura_cpu
        self.sg, self.sc = Suavizador(), Suavizador()
        self.falhas = 0
        self.ativo = False

    def ciclo(self):
        """Um ciclo de leitura e ajuste. Devolve (pct_gpu, pct_cpu) aplicados."""
        try:
            t, u = self.gpu.temp(), self.gpu.uso()
            self.falhas = 0
        except Exception:
            self.falhas += 1
            if self.falhas >= FALHAS_PARA_RESTAURAR and self.ativo:
                print("[fanctl] sem leitura da temperatura: devolvendo ao automático", flush=True)
                self.parar()
            return None, None
        pg = self.sg.passo(alvo_gpu(t, u))
        self.gpu.definir(pg)
        self.ativo = True
        pc = None
        if self.cpu:
            tc = self.leitura_cpu()
            if tc is not None:
                pc = self.sc.passo(int(round(min(100, max(30, interpola(CURVA_CPU, tc))))))
                self.cpu.definir(pc)
        return pg, pc

    def parar(self):
        self.gpu.restaurar()
        if self.cpu:
            self.cpu.restaurar()
        self.sg, self.sc = Suavizador(), Suavizador()
        self.ativo = False


def main():
    gpu = GpuNvidia()
    cpu = CpuPwm(os.environ["FANCTL_CPU_PWM"]) if os.environ.get("FANCTL_CPU_PWM") else None
    ctl = Controlador(gpu, cpu)

    def sair(*_):
        ctl.parar()
        print("[fanctl] encerrado; ventoinhas devolvidas ao automático", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, sair)
    signal.signal(signal.SIGINT, sair)
    print(f"[fanctl] iniciado (CPU PWM: {'sim' if cpu else 'não'})", flush=True)
    erros = 0
    try:
        while True:
            try:
                ctl.ciclo()
                erros = 0
            except Exception as e:
                erros += 1
                print(f"[fanctl] erro: {e}", flush=True)
                if erros >= 5:  # algo está errado de forma persistente: não mexe mais
                    print("[fanctl] muitos erros seguidos; saindo e deixando em automático", flush=True)
                    break
            time.sleep(INTERVALO)
    finally:
        ctl.parar()


if __name__ == "__main__":
    main()
EOF
  cat > recursos.py <<'EOF'
"""Gerenciador de recursos: RAM, VRAM e CPU divididos entre as tarefas do servidor.

Ideias (o PC tem 16 GB de RAM, 2 GB de VRAM e 4 núcleos):
  * Só UMA tarefa pesada por vez (compilar, gerar imagem, escrever um app): elas competem
    pela mesma memória e pelos mesmos núcleos. As demais esperam na fila.
  * Modelos de voz grandes ficam na RAM só enquanto são usados: depois de alguns minutos sem
    uso eles são descarregados, e a RAM volta para o resto.
  * Programas pesados (Gradle, arduino-cli, gerador de imagens) rodam com prioridade baixa
    ("nice"), para a conversa continuar respondendo enquanto eles trabalham.
  * A escolha entre GPU, CPU ou os dois juntos depende da VRAM que está livre naquela hora.
"""
import asyncio
import gc
import os
import shutil
import subprocess
import time

import config

# Trabalhos pesados (compilar, gerar imagem, criar app/firmware): um de cada vez.
pesado = asyncio.Lock()

_cache_nucleos: int | None = None


def nucleos_fisicos() -> int:
    """Núcleos físicos (os threads extras do SMT quase não ajudam em IA, que depende da memória)."""
    global _cache_nucleos
    if _cache_nucleos is None:
        n = 0
        try:
            r = subprocess.run(["lscpu", "-p=CORE,SOCKET"], capture_output=True, text=True, timeout=3)
            n = len({ln for ln in r.stdout.splitlines() if ln and not ln.startswith("#")})
        except (OSError, subprocess.TimeoutExpired):
            pass
        _cache_nucleos = n if n >= 1 else max(1, (os.cpu_count() or 2) // 2)
    return _cache_nucleos


def ram_livre_mb() -> int:
    """RAM que dá para usar agora (inclui o cache de arquivos, que o sistema devolve quando precisa)."""
    try:
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) // 1024
    except OSError:
        pass
    return 0


def vram_livre_mb() -> int | None:
    """VRAM livre da GPU NVIDIA em MiB, ou None se não houver GPU/nvidia-smi."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return int(float(r.stdout.strip().splitlines()[0]))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def baixa_prioridade(cmd: list[str]) -> list[str]:
    """Roda o comando com prioridade baixa, se o 'nice' existir."""
    return (["nice", "-n", "10"] + cmd) if shutil.which("nice") else cmd


# ------------------------------------------------------------ modelos ociosos
_modelos: dict[str, dict] = {}


def registra(nome: str, descarrega) -> None:
    """Registra um modelo carregado e a função que o descarrega da memória."""
    _modelos[nome] = {"descarrega": descarrega, "uso": time.time()}


def usou(nome: str) -> None:
    if nome in _modelos:
        _modelos[nome]["uso"] = time.time()


def libera_ociosos(forcar: bool = False) -> list[str]:
    """Descarrega modelos que ficaram parados. Com forcar=True, descarrega todos (falta de RAM)."""
    limite = config.OCIOSO_MIN * 60
    soltos = []
    for nome, m in list(_modelos.items()):
        if forcar or time.time() - m["uso"] > limite:
            try:
                m["descarrega"]()
            finally:
                _modelos.pop(nome, None)
                soltos.append(nome)
    if soltos:
        gc.collect()
    return soltos


async def vigia() -> None:
    """Tarefa de fundo: a cada minuto descarrega o que está parado há muito tempo."""
    while True:
        await asyncio.sleep(60)
        try:
            libera_ociosos()
        except Exception:
            pass


def garante_ram(minimo_mb: int, o_que: str) -> None:
    """Confere se há RAM para uma tarefa pesada; tenta liberar modelos de voz antes de desistir."""
    if ram_livre_mb() >= minimo_mb:
        return
    libera_ociosos(forcar=True)
    livre = ram_livre_mb()
    if livre < minimo_mb:
        raise RuntimeError(
            f"Pouca memória livre para {o_que}: {livre} MB livres e preciso de uns {minimo_mb} MB. "
            "Feche programas pesados (navegador com muitas abas) e tente de novo.")


# ------------------------------------------------------------ GPU, CPU ou os dois
def modo_imagem() -> str:
    """Onde rodar o gerador de imagens, conforme a VRAM livre agora.

    gpu     : tudo na placa (precisa de ~1,5 GB livres)
    hibrido : o modelo de difusão na placa e o texto/decodificador na CPU (~0,8 GB livres)
    cpu     : tudo na CPU (a placa está ocupada pelo modelo de linguagem)
    """
    v = vram_livre_mb()
    if v is None:
        return "cpu"
    if v >= 1500:
        return "gpu"
    if v >= 800:
        return "hibrido"
    return "cpu"
EOF
  cat > uploads.py <<'EOF'
"""Anexos do usuário (imagens, .ino, .bin, .apk, código): guarda no disco e analisa."""
import glob
import json
import re
import shutil
import struct
import subprocess
import time
import uuid
from pathlib import Path

import config

IMAGENS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
CODIGO = {".py", ".java", ".kt", ".xml", ".gradle", ".html", ".htm", ".js", ".css", ".json", ".md", ".txt",
          ".c", ".cpp", ".h", ".hpp", ".ts", ".sh", ".ini", ".yaml", ".yml", ".toml", ".csv", ".cfg", ".properties"}
ID_RE = re.compile(r"[0-9a-f]{10}")
LIMITE_TEXTO = 14000   # caracteres de código que cabem no contexto do modelo

CHIPS = {0: "ESP32", 2: "ESP32-S2", 5: "ESP32-C3", 9: "ESP32-S3", 12: "ESP32-C2", 13: "ESP32-C6",
         16: "ESP32-H2", 18: "ESP32-P4", 23: "ESP32-C5"}
FLASH = {0: "1 MB", 1: "2 MB", 2: "4 MB", 3: "8 MB", 4: "16 MB", 5: "32 MB"}


def tipo_de(nome: str) -> str | None:
    ext = Path(nome).suffix.lower()
    if ext == ".ino":
        return "ino"
    if ext == ".bin":
        return "bin"
    if ext == ".apk":
        return "apk"
    if ext in IMAGENS:
        return "imagem"
    if ext in CODIGO:
        return "texto"
    return None


def nome_seguro(nome: str) -> str:
    nome = Path(nome).name
    nome = re.sub(r"[^\w.\- ]", "_", nome).strip(" .")[:80]
    return nome or "arquivo"


# ----------------------------------------------------------------------------- análises
def _ferramenta(nome: str) -> str | None:
    base = __import__("os").environ.get("ANDROID_HOME") or __import__("os").environ.get("ANDROID_SDK_ROOT") or ""
    achados = sorted(glob.glob(f"{base}/build-tools/*/{nome}"))
    return achados[-1] if achados else None


def _analisa_apk(p: Path) -> dict:
    out = {"pacote": None, "versao": None, "nome": None, "permissoes": [], "min_sdk": None, "alvo_sdk": None,
           "atividade": None, "assinatura": None}
    aapt = _ferramenta("aapt2")
    if not aapt:
        return {**out, "resumo": "Não consegui analisar: o Android SDK não está instalado no servidor."}
    r = subprocess.run([aapt, "dump", "badging", str(p)], capture_output=True, text=True, timeout=60)
    for ln in r.stdout.splitlines():
        if ln.startswith("package:"):
            m = re.search(r"name='([^']*)' versionCode='([^']*)' versionName='([^']*)'", ln)
            if m:
                out["pacote"], out["versao"] = m.group(1), f"{m.group(3)} ({m.group(2)})"
        elif ln.startswith("application-label:") and not out["nome"]:
            out["nome"] = ln.split(":", 1)[1].strip("'")
        elif ln.startswith("application:"):
            m = re.search(r"label='([^']*)'", ln)
            if m and m.group(1):
                out["nome"] = m.group(1)
        elif ln.startswith("uses-permission:"):
            m = re.search(r"name='([^']*)'", ln)
            if m:
                out["permissoes"].append(m.group(1).replace("android.permission.", ""))
        elif ln.startswith("sdkVersion:"):
            out["min_sdk"] = ln.split(":", 1)[1].strip("'")
        elif ln.startswith("targetSdkVersion:"):
            out["alvo_sdk"] = ln.split(":", 1)[1].strip("'")
        elif ln.startswith("launchable-activity:"):
            m = re.search(r"name='([^']*)'", ln)
            out["atividade"] = m.group(1) if m else None
    sg = _ferramenta("apksigner")
    if sg:
        s = subprocess.run([sg, "verify", "--print-certs", str(p)], capture_output=True, text=True, timeout=60)
        m = re.search(r"Signer #1 certificate DN: (.*)", s.stdout)
        out["assinatura"] = m.group(1) if m else ("não assinado ou inválido" if s.returncode else None)
    linhas = [f"Aplicativo Android: {out['nome'] or '(sem nome)'}", f"Pacote: {out['pacote']}",
              f"Versão: {out['versao']}", f"Android mínimo (API): {out['min_sdk']} | alvo: {out['alvo_sdk']}",
              f"Tela principal: {out['atividade']}", f"Assinado por: {out['assinatura']}",
              "Permissões: " + (", ".join(out["permissoes"]) if out["permissoes"] else "nenhuma")]
    out["resumo"] = "\n".join(linhas)
    return out


def _analisa_bin(p: Path) -> dict:
    dados = p.read_bytes()[: 0x9000 + 4096]
    tam = p.stat().st_size

    def cabecalho(off: int):
        if len(dados) < off + 24 or dados[off] != 0xE9:
            return None
        nseg, modo, fs = dados[off + 1], dados[off + 2], dados[off + 3]
        entrada = struct.unpack_from("<I", dados, off + 4)[0]
        chip = struct.unpack_from("<H", dados, off + 12)[0]
        segs, pos = [], off + 24
        for _ in range(min(nseg, 16)):
            if len(dados) < pos + 8:
                break
            addr, ln = struct.unpack_from("<II", dados, pos)
            segs.append((addr, ln))
            pos += 8 + ln
        return {"chip": CHIPS.get(chip, f"desconhecido ({chip})"), "segmentos": segs, "flash": FLASH.get(fs >> 4, "?"),
                "entrada": entrada}

    linhas = [f"Arquivo .bin de {tam / 1024:.0f} KB"]
    info: dict = {"tipo_bin": "desconhecido"}
    h0, h1 = cabecalho(0), cabecalho(0x1000)
    parts = []
    if len(dados) > 0x8000 + 32 and dados[0x8000:0x8002] == b"\xaa\x50":
        for i in range(0, 96):
            off = 0x8000 + i * 32
            if len(dados) < off + 32 or dados[off:off + 2] != b"\xaa\x50":
                break
            _, tp, sub, ofs, sz = struct.unpack_from("<2sBBII", dados, off)
            nome = dados[off + 12:off + 28].split(b"\0")[0].decode(errors="replace")
            parts.append((nome, tp, sub, ofs, sz))
    if h1 and parts:
        info["tipo_bin"] = "imagem completa de flash"
        linhas += [f"Tipo: imagem COMPLETA de flash (bootloader em 0x1000) para {h1['chip']}",
                   "Para gravar: endereço 0x0."]
    elif h0 and parts:
        info["tipo_bin"] = "imagem completa de flash"
        linhas += [f"Tipo: imagem COMPLETA de flash para {h0['chip']}", "Para gravar: endereço 0x0."]
    elif h0:
        info["tipo_bin"] = "aplicativo ou bootloader"
        linhas += [f"Tipo: imagem de aplicativo/bootloader para {h0['chip']}", f"Flash: {h0['flash']}",
                   f"Segmentos: {len(h0['segmentos'])} | entrada: 0x{h0['entrada']:08X}",
                   "Para gravar: normalmente 0x10000 (aplicativo) ou 0x1000/0x0 (bootloader)."]
    else:
        linhas += ["Não reconheci como imagem ESP32 (falta o byte mágico 0xE9). Pode ser outro tipo de binário."]
    if parts:
        linhas.append("Partições: " + "; ".join(f"{n} @0x{o:X} ({s // 1024} KB)" for n, _, _, o, s in parts))
    info["resumo"] = "\n".join(linhas)
    return info


def _analisa_ino(p: Path) -> dict:
    t = p.read_text(errors="replace")
    incs = sorted(set(re.findall(r'#include\s*[<"]([^>"]+)[>"]', t)))
    linhas = [f"Sketch Arduino com {len(t.splitlines())} linhas.",
              "Tem setup(): " + ("sim" if re.search(r"void\s+setup\s*\(", t) else "NÃO"),
              "Tem loop(): " + ("sim" if re.search(r"void\s+loop\s*\(", t) else "NÃO"),
              "Bibliotecas: " + (", ".join(incs) if incs else "nenhuma")]
    return {"resumo": "\n".join(linhas), "bibliotecas": incs}


def _analisa_imagem(p: Path) -> dict:
    from PIL import Image
    with Image.open(p) as im:
        return {"resumo": f"Imagem {im.format} de {im.width}×{im.height} pixels, modo {im.mode}.",
                "largura": im.width, "altura": im.height}


def _analisa_texto(p: Path) -> dict:
    t = p.read_text(errors="replace")
    return {"resumo": f"Arquivo de texto/código com {len(t.splitlines())} linhas e {len(t)} caracteres."}


ANALISES = {"apk": _analisa_apk, "bin": _analisa_bin, "ino": _analisa_ino, "imagem": _analisa_imagem,
            "texto": _analisa_texto}


# ----------------------------------------------------------------------------- armazenamento
def salva(nome: str, origem: Path) -> dict:
    """Move o arquivo recebido para a pasta de anexos e o analisa."""
    tipo = tipo_de(nome)
    if tipo is None:
        raise ValueError("Tipo de arquivo não aceito. Aceito: imagens, .ino, .bin, .apk e arquivos de código/texto.")
    id_ = uuid.uuid4().hex[:10]
    pasta = config.UPLOADS_DIR / id_
    pasta.mkdir(parents=True, exist_ok=True)
    seguro = nome_seguro(nome)
    destino = pasta / seguro
    shutil.move(str(origem), destino)
    try:
        analise = ANALISES[tipo](destino)
    except Exception as e:  # análise é um extra: nunca derruba o envio
        analise = {"resumo": f"Não consegui analisar o arquivo ({type(e).__name__})."}
    meta = {"id": id_, "name": seguro, "tipo": tipo, "size": destino.stat().st_size, "created": time.time(),
            "analise": analise, "url": f"/uploads/{id_}/{seguro}"}
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    return meta


def meta(id_: str) -> dict | None:
    if not ID_RE.fullmatch(id_):
        return None
    f = config.UPLOADS_DIR / id_ / "meta.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def caminho(id_: str) -> Path | None:
    m = meta(id_)
    if not m:
        return None
    p = config.UPLOADS_DIR / id_ / m["name"]
    return p if p.exists() else None


def texto(id_: str, limite: int = LIMITE_TEXTO) -> str | None:
    p = caminho(id_)
    if p is None:
        return None
    return p.read_text(errors="replace")[:limite]


def apaga(id_: str) -> None:
    if ID_RE.fullmatch(id_):
        shutil.rmtree(config.UPLOADS_DIR / id_, ignore_errors=True)


def limpa_antigos(dias: int = 14) -> None:
    limite = time.time() - dias * 86400
    for f in config.UPLOADS_DIR.glob("*/meta.json"):
        try:
            if json.loads(f.read_text())["created"] < limite:
                shutil.rmtree(f.parent, ignore_errors=True)
        except (OSError, ValueError, KeyError):
            pass


def contexto_do_anexo(id_: str) -> str:
    """Texto que descreve o anexo para o modelo de linguagem usar na conversa."""
    m = meta(id_)
    if not m:
        return ""
    cab = f"Arquivo anexado: {m['name']} ({m['tipo']}, {m['size'] // 1024} KB)\n{m['analise'].get('resumo', '')}"
    if m["tipo"] in ("ino", "texto"):
        conteudo = texto(id_)
        if conteudo:
            cab += f"\n--- conteúdo ---\n{conteudo}\n--- fim ---"
    return cab


def encontra_app_por_pacote(pacote: str) -> str | None:
    """Se o APK anexado foi criado por esta IA, devolve o id da entrega (para modificar pelo código-fonte)."""
    for f in config.APPS_DIR.glob("*/meta.json"):
        try:
            m = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if m.get("app_id") == pacote:
            return m["id"]
    return None
EOF
  cat > imagens.py <<'EOF'
"""Criar e modificar imagens (texto para imagem e imagem para imagem), 100% local.

Usa o Stable Diffusion Turbo pelo stable-diffusion.cpp. Não existe filtro de conteúdo.
A IA de texto traduz e detalha o pedido para inglês (os modelos de imagem entendem inglês bem melhor).
"""
import asyncio
import json
import random
import re
import shutil
import sys
import uuid
from pathlib import Path

import httpx

import androidgen
import config
import entregas
import recursos
import uploads

TAMANHOS = {"512x512": (512, 512), "512x768": (512, 768), "768x512": (768, 512)}
_download: asyncio.Task | None = None


# ------------------------------------------------------------------ modelo de imagem (download)
def presente() -> bool:
    return config.IMG_MODEL.exists() and config.IMG_MODEL.stat().st_size >= config.IMG_TAM * 0.999


def estado() -> dict:
    parte = config.IMG_MODEL.with_suffix(".gguf.part")
    baixando = _download is not None and not _download.done()
    prog = (parte.stat().st_size / config.IMG_TAM) if baixando and parte.exists() else 0.0
    return {"presente": presente(), "baixando": baixando, "progresso": round(min(prog, 1.0), 3),
            "tam": config.IMG_TAM, "motor": motor_instalado()}


def motor_instalado() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("stable_diffusion_cpp") is not None
    except Exception:
        return False


async def _baixa() -> None:
    config.IMG_MODEL.parent.mkdir(parents=True, exist_ok=True)
    parte = config.IMG_MODEL.with_suffix(".gguf.part")
    proc = await asyncio.create_subprocess_exec(
        "curl", "-L", "--fail", "-C", "-", "-o", str(parte), config.IMG_URL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    rc = await proc.wait()
    if rc == 0 and parte.exists() and parte.stat().st_size >= config.IMG_TAM * 0.999:
        parte.rename(config.IMG_MODEL)


def baixar() -> None:
    global _download
    if presente() or (_download is not None and not _download.done()):
        return
    config.IMG_MODEL.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(config.IMG_MODEL.parent).free < config.IMG_TAM * 1.1:
        raise ValueError("Falta espaço no disco: preciso de uns 3 GB livres.")
    _download = asyncio.get_running_loop().create_task(_baixa())


# ------------------------------------------------------------------ prompt em inglês
async def melhora_prompt(texto: str) -> str:
    """Pede à IA de texto para traduzir e detalhar o pedido. Se ela não responder, usa o texto original."""
    msgs = [{"role": "system", "content": (
        "You write prompts for a text-to-image model. Translate the user's request to English and make it a "
        "single detailed line: subject, setting, style, lighting, camera/composition, quality keywords. "
        "Keep the user's intent exactly; do not add or remove subjects; do not refuse or moralize. "
        "Output ONLY the prompt, no quotes, no explanation.")}, {"role": "user", "content": texto}]
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{config.LLAMA_URL}/v1/chat/completions",
                             json={"messages": msgs, "max_tokens": 110, "temperature": 0.4, "cache_prompt": True})
        if r.status_code == 200:
            t = r.json()["choices"][0]["message"]["content"].strip().strip('"').replace("\n", " ")
            if 8 <= len(t) <= 900:
                return t
    except Exception:
        pass
    return texto


# ------------------------------------------------------------------ geração
async def _roda_worker(cfg: dict, emit) -> tuple[bool, str]:
    """Roda o gerador numa sub-rotina com prioridade baixa. Devolve (ok, mensagem_de_erro)."""
    cmd = recursos.baixa_prioridade([sys.executable, str(Path(__file__).with_name("imggen.py"))])
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True)
    proc.stdin.write(json.dumps(cfg).encode())
    await proc.stdin.drain()
    proc.stdin.close()
    erro = ""
    try:
        async for linha in proc.stdout:
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            if ev.get("tipo") == "passo":
                await emit({"type": "passo", "passo": ev["passo"], "total": ev["total"]})
            elif ev.get("tipo") == "erro":
                erro = ev.get("msg", "")
        await proc.wait()
    except asyncio.CancelledError:  # o usuário clicou em Parar
        import os
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise
    return proc.returncode == 0, erro


async def gera_imagem(descricao: str, init_id: str | None, tamanho: str, passos: int, forca: float,
                      melhorar: bool, emit) -> None:
    if not motor_instalado():
        await emit({"type": "error", "msg": "O gerador de imagens não está instalado. Rode: bash atualizar.sh"})
        return
    if not presente():
        await emit({"type": "error", "msg": "Falta baixar o modelo de imagens (2 GB). Use o botão de baixar abaixo.",
                    "precisa_modelo": True})
        return
    if androidgen._trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outra coisa. Espere terminar ou clique em Parar."})
        return
    largura, altura = TAMANHOS.get(tamanho, (512, 512))
    passos = max(1, min(int(passos), 8))
    async with androidgen._trava:
        try:
            recursos.garante_ram(3500, "gerar a imagem")
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
            return
        init = None
        if init_id:
            init = uploads.caminho(init_id)
            m = uploads.meta(init_id)
            if init is None or not m or m["tipo"] != "imagem":
                await emit({"type": "error", "msg": "A imagem anexada não foi encontrada. Anexe de novo."})
                return
        prompt = descricao
        if melhorar:
            await emit({"type": "status", "msg": "A IA está traduzindo e detalhando o seu pedido…"})
            prompt = await melhora_prompt(descricao)
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"img-{id_}"
        pasta.mkdir(parents=True, exist_ok=True)
        saida = pasta / "imagem.png"
        seed = random.randint(1, 2**31 - 1)
        modo = recursos.modo_imagem()
        base = {"modelo": str(config.IMG_MODEL), "threads": recursos.nucleos_fisicos(), "prompt": prompt,
                "largura": largura, "altura": altura, "passos": passos, "seed": seed, "saida": str(saida),
                "cfg": 1.0, "init": str(init) if init else None, "forca": forca}
        try:
            rotulo = {"gpu": "na placa de vídeo", "hibrido": "na placa de vídeo e na CPU", "cpu": "na CPU"}[modo]
            await emit({"type": "status", "msg": f"Gerando a imagem {rotulo}… (leva cerca de 1 minuto na CPU)"})
            ok, erro = await _roda_worker({**base, "modo": modo}, emit)
            if not ok and modo != "cpu":
                await emit({"type": "status", "msg": "A placa de vídeo não deu conta; tentando só pela CPU…"})
                ok, erro = await _roda_worker({**base, "modo": "cpu"}, emit)
            if not ok or not saida.exists():
                await emit({"type": "error", "msg": "Não consegui gerar a imagem.", "log": erro})
                return
            slug = androidgen.slugify(descricao) or "imagem"
            nome = (descricao[:40] or "Imagem").strip()
            meta = entregas.salva(id_, nome, descricao, "img", [(saida, f"{slug}.png", "Imagem (.png)")],
                                  {"prompt": prompt, "seed": seed, "tamanho": f"{largura}x{altura}",
                                   "modificada_de": init_id})
            await emit({"type": "done", "id": id_, "name": nome, "kind": "img", "files": meta["files"],
                        "preview": meta["files"][0]["url"], "prompt": prompt,
                        "note": f"Prompt usado: {prompt}"})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
EOF
  cat > imggen.py <<'EOF'
#!/usr/bin/env python3
"""Processo isolado que gera UMA imagem com o stable-diffusion.cpp (Stable Diffusion Turbo).

Lê um JSON na entrada padrão e escreve eventos JSON (um por linha) na saída. Roda separado do
servidor para: (1) poder ser cancelado na hora (botão Parar), (2) devolver toda a memória ao
terminar, (3) se a GPU não der conta, o servidor repete só pela CPU.
Não há filtro de conteúdo: o modelo gera o que foi pedido.
"""
import json
import os
import sys


def evento(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def main():
    cfg = json.loads(sys.stdin.read())
    modo = cfg.get("modo", "cpu")
    if modo == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"   # sem GPU: tudo na CPU, mesmo que o binding tenha CUDA
    try:
        from stable_diffusion_cpp import StableDiffusion
        kw = dict(model_path=cfg["modelo"], n_threads=cfg["threads"], vae_decode_only=not cfg.get("init"))
        if modo == "hibrido":                         # modelo de difusão na placa; texto e decodificador na CPU
            kw.update(keep_clip_on_cpu=True, keep_vae_on_cpu=True)
        elif modo == "gpu":
            kw.update(keep_clip_on_cpu=False, keep_vae_on_cpu=False)
        sd = StableDiffusion(**kw)
        evento(tipo="carregado", modo=modo)

        def passo(i, total, tempo):
            evento(tipo="passo", passo=int(i), total=int(total), seg=round(float(tempo), 1))

        args = dict(prompt=cfg["prompt"], negative_prompt=cfg.get("negativo", ""), width=cfg["largura"],
                    height=cfg["altura"], cfg_scale=cfg.get("cfg", 1.0), sample_steps=cfg["passos"],
                    seed=cfg["seed"], sample_method="euler_a", progress_callback=passo,
                    vae_tiling=(modo != "cpu"))  # em GPU o decodificador precisa ser fatiado para caber na VRAM
        if cfg.get("init"):
            args.update(init_image=cfg["init"], strength=cfg.get("forca", 0.6))
        imagens = sd.generate_image(**args)
        imagens[0].save(cfg["saida"])
        evento(tipo="pronto", arquivo=cfg["saida"])
    except Exception as e:  # o servidor decide o que fazer (por exemplo, repetir só pela CPU)
        evento(tipo="erro", msg=f"{type(e).__name__}: {e}"[:300])
        sys.exit(1)


if __name__ == "__main__":
    main()
EOF
  cat > codegen.py <<'EOF'
"""Programar projetos com VÁRIOS arquivos: app Android avançado, página Web e script Python.

Diferente do modo simples (um arquivo só), aqui a IA devolve vários arquivos no formato

    NOME: <nome>
    ### caminho/do/arquivo.ext
    ```linguagem
    conteúdo
    ```

O servidor valida os caminhos, monta o projeto, confere/compila e devolve os arquivos prontos.
Para MODIFICAR algo que já existe, a IA recebe os arquivos atuais e devolve só o que mudou.
"""
import asyncio
import re
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

import androidgen
import config
import entregas
import recursos

ARQ_RE = re.compile(r"^###\s*(?:ARQUIVO:\s*)?`?([^\s`]+)`?\s*\n```[^\n]*\n(.*?)\n```", re.S | re.M)
LIMITE_ARQ = 60_000
MAX_ARQUIVOS = 20


def extrai_arquivos(texto: str) -> dict[str, str]:
    arqs: dict[str, str] = {}
    for caminho, conteudo in ARQ_RE.findall(texto):
        arqs[caminho.strip().lstrip("./")] = conteudo.rstrip() + "\n"
    return arqs


def extrai_nome(texto: str, padrao: str) -> str:
    m = re.search(r"NOME:\s*(.+)", texto)
    return ((m.group(1).strip() if m else padrao)[:40]) or padrao


def caminho_seguro(c: str) -> bool:
    p = Path(c)
    return not p.is_absolute() and ".." not in p.parts and len(c) < 160 and bool(re.fullmatch(r"[\w./\- ]+", c))


def formata_base(arqs: dict[str, str], limite: int = 12_000) -> str:
    """Mostra os arquivos atuais à IA (cortando se for muito grande)."""
    out, usado = [], 0
    for c, t in arqs.items():
        trecho = t if usado + len(t) <= limite else t[: max(0, limite - usado)] + "\n…(cortado)"
        out.append(f"### {c}\n```\n{trecho.rstrip()}\n```")
        usado += len(t)
        if usado >= limite:
            break
    return "\n".join(out)


async def _compila_comando(cmd: list[str], pasta: Path) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *recursos.baixa_prioridade(cmd), cwd=pasta, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    try:
        saida, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        import os
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise
    return proc.returncode == 0, saida.decode(errors="replace")


def zipa(pasta: Path, arquivos: dict[str, str], destino: Path) -> Path:
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for c, t in arquivos.items():
            z.writestr(c, t)
    return destino


# =============================================================================== Android avançado
SYS_ANDROID = """Você é um programador Android sênior. Crie um aplicativo Android completo com VÁRIOS arquivos.

REGRAS OBRIGATÓRIAS:
1. Linguagem: Java (NÃO use Kotlin). Pacote: com.localai.app.
2. Já estão no projeto: AppCompat, Material, ConstraintLayout, RecyclerView, CardView, ViewPager2 e SwipeRefreshLayout. NENHUMA outra biblioteca.
3. A tela principal é com.localai.app.MainActivity (extends AppCompatActivity), com layout em app/src/main/res/layout/activity_main.xml. Pode criar outras Activities, Fragments e classes (arquivos .java), layouts, e res/values (strings.xml, colors.xml, themes.xml) e res/drawable (somente XML).
4. Se criar OUTRAS Activities, envie TAMBÉM o app/src/main/AndroidManifest.xml completo declarando todas (MainActivity com o filtro MAIN/LAUNCHER). Se não precisar, NÃO envie o manifesto.
5. Tema do app: @style/Theme.AppCompat.Light.DarkActionBar (ou um tema Material, se usar Material).
6. Todos os IDs usados no código (R.id.x) devem existir nos layouts. Inclua TODOS os imports. O projeto precisa compilar na primeira tentativa.
7. Sem imagens binárias (use cores e drawables XML). Textos em português do Brasil. Permissão de internet já existe.
8. Todo caminho começa com app/src/main/.

FORMATO (siga exatamente, sem explicações):
NOME: <nome curto do app>
### app/src/main/java/com/localai/app/MainActivity.java
```java
<código>
```
### app/src/main/res/layout/activity_main.xml
```xml
<layout>
```
(um bloco ### + código para cada arquivo)"""

EX_PEDIDO_AND = "um contador com botões de mais e menos"
EX_RESP_AND = """NOME: Contador
### app/src/main/java/com/localai/app/MainActivity.java
```java
package com.localai.app;

import android.os.Bundle;
import android.widget.Button;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

public class MainActivity extends AppCompatActivity {
    private int valor = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        final TextView texto = findViewById(R.id.texto);
        Button mais = findViewById(R.id.mais);
        Button menos = findViewById(R.id.menos);
        mais.setOnClickListener(v -> texto.setText(String.valueOf(++valor)));
        menos.setOnClickListener(v -> texto.setText(String.valueOf(--valor)));
    }
}
```
### app/src/main/res/layout/activity_main.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent"
    android:gravity="center"
    android:orientation="vertical"
    android:padding="24dp">

    <TextView
        android:id="@+id/texto"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="0"
        android:textSize="48sp" />

    <Button
        android:id="@+id/mais"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="+1" />

    <Button
        android:id="@+id/menos"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="-1" />
</LinearLayout>
```"""

APP_BUILD_AVANCADO = """plugins {
    id 'com.android.application'
}

android {
    namespace 'com.localai.app'
    compileSdk 34

    defaultConfig {
        applicationId "%(app_id)s"
        minSdk 24
        targetSdk 34
        versionCode 1
        versionName "1.0"
    }

    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }

    lint {
        abortOnError false
        checkReleaseBuilds false
    }
}

dependencies {
    implementation 'androidx.appcompat:appcompat:1.6.1'
    implementation 'com.google.android.material:material:1.11.0'
    implementation 'androidx.constraintlayout:constraintlayout:2.1.4'
    implementation 'androidx.recyclerview:recyclerview:1.3.2'
    implementation 'androidx.cardview:cardview:1.0.0'
    implementation 'androidx.viewpager2:viewpager2:1.0.0'
    implementation 'androidx.swiperefreshlayout:swiperefreshlayout:1.1.0'
}
"""

GRADLE_PROPS_AVANCADO = """org.gradle.jvmargs=-Xmx1536m -Dfile.encoding=UTF-8
org.gradle.workers.max=2
android.useAndroidX=true
android.nonTransitiveRClass=true
"""

MANIFEST_AVANCADO = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET" />
    <application
        android:label="%(label)s"
        android:allowBackup="true"
        android:usesCleartextTraffic="true"
        android:theme="@style/Theme.AppCompat.Light.DarkActionBar">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""


def valida_android(arqs: dict[str, str]) -> str | None:
    """Devolve uma mensagem de erro se os arquivos do app não forem aceitáveis."""
    if not arqs:
        return "A IA não devolveu nenhum arquivo no formato pedido."
    if len(arqs) > MAX_ARQUIVOS:
        return f"Arquivos demais ({len(arqs)}). O máximo é {MAX_ARQUIVOS}."
    for c, t in arqs.items():
        if not caminho_seguro(c) or not c.startswith("app/src/main/"):
            return f"Caminho não permitido: {c}"
        if not (c.endswith(".java") or c.endswith(".xml")):
            return f"Tipo de arquivo não permitido: {c} (só .java e .xml)"
        if len(t) > LIMITE_ARQ:
            return f"Arquivo grande demais: {c}"
    return None


def escreve_projeto_avancado(pasta: Path, nome: str, app_id: str, arqs: dict[str, str]) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "settings.gradle").write_text(androidgen.SETTINGS_GRADLE)
    (pasta / "build.gradle").write_text(androidgen.ROOT_BUILD_GRADLE)
    (pasta / "gradle.properties").write_text(GRADLE_PROPS_AVANCADO)
    import os
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    (pasta / "local.properties").write_text(f"sdk.dir={sdk}\n")
    (pasta / "app").mkdir(exist_ok=True)
    (pasta / "app/build.gradle").write_text(APP_BUILD_AVANCADO % {"app_id": app_id})
    todos = dict(arqs)
    todos.setdefault("app/src/main/AndroidManifest.xml", MANIFEST_AVANCADO % {"label": androidgen.xml_escape(nome)})
    for c, t in todos.items():
        f = pasta / c
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(t)


async def gera_app_avancado(descricao: str, base: dict[str, str] | None, base_nome: str | None, emit) -> None:
    if androidgen._trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outra coisa. Espere terminar ou clique em Parar."})
        return
    async with androidgen._trava:
        try:
            recursos.garante_ram(2500, "compilar o app")
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
            return
        if base:
            pedido = (f"Estes são os arquivos ATUAIS do app:\n{formata_base(base)}\n\n"
                      f"Modifique o app conforme o pedido e devolva SOMENTE os arquivos que mudaram ou são novos, "
                      f"cada um COMPLETO (não use reticências). Pedido: {descricao}")
        else:
            pedido = f"Crie: {descricao}"
        mensagens = [{"role": "system", "content": SYS_ANDROID},
                     {"role": "user", "content": f"Crie: {EX_PEDIDO_AND}"},
                     {"role": "assistant", "content": EX_RESP_AND},
                     {"role": "user", "content": pedido}]
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"appx-{id_}"
        try:
            for tentativa in range(config.MAX_FIX_ATTEMPTS + 1):
                if tentativa == 0:
                    await emit({"type": "status", "msg": "A IA está escrevendo os arquivos do app… (pode levar vários minutos)"})
                else:
                    await emit({"type": "status", "msg": f"Deu erro ao compilar. A IA está corrigindo (tentativa {tentativa} de {config.MAX_FIX_ATTEMPTS})…"})
                texto = await androidgen.pede_codigo(mensagens, emit)
                novos = extrai_arquivos(texto)
                erro = valida_android(novos)
                if erro:
                    await emit({"type": "error", "msg": erro, "log": texto[-1500:]})
                    return
                arqs = {**(base or {}), **novos}
                if not any(c.endswith("MainActivity.java") for c in arqs):
                    await emit({"type": "error", "msg": "Faltou a MainActivity.java.", "log": texto[-800:]})
                    return
                nome = extrai_nome(texto, base_nome or "App IA")
                await emit({"type": "code", "text": "\n\n".join(f"// {c}\n{t}" for c, t in novos.items())})
                if pasta.exists():
                    shutil.rmtree(pasta)
                app_id = f"com.localai.{androidgen.slugify(nome)}{id_[:4]}"
                escreve_projeto_avancado(pasta, nome, app_id, arqs)
                await emit({"type": "status", "msg": "Compilando o app… (a primeira vez baixa as bibliotecas e demora mais)"})
                ok, log, apk = await androidgen.compila(pasta)
                if ok:
                    meta = entregas.salva(id_, nome, descricao, "apk",
                                          [(apk, f"{androidgen.slugify(nome)}.apk", "App Android (.apk)")],
                                          {"app_id": app_id, "modo": "avancado", "arquivos_fonte": arqs})
                    await emit({"type": "done", "id": id_, "name": nome, "kind": "apk", "files": meta["files"],
                                "note": "Passe o arquivo para o celular e abra-o para instalar. Para mudar o app, use ✏ Modificar."})
                    return
                erros = androidgen.resumo_erros(log)
                if tentativa >= config.MAX_FIX_ATTEMPTS:
                    await emit({"type": "error", "msg": "Não consegui compilar o app depois das correções.", "log": erros})
                    return
                mensagens += [{"role": "assistant", "content": texto},
                              {"role": "user", "content": (
                                  "O código NÃO compilou. ERROS DE COMPILAÇÃO:\n" + erros +
                                  "\n\nCorrija e devolva SOMENTE os arquivos que precisam mudar, completos, no mesmo formato.")}]
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)


# =============================================================================== Web e Python
SYS_WEB = """Você é um desenvolvedor web experiente. Crie uma página/aplicação web completa.

REGRAS:
1. O arquivo principal é index.html, com HTML, CSS e JavaScript. Pode enviar também outros arquivos (.css, .js) se ajudar.
2. NÃO use bibliotecas externas, CDNs nem imagens da internet: tudo precisa funcionar offline, abrindo o index.html.
3. Layout responsivo (funciona no celular), visual limpo e moderno. Textos em português do Brasil.
4. O código precisa funcionar na primeira tentativa.

FORMATO (siga exatamente, sem explicações):
NOME: <nome curto>
### index.html
```html
<código>
```
(um bloco ### + código para cada arquivo)"""

SYS_PY = """Você é um programador Python experiente. Crie o programa pedido.

REGRAS:
1. Python 3. Use apenas a biblioteca padrão, a menos que o usuário peça outra biblioteca.
2. O arquivo principal é main.py. Pode enviar outros arquivos .py se ajudar.
3. O código precisa rodar na primeira tentativa. Comentários e mensagens em português do Brasil.

FORMATO (siga exatamente, sem explicações):
NOME: <nome curto>
### main.py
```python
<código>
```
(um bloco ### + código para cada arquivo)"""

EX_WEB = ("um contador de cliques",
          "NOME: Contador\n### index.html\n```html\n<!doctype html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
          "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Contador</title>\n"
          "<style>body{font-family:sans-serif;text-align:center;padding:40px}button{font-size:24px;padding:12px 24px}</style>\n"
          "</head>\n<body>\n<h1 id=\"n\">0</h1>\n<button onclick=\"n.textContent=++c\">+1</button>\n"
          "<script>let c=0;</script>\n</body>\n</html>\n```")
EX_PY = ("somar dois números digitados",
         "NOME: Somador\n### main.py\n```python\ndef main():\n    a = float(input(\"Primeiro número: \"))\n"
         "    b = float(input(\"Segundo número: \"))\n    print(f\"Soma: {a + b}\")\n\n\nif __name__ == \"__main__\":\n    main()\n```")

ALVOS = {
    "web": dict(sys=SYS_WEB, ex=EX_WEB, rotulo="página web", principal="index.html", icone="web"),
    "python": dict(sys=SYS_PY, ex=EX_PY, rotulo="programa Python", principal="main.py", icone="python"),
}


async def confere_python(pasta: Path, arqs: dict[str, str]) -> str:
    erros = []
    for c in arqs:
        if c.endswith(".py"):
            ok, saida = await _compila_comando([sys.executable, "-m", "py_compile", c], pasta)
            if not ok:
                erros.append(saida.strip()[-600:])
    return "\n".join(erros)


async def gera_codigo(alvo: str, descricao: str, base: dict[str, str] | None, base_nome: str | None, emit) -> None:
    a = ALVOS.get(alvo)
    if not a:
        await emit({"type": "error", "msg": "Tipo de projeto desconhecido."})
        return
    if androidgen._trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outra coisa. Espere terminar ou clique em Parar."})
        return
    async with androidgen._trava:
        if base:
            pedido = (f"Estes são os arquivos ATUAIS:\n{formata_base(base)}\n\nModifique conforme o pedido e devolva "
                      f"SOMENTE os arquivos que mudaram ou são novos, cada um COMPLETO. Pedido: {descricao}")
        else:
            pedido = f"Crie: {descricao}"
        mensagens = [{"role": "system", "content": a["sys"]}, {"role": "user", "content": f"Crie: {a['ex'][0]}"},
                     {"role": "assistant", "content": a["ex"][1]}, {"role": "user", "content": pedido}]
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"cod-{id_}"
        try:
            for tentativa in range(config.MAX_FIX_ATTEMPTS + 1):
                await emit({"type": "status", "msg": (f"A IA está escrevendo o {a['rotulo']}…" if tentativa == 0 else
                            f"Achei um erro. A IA está corrigindo (tentativa {tentativa} de {config.MAX_FIX_ATTEMPTS})…")})
                texto = await androidgen.pede_codigo(mensagens, emit)
                novos = extrai_arquivos(texto)
                if not novos or not all(caminho_seguro(c) for c in novos) or len(novos) > MAX_ARQUIVOS:
                    await emit({"type": "error", "msg": "A IA não devolveu os arquivos no formato pedido.", "log": texto[-1200:]})
                    return
                arqs = {**(base or {}), **novos}
                if a["principal"] not in arqs:
                    await emit({"type": "error", "msg": f"Faltou o arquivo {a['principal']}.", "log": texto[-800:]})
                    return
                nome = extrai_nome(texto, base_nome or a["rotulo"].capitalize())
                await emit({"type": "code", "text": "\n\n".join(f"// {c}\n{t}" for c, t in novos.items())})
                erros = ""
                if alvo == "python":
                    await emit({"type": "status", "msg": "Conferindo se o código Python é válido…"})
                    if pasta.exists():
                        shutil.rmtree(pasta)
                    for c, t in arqs.items():
                        (pasta / c).parent.mkdir(parents=True, exist_ok=True)
                        (pasta / c).write_text(t)
                    erros = await confere_python(pasta, arqs)
                if erros:
                    if tentativa >= config.MAX_FIX_ATTEMPTS:
                        await emit({"type": "error", "msg": "O código continua com erros de sintaxe.", "log": erros})
                        return
                    mensagens += [{"role": "assistant", "content": texto},
                                  {"role": "user", "content": "O código tem erros:\n" + erros +
                                   "\n\nCorrija e devolva SOMENTE os arquivos que precisam mudar, completos, no mesmo formato."}]
                    continue
                slug = androidgen.slugify(nome)
                pasta.mkdir(parents=True, exist_ok=True)
                saidas = []
                for c, t in arqs.items():
                    dest = pasta / "saida" / c.replace("/", "_")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(t)
                    saidas.append((dest, c.replace("/", "_"), f"Arquivo {c}"))
                zip_dest = zipa(pasta, arqs, pasta / f"{slug}.zip")
                saidas.append((zip_dest, f"{slug}.zip", "Projeto completo (.zip)"))
                meta = entregas.salva(id_, nome, descricao, alvo, saidas, {"arquivos_fonte": arqs})
                nota = ("Abra o index.html no navegador (funciona offline)." if alvo == "web"
                        else "Rode com: python3 main.py")
                await emit({"type": "done", "id": id_, "name": nome, "kind": alvo, "files": meta["files"],
                            "note": nota + " Para mudar, use ✏ Modificar."})
                return
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
EOF
  cat > biblioteca.py <<'EOF'
"""Biblioteca local: documentação e código de referência guardados no disco grande.

A pasta ~/biblioteca (um link para o disco de 500 GB) tem:
  docs/    documentação (Python, MDN/web...)       codigo/  exemplos e bibliotecas (ESP32, Android...)
  zim/     Wikipedia em português (offline)         meus/    qualquer arquivo seu: a IA passa a consultar
O servidor cria um índice de busca (SQLite FTS5) com tudo isso, em segundo plano e com prioridade baixa.
Na hora de responder ou de criar apps/firmware, os trechos mais parecidos com o pedido entram no contexto
da IA, assim ela acerta nomes de funções e usos sem depender da internet.
"""
import html
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import config

EXTENSOES = {".md", ".rst", ".txt", ".h", ".hpp", ".c", ".cpp", ".ino", ".java", ".kt", ".py",
             ".html", ".htm", ".gradle", ".xml", ".js", ".ts", ".css"}
IGNORAR = {".git", "node_modules", "build", "__pycache__", ".gradle", "dist", "venv", ".venv"}
MAX_ARQUIVO = 400_000
TAM_TRECHO = 1300
PARADAS = {"para", "como", "qual", "quais", "com", "uma", "que", "por", "mais", "isso", "este", "esta", "the", "and",
           "for", "you", "with", "create", "crie", "faca", "fazer", "app", "quero", "preciso", "gere", "pode"}

_estado = {"rodando": False, "arquivos": 0, "trechos": 0, "inicio": 0.0, "fim": 0.0, "erro": ""}
_trava = threading.Lock()


def raiz() -> Path:
    return config.BIBLIOTECA_DIR


def disponivel() -> bool:
    try:
        return raiz().is_dir() and os.access(raiz(), os.W_OK)
    except OSError:
        return False


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(raiz() / "indice.db"), timeout=30)
    con.execute("CREATE TABLE IF NOT EXISTS arquivos(caminho TEXT PRIMARY KEY, mtime REAL, fonte TEXT)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS trechos USING fts5("
                "texto, caminho UNINDEXED, fonte UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
    return con


def _fonte(rel: Path) -> str:
    return "/".join(rel.parts[:2]) if len(rel.parts) > 2 else (rel.parts[0] if rel.parts else "")


def _texto(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_ARQUIVO:
            return ""
        t = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if path.suffix.lower() in (".html", ".htm"):
        t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t)
        t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return t


def _trechos(texto: str) -> list[str]:
    saida, atual = [], ""
    for bloco in re.split(r"\n\s*\n", texto):
        bloco = bloco.strip()
        if not bloco:
            continue
        if len(atual) + len(bloco) + 2 <= TAM_TRECHO:
            atual = (atual + "\n\n" + bloco) if atual else bloco
            continue
        if atual:
            saida.append(atual)
        while len(bloco) > TAM_TRECHO:  # bloco enorme (código sem linhas em branco): corta nas quebras de linha
            corte = bloco.rfind("\n", 0, TAM_TRECHO)
            corte = corte if corte > 200 else TAM_TRECHO
            saida.append(bloco[:corte])
            bloco = bloco[corte:].strip()
        atual = bloco
    if atual:
        saida.append(atual)
    return [t for t in saida if len(t) > 40]


def indexar(forcar: bool = False) -> dict:
    """Indexa arquivos novos ou alterados (e esquece os apagados). Roda em segundo plano, devagar."""
    if not disponivel() or not _trava.acquire(blocking=False):
        return dict(_estado)
    try:
        _estado.update(rodando=True, arquivos=0, trechos=0, inicio=time.time(), erro="")
        try:
            os.nice(10)
        except (OSError, AttributeError):
            pass
        con = _db()
        if forcar:
            con.execute("DELETE FROM arquivos")
            con.execute("DELETE FROM trechos")
        conhecidos = {c: m for c, m in con.execute("SELECT caminho, mtime FROM arquivos")}
        vistos = set()
        base = raiz()
        feitos = 0
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in IGNORAR and not d.startswith(".")]
            for nome in files:
                p = Path(dirpath) / nome
                if p.suffix.lower() not in EXTENSOES or nome == "indice.db":
                    continue
                rel = p.relative_to(base)
                chave = str(rel)
                vistos.add(chave)
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                if conhecidos.get(chave) == mt:
                    continue
                con.execute("DELETE FROM trechos WHERE caminho=?", (chave,))
                partes = _trechos(_texto(p))
                fonte = _fonte(rel)
                con.executemany("INSERT INTO trechos(texto, caminho, fonte) VALUES (?,?,?)",
                                [(t, chave, fonte) for t in partes])
                con.execute("INSERT OR REPLACE INTO arquivos VALUES (?,?,?)", (chave, mt, fonte))
                _estado["arquivos"] += 1
                _estado["trechos"] += len(partes)
                feitos += 1
                if feitos % 200 == 0:
                    con.commit()
                    time.sleep(0.05)  # deixa o resto do PC respirar
        for velho in set(conhecidos) - vistos:
            con.execute("DELETE FROM trechos WHERE caminho=?", (velho,))
            con.execute("DELETE FROM arquivos WHERE caminho=?", (velho,))
        con.commit()
        con.close()
    except Exception as e:  # disco desmontado no meio, banco travado...
        _estado["erro"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        _estado.update(rodando=False, fim=time.time())
        _trava.release()
    return dict(_estado)


def _palavras(consulta: str) -> list[str]:
    vistos = []
    for w in re.findall(r"[\w.+#]{3,}", consulta.lower()):
        w = w.strip(".+#")
        if len(w) >= 3 and w not in PARADAS and w not in vistos:
            vistos.append(w)
    return vistos[:8]


def busca(consulta: str, k: int = 3, fontes: tuple[str, ...] | None = None) -> list[dict]:
    """Trechos mais parecidos com a consulta. Só devolve os que casam com pelo menos 2 palavras (ou 1, se
    a consulta só tem uma), para não encher a conversa de ruído."""
    palavras = _palavras(consulta)
    if not palavras or not disponivel() or not (raiz() / "indice.db").exists():
        return []
    consulta_fts = " OR ".join('"' + w.replace('"', "") + '"' for w in palavras)
    try:
        con = _db()
        linhas = con.execute("SELECT texto, caminho, fonte FROM trechos WHERE trechos MATCH ? "
                             "ORDER BY bm25(trechos) LIMIT 40", (consulta_fts,)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    minimo = min(2, len(palavras))
    saida = []
    for texto, caminho, fonte in linhas:
        if fontes and not any(fonte.startswith(f) for f in fontes):
            continue
        baixo = texto.lower()
        # nome técnico (os.listdir, esp32, set_server...) é raro o bastante para valer por duas palavras
        pontos = sum(2 if re.search(r"[._\d]", w) else 1 for w in palavras if w in baixo)
        if pontos >= minimo:
            saida.append({"texto": texto, "caminho": caminho, "fonte": fonte})
        if len(saida) >= k:
            break
    return saida


# ------------------------------------------------------------------ Wikipedia offline (arquivos .zim)
def _zims() -> list[Path]:
    d = raiz() / "zim"
    return sorted(d.glob("*.zim")) if d.is_dir() else []


def wikipedia(consulta: str, k: int = 2, max_chars: int = 700) -> list[dict]:
    """Busca nos arquivos .zim (Wikipedia offline). Precisa do pacote libzim; sem ele, devolve vazio."""
    zims = _zims()
    if not zims or not _palavras(consulta):
        return []
    try:
        from libzim.reader import Archive
        from libzim.search import Query, Searcher
    except ImportError:
        return []
    saida = []
    for z in zims:
        try:
            arq = Archive(str(z))
            res = Searcher(arq).search(Query().set_query(consulta))
            for caminho in res.getResults(0, k):
                e = arq.get_entry_by_path(caminho)
                item = e.get_item()
                if "html" not in item.mimetype:
                    continue
                t = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ",
                                                                  bytes(item.content).decode("utf-8", "ignore"))))
                t = re.sub(r"\s+", " ", t).strip()
                saida.append({"titulo": e.title, "texto": t[:max_chars], "fonte": z.name})
        except Exception:
            continue
        if len(saida) >= k:
            break
    return saida[:k]


# ------------------------------------------------------------------ o que entra no contexto da IA
def contexto_chat(pergunta: str, max_chars: int = 1400) -> str:
    achados = busca(pergunta, k=3)
    if not achados:
        return ""
    texto, total = [], 0
    for a in achados:
        t = a["texto"][:520]
        total += len(t)
        if total > max_chars:
            break
        texto.append(f"[{a['fonte']}] {t}")
    return "Da biblioteca local (use se ajudar):\n" + "\n---\n".join(texto)


def referencias(descricao: str, max_chars: int = 1800) -> str:
    """Trechos de exemplos e documentação para ajudar a escrever o código pedido (apps, firmware, web, python)."""
    achados = busca(descricao, k=4, fontes=("codigo", "docs", "meus"))
    if not achados:
        return ""
    texto, total = [], 0
    for a in achados:
        t = a["texto"][:600]
        total += len(t)
        if total > max_chars:
            break
        texto.append(f"// {a['caminho']}\n{t}")
    return ("\n\nReferências da biblioteca local (exemplos reais; use os nomes de funções e classes se servirem, "
            "ignore se não tiverem a ver):\n" + "\n---\n".join(texto))


# ------------------------------------------------------------------ painel
def status() -> dict:
    r = raiz()
    info = {"pasta": str(r), "destino": "", "disponivel": disponivel(), "livre_gb": None, "total_gb": None,
            "fontes": [], "zims": [], "indexando": _estado["rodando"], "ultimo_erro": _estado["erro"],
            "wikipedia_pronta": False}
    try:
        info["destino"] = str(r.resolve())
        u = shutil.disk_usage(r if r.exists() else r.parent)
        info["livre_gb"], info["total_gb"] = round(u.free / 1e9), round(u.total / 1e9)
    except OSError:
        pass
    if info["disponivel"]:
        try:
            if (r / "indice.db").exists():
                con = _db()
                info["fontes"] = [{"nome": f, "trechos": n, "arquivos": a} for f, n, a in con.execute(
                    "SELECT t.fonte, COUNT(*), COUNT(DISTINCT t.caminho) FROM trechos t GROUP BY t.fonte ORDER BY 2 DESC")]
                con.close()
        except sqlite3.Error:
            pass
        info["zims"] = [{"nome": z.name, "gb": round(z.stat().st_size / 1e9, 1)} for z in _zims()]
        try:
            import libzim  # noqa: F401
            info["wikipedia_pronta"] = bool(info["zims"])
        except ImportError:
            pass
    return info
EOF
  cat > "$BASE/diagnostico_fans.sh" <<'EOF'
#!/usr/bin/env bash
# Mostra o que dá para controlar nas ventoinhas pelo Linux (GPU e CPU).
#   bash diagnostico_fans.sh                   -> lista sensores e ventoinhas
#   sudo bash diagnostico_fans.sh testar hwmonN pwmM
#        -> põe esse PWM em 100% por 8 s e mostra se alguma ventoinha acelerou (depois restaura)

if [ "${1:-}" = "testar" ]; then
  d="/sys/class/hwmon/$2"; p="$d/$3"
  [ -w "$p" ] || { echo "Sem permissão ou não existe: $p (rode com sudo)"; exit 1; }
  orig=$(cat "${p}_enable" 2>/dev/null || echo "")
  restaura() { [ -n "$orig" ] && echo "$orig" > "${p}_enable"; echo "Restaurado ao modo original ($orig)."; }
  trap restaura EXIT
  leitura() { for f in "$d"/fan*_input; do [ -e "$f" ] && printf '  %s=%s RPM' "$(basename "${f%_input}")" "$(cat "$f")"; done; echo; }
  echo "Antes:"; leitura
  echo 1 > "${p}_enable"; echo 255 > "$p"; sleep 8
  echo "Com $3 em 100%:"; leitura
  echo "Se alguma ventoinha ACELEROU (e era a da CPU), use: FANCTL_CPU_PWM=$p"
  exit 0
fi

echo "== Placa de vídeo NVIDIA =="
nvidia-smi --query-gpu=name,temperature.gpu,fan.speed --format=csv 2>/dev/null || echo "nvidia-smi não encontrado"
echo
echo "== Sensores (hwmon): temperatura da CPU, ventoinhas (RPM) e controles PWM =="
achou_pwm=0
for d in /sys/class/hwmon/hwmon*; do
  nome=$(cat "$d/name" 2>/dev/null) || continue
  fans=""; pwms=""
  for f in "$d"/fan*_input; do [ -e "$f" ] && fans="$fans $(basename "${f%_input}")=$(cat "$f")rpm"; done
  for p in "$d"/pwm[0-9]; do [ -e "$p" ] && { pwms="$pwms $(basename "$p")"; achou_pwm=1; }; done
  case "$nome" in k10temp|coretemp|zenpower) echo "$(basename "$d")  $nome  CPU: $(( $(cat "$d/temp1_input") / 1000 )) °C" ;; esac
  [ -n "$fans$pwms" ] && echo "$(basename "$d")  $nome  ventoinhas:${fans:- nenhuma}  PWM:${pwms:- nenhum}"
done
echo
if [ "$achou_pwm" = 1 ]; then
  echo "Sua placa-mãe expõe controles PWM no Linux. Para a ventoinha da CPU:"
  echo "  1) Descubra qual PWM comanda a ventoinha da CPU:  sudo bash diagnostico_fans.sh testar hwmonN pwmM"
  echo "  2) Coloque a linha  FANCTL_CPU_PWM=/sys/class/hwmon/hwmonN/pwmM  no arquivo ~/localai/server/fan.env"
  echo "  3) sudo systemctl restart localai-fan"
else
  echo "Nenhum PWM de ventoinha exposto pelo Linux (comum em placas-mãe AMD novas sem o driver do chip)."
  echo "A ventoinha da CPU continua sendo controlada pela BIOS. Para ela acelerar sob demanda:"
  echo "  - Entre na BIOS (tecla Del/F2 ao ligar) > Monitor/Hardware Monitor/Q-Fan/Smart Fan"
  echo "  - Escolha o perfil 'Standard' ou 'Turbo' (ou uma curva própria: ~40% a 40 °C, 70% a 65 °C, 100% a 80 °C)"
fi
echo
echo "Controle automático da ventoinha da GPU: systemctl status localai-fan"
EOF
  chmod +x "$BASE/diagnostico_fans.sh"
  mkdir -p static
  cat > static/index.html <<'EOF'
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Betina &amp; IA</title>
<style>
  /* Visual "terminal de segurança" (mesma família do app da bomba da piscina): fundo escuro, verde neon e ciano */
  :root {
    --bg:#05090D; --side:#080F15; --card:#0C161E; --txt:#D7F5E6; --mut:#6F9A8C; --line:#16313A;
    --acc:#00E58F; --acc-h:#33FFAA; --acc-soft:#073326; --ciano:#22D3EE; --user:#0F2330; --code:#030608;
    --ok:#00E58F; --warn:#FFB020; --err:#FF4D5E;
    --sombra:0 0 0 1px #16313A, 0 4px 22px rgba(0,229,143,.07);
    --sans:-apple-system,"Segoe UI",system-ui,"Noto Sans",Roboto,sans-serif;
    --serif:ui-monospace,"SF Mono",Menlo,Consolas,"DejaVu Sans Mono",monospace;
    --mono:ui-monospace,"SF Mono",Menlo,Consolas,"DejaVu Sans Mono",monospace;
  }
  :root[data-tema="claro"] {
    --bg:#F2F7F5; --side:#E6EFEB; --card:#FFFFFF; --txt:#0D2A22; --mut:#4E6F65; --line:#C9DAD3;
    --acc:#00855A; --acc-h:#006B49; --acc-soft:#D8F1E7; --ciano:#0A7C93; --user:#DDEBE6; --code:#E9F1EE;
    --ok:#00855A; --warn:#B26A00; --err:#C0283A;
    --sombra:0 2px 14px rgba(0,60,40,.08);
  }
  * { box-sizing:border-box; }
  html, body { height:100%; }
  body { margin:0; background:var(--bg); color:var(--txt); font:15px/1.5 var(--sans); -webkit-text-size-adjust:100%; }
  button { font:inherit; color:inherit; cursor:pointer; }
  button:disabled { opacity:.45; cursor:default; }
  [hidden] { display:none !important; }
  a { color:var(--acc); }

  #app { display:flex; height:100vh; height:100dvh; }

  /* ---------- barra lateral ---------- */
  #side { width:272px; flex:none; background:var(--side); border-right:1px solid var(--line); display:flex; flex-direction:column; padding:14px 10px 10px; gap:10px; z-index:30; }
  .marca { display:flex; align-items:center; gap:8px; padding:4px 8px; font:700 18px var(--mono); letter-spacing:.5px; color:var(--acc); text-shadow:0 0 12px rgba(0,229,143,.45); }
  .marca i { color:var(--ciano); font-style:normal; }
  .marca i { font-style:normal; color:var(--acc); font-size:22px; }
  .btn-nova { display:flex; align-items:center; gap:8px; width:100%; padding:9px 12px; border:1px solid var(--line); background:var(--card); border-radius:12px; font-weight:500; }
  .btn-nova:hover { border-color:var(--acc); }
  #convs { flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:2px; margin:0 -4px; padding:0 4px; }
  .rotulo { font-size:12px; color:var(--mut); padding:8px 8px 2px; }
  .conv { display:flex; align-items:center; gap:4px; padding:7px 8px; border-radius:10px; cursor:pointer; }
  .conv:hover, .conv.ativa { background:var(--user); }
  .conv span { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:14px; }
  .conv button { border:0; background:none; color:var(--mut); padding:2px 6px; border-radius:6px; visibility:hidden; }
  .conv:hover button { visibility:visible; }
  .conv button:hover { color:var(--err); }
  .side-foot { position:relative; border-top:1px solid var(--line); padding-top:10px; display:flex; flex-direction:column; gap:8px; }
  .btn-cfg { display:flex; align-items:center; gap:8px; width:100%; padding:8px 10px; border:0; background:none; border-radius:10px; text-align:left; }
  .btn-cfg:hover { background:var(--user); }
  #estadoLlm { font-size:12px; color:var(--mut); padding:0 10px; display:flex; align-items:center; gap:6px; }
  .bolinha { width:8px; height:8px; border-radius:50%; background:var(--mut); display:inline-block; }
  .bolinha.ok { background:var(--ok); } .bolinha.carregando { background:var(--warn); } .bolinha.desligado { background:var(--err); }
  #fundo { display:none; }

  /* ---------- área principal ---------- */
  main { flex:1; min-width:0; display:flex; flex-direction:column; position:relative; }
  .topo { display:flex; align-items:center; gap:10px; padding:10px 16px; min-height:52px; }
  .topo #titulo { font-weight:500; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; color:var(--mut); }
  #menuBtn { display:none; border:0; background:none; font-size:22px; padding:2px 8px; }
  #chips { display:flex; gap:6px; flex-wrap:wrap; font-size:12px; }
  #chips span { padding:3px 9px; border:1px solid var(--line); border-radius:999px; background:var(--card); color:var(--mut); white-space:nowrap; }
  #chips span.quente { color:var(--warn); border-color:var(--warn); } #chips span.perigo { color:var(--err); border-color:var(--err); }

  #log { flex:1; overflow-y:auto; scroll-behavior:smooth; }
  .coluna { max-width:760px; margin:0 auto; padding:8px 20px 24px; display:flex; flex-direction:column; gap:22px; min-height:100%; }
  .vazio { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; text-align:center; padding:24px 0 60px; gap:22px; }
  .vazio h1 { font:700 clamp(26px,5vw,36px)/1.2 var(--serif); margin:0; }
  .vazio h1 i { font-style:normal; color:var(--acc); margin-right:8px; }
  .sugestoes { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; max-width:640px; }
  .sugestoes button { border:1px solid var(--line); background:var(--card); border-radius:999px; padding:8px 14px; font-size:14px; }
  .sugestoes button:hover { border-color:var(--acc); background:var(--acc-soft); }

  .msg { display:flex; flex-direction:column; gap:6px; }
  .msg.user { align-items:flex-end; }
  .msg.user .corpo { background:var(--user); padding:10px 16px; border-radius:18px; max-width:85%; white-space:pre-wrap; word-wrap:break-word; }
  .msg.assistant .corpo { font:16px/1.65 var(--sans); word-wrap:break-word; overflow-wrap:anywhere; }
  .corpo p { margin:0 0 .85em; } .corpo p:last-child { margin-bottom:0; }
  .corpo h2, .corpo h3, .corpo h4 { font-family:var(--serif); margin:1.1em 0 .4em; line-height:1.3; }
  .corpo ul, .corpo ol { margin:.2em 0 .9em; padding-left:1.4em; } .corpo li { margin:.2em 0; }
  .corpo blockquote { margin:.6em 0; padding:.1em 1em; border-left:3px solid var(--line); color:var(--mut); }
  .corpo code { font:.88em var(--mono); background:var(--code); padding:.12em .4em; border-radius:6px; }
  .code { background:var(--code); border:1px solid var(--line); border-radius:12px; margin:.4em 0 1em; overflow:hidden; }
  .code-h { display:flex; justify-content:space-between; align-items:center; padding:6px 12px; font:12px var(--sans); color:var(--mut); border-bottom:1px solid var(--line); }
  .code-h button { border:0; background:none; color:var(--mut); font-size:12px; padding:2px 6px; border-radius:6px; }
  .code-h button:hover { color:var(--txt); background:var(--user); }
  .code pre { margin:0; padding:12px 14px; overflow-x:auto; } .code pre code { background:none; padding:0; font-size:13.5px; line-height:1.55; }
  .ferr { display:flex; gap:4px; opacity:.9; } .ferr button { border:0; background:none; color:var(--mut); font:12.5px var(--sans); padding:3px 8px; border-radius:8px; }
  .ferr button:hover { background:var(--user); color:var(--txt); }
  .aviso { font:13px var(--sans); color:var(--mut); }
  .msg.erro .corpo { color:var(--err); }
  .pensando { display:inline-flex; gap:5px; padding:6px 0; } .pensando i { width:7px; height:7px; border-radius:50%; background:var(--acc); opacity:.35; animation:pisca 1.2s infinite; }
  .pensando i:nth-child(2){animation-delay:.2s} .pensando i:nth-child(3){animation-delay:.4s}
  @keyframes pisca { 0%,80%,100%{opacity:.25} 40%{opacity:1} }

  /* cartão de criação (app / firmware) */
  .cartao { border:1px solid var(--line); background:var(--card); border-radius:16px; padding:14px 16px; font:15px var(--sans); box-shadow:var(--sombra); }
  .cartao.erro { border-color:var(--err); }
  .cartao .st { font-weight:500; }
  .cartao details { margin-top:10px; } .cartao summary { cursor:pointer; color:var(--mut); font-size:13.5px; }
  .cartao pre { background:var(--code); padding:10px 12px; border-radius:10px; overflow-x:auto; font:12.5px var(--mono); margin:8px 0 0; }
  a.dl { display:flex; align-items:center; gap:8px; width:fit-content; max-width:100%; margin-top:10px; padding:9px 14px; border-radius:12px; background:var(--acc); color:#03140D; text-decoration:none; font-weight:500; }
  a.dl:hover { background:var(--acc-h); } a.dl small { opacity:.85; font-weight:400; }
  .dica { color:var(--mut); font-size:13.5px; margin-top:8px; }

  /* ---------- campo de digitação ---------- */
  #status { min-height:20px; padding:0 20px 6px; text-align:center; font-size:13px; color:var(--mut); }
  .compor { padding:0 16px calc(14px + env(safe-area-inset-bottom)); }
  .caixa { max-width:760px; margin:0 auto; background:var(--card); border:1px solid var(--line); border-radius:24px; box-shadow:var(--sombra); padding:10px 12px 8px; position:relative; transition:border-color .15s, box-shadow .15s; }
  .caixa:focus-within { border-color:var(--acc); box-shadow:0 2px 18px rgba(198,97,63,.18); }
  #modoChips { display:flex; gap:6px; flex-wrap:wrap; padding:0 4px 6px; } #modoChips:empty { display:none; }
  #modoChips span { font-size:12.5px; background:var(--acc-soft); color:var(--acc); padding:3px 10px; border-radius:999px; }
  #txt { width:100%; border:0; outline:0; resize:none; background:none; color:var(--txt); font:16px/1.5 var(--sans); padding:6px 6px 4px; max-height:200px; min-height:28px; }
  #txt::placeholder { color:var(--mut); }
  .linha { display:flex; align-items:center; gap:4px; }
  .linha .esp { flex:1; }
  .ic { width:36px; height:36px; display:inline-flex; align-items:center; justify-content:center; border:0; background:none; border-radius:50%; font-size:19px; color:var(--mut); }
  .ic:hover { background:var(--user); color:var(--txt); }
  .ic.gravando { background:var(--err); color:#fff; animation:pulsa 1.3s infinite; }
  @keyframes pulsa { 50% { box-shadow:0 0 0 6px rgba(179,65,47,.2); } }
  #send, #stop { width:36px; height:36px; border-radius:50%; border:0; display:inline-flex; align-items:center; justify-content:center; font-size:18px; }
  #send { background:var(--acc); color:#03140D; } #send:hover:not(:disabled) { background:var(--acc-h); }
  :root[data-tema="claro"] a.dl, :root[data-tema="claro"] #send, :root[data-tema="claro"] .bt.pri { color:#fff; }
  #stop { background:var(--txt); color:var(--bg); }
  .rodape { text-align:center; font-size:12px; color:var(--mut); padding-top:8px; }

  /* menus flutuantes */
  .menu { position:absolute; background:var(--card); border:1px solid var(--line); border-radius:14px; box-shadow:0 8px 30px rgba(0,0,0,.18); padding:6px; z-index:50; min-width:250px; }
  .menu.acima { bottom:calc(100% + 8px); left:0; }
  .menu .sec { font-size:12px; color:var(--mut); padding:8px 10px 2px; }
  .menu label, .menu .item { display:flex; align-items:center; gap:10px; padding:8px 10px; border-radius:9px; cursor:pointer; width:100%; border:0; background:none; text-align:left; }
  .menu label:hover, .menu .item:hover { background:var(--user); }
  .menu select { width:100%; margin:4px 0 6px; }
  select, input[type=range], input[type=text], .campo { font:inherit; color:var(--txt); background:var(--bg); border:1px solid var(--line); border-radius:10px; padding:7px 9px; }
  select, input[type=range] { width:100%; }
  input[type=range] { padding:0; accent-color:var(--acc); }

  /* janelas */
  dialog { background:var(--card); color:var(--txt); border:1px solid var(--line); border-radius:18px; width:min(640px,94vw); max-height:86vh; max-height:86dvh; padding:20px; box-shadow:0 20px 60px rgba(0,0,0,.3); }
  dialog::backdrop { background:rgba(0,0,0,.45); }
  dialog h2 { font:700 16px var(--mono); margin:0 0 4px; color:var(--acc); letter-spacing:.4px; }
  dialog h2::before { content:"// "; color:var(--ciano); }
  .bt { border:1px solid var(--line); background:var(--card); border-radius:10px; padding:7px 14px; } .bt:hover:not(:disabled) { border-color:var(--acc); }
  .bt.pri { background:var(--acc); border-color:var(--acc); color:#03140D; } .bt.pri:hover:not(:disabled) { background:var(--acc-h); }
  .lista { display:flex; flex-direction:column; gap:8px; margin:12px 0; max-height:52vh; max-height:52dvh; overflow-y:auto; }
  .item-l { border:1px solid var(--line); border-radius:12px; padding:10px 12px; display:flex; gap:10px; align-items:flex-start; font-size:14px; }
  .item-l .t { flex:1; word-break:break-word; white-space:pre-wrap; } .item-l small { display:block; color:var(--mut); }
  .barra { height:6px; background:var(--line); border-radius:99px; overflow:hidden; margin-top:6px; width:100%; } .barra i { display:block; height:100%; background:var(--acc); width:0; transition:width .4s; }
  .copia { font:13px var(--mono); word-break:break-all; background:var(--bg); border:1px solid var(--line); border-radius:10px; padding:7px 10px; margin:5px 0; }
  table.sis { width:100%; border-collapse:collapse; font-size:14px; margin-top:10px; } table.sis td { padding:6px 4px; border-bottom:1px solid var(--line); } table.sis td:last-child { text-align:right; font-variant-numeric:tabular-nums; }

  /* anexos e imagens */
  #anexos { display:flex; gap:8px; flex-wrap:wrap; padding:0 4px 8px; } #anexos:empty { display:none; }
  .anx { display:flex; align-items:center; gap:8px; max-width:100%; background:var(--user); border:1px solid var(--line); border-radius:12px; padding:5px 8px; font-size:13px; }
  .anx img { width:34px; height:34px; object-fit:cover; border-radius:8px; }
  .anx .ico { font-size:20px; } .anx .nome { max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .anx small { color:var(--mut); } .anx button { border:0; background:none; color:var(--mut); padding:0 4px; font-size:15px; }
  .anx button:hover { color:var(--err); }
  .anx.base { background:var(--acc-soft); border-color:var(--acc); }
  .msg.user .anexos-msg { display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; margin-bottom:4px; max-width:85%; }
  .msg.user .anexos-msg .anx { background:var(--card); }
  .miniatura { max-width:min(100%,420px); width:100%; border-radius:14px; border:1px solid var(--line); margin-top:10px; display:block; background:var(--code); }
  .acoes-cartao { display:flex; gap:8px; flex-wrap:wrap; margin-top:10px; }
  .acoes-cartao button, .bt-mini { border:1px solid var(--line); background:var(--card); border-radius:10px; padding:7px 12px; font-size:13.5px; }
  .acoes-cartao button:hover, .bt-mini:hover { border-color:var(--acc); color:var(--acc); }
  .caixa.arrastando { border-color:var(--acc); background:var(--acc-soft); }
  #imgOpts { padding:2px 10px 6px; } #imgOpts label { padding:5px 0; } #imgOpts .linha-opt { display:flex; align-items:center; gap:8px; font-size:13px; color:var(--mut); }
  #imgOpts input[type=range] { flex:1; }
  .barra-img { height:6px; background:var(--line); border-radius:99px; overflow:hidden; margin-top:8px; } .barra-img i { display:block; height:100%; background:var(--acc); width:0; transition:width .5s; }
  .sec-btn { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:6px 10px; font-size:13px; color:var(--mut); }
  /* ---------- celular ---------- */
  @media (max-width: 860px) {
    #side { position:fixed; inset:0 auto 0 0; width:min(300px,86vw); transform:translateX(-102%); transition:transform .22s; box-shadow:none; }
    body.lateral #side { transform:none; box-shadow:0 0 40px rgba(0,0,0,.35); }
    body.lateral #fundo { display:block; position:fixed; inset:0; background:rgba(0,0,0,.4); z-index:20; }
    #menuBtn { display:inline-block; }
    #chips { display:none; }
    .coluna { padding:6px 14px 20px; gap:18px; }
    .msg.user .corpo { max-width:92%; }
    .compor { padding:0 10px calc(10px + env(safe-area-inset-bottom)); }
    .rodape { display:none; }
  }
</style>
</head>
<body>
<div id="app">
  <aside id="side">
    <div class="marca"><i>✦</i> Betina &amp; IA</div>
    <button class="btn-nova" id="new">＋ Nova conversa</button>
    <div id="convs"></div>
    <div class="side-foot">
      <div id="estadoLlm"><span class="bolinha" id="bolinha"></span><span id="estadoTxt">Verificando o modelo…</span></div>
      <div class="menu acima" id="gearMenu" hidden style="left:0;right:0">
        <button class="item" id="voiceBtn">🔊 Voz</button>
        <button class="item" id="llmBtn">🤖 Modelo de IA</button>
        <button class="item" id="memBtn">🧠 Memória</button>
        <button class="item" id="filesBtn">📁 Arquivos criados</button>
        <button class="item" id="sisBtn">🖥 Sistema e ventoinhas</button>
        <button class="item" id="libBtn">📚 Biblioteca</button>
        <button class="item" id="pairBtn" hidden>📲 Conectar celular</button>
        <button class="item" id="cfgBtn" hidden>⚙ Servidor do app</button>
        <div class="sec">Aparência</div>
        <button class="item" id="temaBtn">🌓 Tema: automático</button>
      </div>
      <button class="btn-cfg" id="gearBtn">⚙ Configurações</button>
    </div>
  </aside>
  <div id="fundo"></div>

  <main>
    <div class="topo">
      <button id="menuBtn" aria-label="Abrir menu">☰</button>
      <div id="titulo">Nova conversa</div>
      <div id="chips"></div>
    </div>
    <div id="log"><div class="coluna" id="coluna"></div></div>
    <div id="status"></div>
    <div class="compor">
      <div class="caixa">
        <div class="menu acima" id="plusMenu" hidden>
          <div class="sec">O que você quer fazer?</div>
          <select id="modo">
            <option value="chat">💬 Conversa</option>
            <option value="app">📱 App Android rápido (1 arquivo)</option>
            <option value="appx">📱 App Android avançado (vários arquivos)</option>
            <option value="esp32">🔌 Firmware ESP32 (.ino / .bin)</option>
            <option value="web">🌐 Página web</option>
            <option value="python">🐍 Programa Python</option>
            <option value="img">🎨 Criar ou modificar imagem</option>
          </select>
          <select id="placa" hidden></select>
          <div id="imgOpts" hidden>
            <select id="imgTam"><option value="512x512">Quadrada 512×512</option><option value="512x768">Em pé 512×768</option><option value="768x512">Deitada 768×512</option></select>
            <div class="linha-opt">Qualidade (passos) <input type="range" id="imgPassos" min="1" max="8" value="4"> <span id="imgPassosV">4</span></div>
            <div class="linha-opt" id="imgForcaLinha" hidden>Quanto mudar <input type="range" id="imgForca" min="0.2" max="0.95" step="0.05" value="0.6"> <span id="imgForcaV">0.60</span></div>
            <label title="A IA traduz o pedido para inglês e o detalha; os modelos de imagem entendem muito melhor"><input type="checkbox" id="imgMelhorar" checked> ✨ Melhorar o pedido com a IA</label>
            <div class="sec-btn"><span id="imgModeloTxt">Modelo de imagens: verificando…</span><button class="bt-mini" id="imgBaixar" hidden>Baixar (2 GB)</button></div>
            <div class="barra-img" id="imgBaixarBarra" hidden><i></i></div>
          </div>
          <div class="sec">Opções</div>
          <label><input type="checkbox" id="web"> 🌐 Pesquisar na web</label>
          <label title="Guarda na memória o que descobrir pesquisando"><input type="checkbox" id="learn" checked> 📚 Aprender com as pesquisas</label>
          <label><input type="checkbox" id="speak"> 🔊 Falar as respostas</label>
        </div>
        <div id="anexos"></div>
        <div id="modoChips"></div>
        <textarea id="txt" rows="1" placeholder="Escreva sua mensagem…"></textarea>
        <div class="linha">
          <button class="ic" id="plus" title="Modo e opções" aria-label="Modo e opções">＋</button>
          <button class="ic" id="clipe" title="Anexar arquivos: imagens, .ino, .bin, .apk, código" aria-label="Anexar">📎</button>
          <input type="file" id="arq" multiple hidden accept="image/*,.ino,.bin,.apk,.py,.java,.kt,.xml,.gradle,.html,.js,.css,.json,.md,.txt,.c,.cpp,.h,.ts,.sh,.yaml,.yml">
          <span class="esp"></span>
          <button class="ic" id="mic" title="Falar (clique de novo para enviar)" aria-label="Falar">🎤</button>
          <button id="stop" hidden title="Parar (Esc)" aria-label="Parar">■</button>
          <button id="send" title="Enviar (Enter)" aria-label="Enviar" disabled>↑</button>
        </div>
      </div>
      <div class="rodape">A IA roda no seu PC. Confira informações importantes.</div>
    </div>
  </main>
</div>

<dialog id="vozDlg">
  <h2>Voz da IA</h2>
  <div class="dica">Escolha uma voz e clique em Testar. As vozes de outros países leem o português com sotaque.</div>
  <div style="margin:14px 0 4px">Voz</div><select id="vozSel"></select>
  <div style="margin:14px 0 4px">Velocidade: <span id="velVal">1.00</span>×</div>
  <input type="range" id="vel" min="0.7" max="1.4" step="0.05" value="1">
  <div style="display:flex;gap:8px;margin-top:16px"><button class="bt pri" id="vozTest">▶ Testar</button><button class="bt" id="vozClose">Fechar</button></div>
</dialog>

<dialog id="llmDlg">
  <h2>Modelo de IA</h2>
  <div class="dica">Troque conforme a tarefa. Mais preciso = mais lento. Ao trocar, o modelo recarrega (alguns minutos).</div>
  <div class="lista" id="llmLista"></div>
  <div class="dica" id="llmMsg"></div>
  <button class="bt" id="llmClose">Fechar</button>
</dialog>

<dialog id="memDlg">
  <h2>Memória de longo prazo</h2>
  <div class="dica">Tudo que a IA sabe sobre você e o que aprendeu. Apague o que estiver errado.</div>
  <div class="lista" id="memlist"></div>
  <div style="display:flex;gap:8px"><input type="text" class="campo" id="memNew" placeholder="Ensinar algo novo…" style="flex:1"><button class="bt pri" id="memAdd">Ensinar</button><button class="bt" id="memClose">Fechar</button></div>
</dialog>

<dialog id="filesDlg">
  <h2>Arquivos que a IA criou</h2>
  <div class="dica">Aplicativos (.apk) e firmwares (.bin). Ficam guardados no PC, na pasta ~/localai/apps.</div>
  <div class="lista" id="fileslist"></div>
  <button class="bt" id="filesClose">Fechar</button>
</dialog>

<dialog id="libDlg">
  <h2>Biblioteca</h2>
  <div class="dica">Documentação e exemplos guardados no disco grande. A IA consulta isto sozinha ao responder e ao criar apps e firmware. Coloque seus próprios arquivos na pasta <b>meus</b>.</div>
  <table class="sis" id="libTab"></table>
  <div class="dica" id="libNota"></div>
  <div style="display:flex;gap:8px;margin-top:10px"><input id="libQ" placeholder="Buscar na biblioteca…" style="flex:1"><button class="bt" id="libBusca">Buscar</button></div>
  <div class="lista" id="libRes" style="max-height:34vh;overflow:auto"></div>
  <div style="margin-top:14px;display:flex;gap:8px"><button class="bt" id="libIdx">Atualizar índice</button><button class="bt" id="libClose">Fechar</button></div>
</dialog>

<dialog id="sisDlg">
  <h2>Sistema e ventoinhas</h2>
  <div class="dica">Leituras em tempo real do PC. A ventoinha da placa de vídeo sobe sozinha conforme a temperatura.</div>
  <table class="sis" id="sisTab"></table>
  <div class="dica" id="sisNota"></div>
  <div style="margin-top:14px"><button class="bt" id="sisClose">Fechar</button></div>
</dialog>

<dialog id="pairDlg">
  <h2>Conectar o celular</h2>
  <div class="dica" id="pairEstado"></div>
  <div style="margin-top:12px"><b>Endereço do servidor</b> (o do Tailscale funciona de qualquer lugar)</div>
  <div id="pairUrls"></div>
  <div style="margin-top:12px"><b>Token (senha)</b></div>
  <div id="pairToken" class="copia"></div>
  <div class="dica">No celular: instale o Tailscale, entre com a mesma conta e abra o app Betina &amp; IA: ele acha este PC sozinho (nome <b>betina</b>) e, pela VPN, não pede token. O token só vale fora da VPN; não mostre a ninguém.</div>
  <div style="margin-top:14px"><button class="bt" id="pairClose">Fechar</button></div>
</dialog>

<script>
const $ = id => document.getElementById(id);
const NATIVO = typeof window.AndroidBridge !== 'undefined';   // dentro do app Android
const txt = $('txt'), statusEl = $('status'), coluna = $('coluna'), logEl = $('log');
let busy = false, ctrl = null, falando = false, descartar = false, gravandoNativo = false;
const setStatus = t => statusEl.textContent = t || '';
const guarda = (k, v) => { try { localStorage.setItem(k, v); } catch (e) {} };
const le = (k, d) => { try { return localStorage.getItem(k) ?? d; } catch (e) { return d; } };

/* ======================= Markdown seguro ======================= */
const esc = s => s.replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function inline(s) {
  s = esc(s);
  s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
  s = s.replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  return s;
}
function mdHtml(text) {
  const out = [], L = text.replace(/\r/g, '').split('\n'); let i = 0;
  while (i < L.length) {
    const ln = L[i], f = ln.match(/^```\s*([\w+#.-]*)\s*$/);
    if (f) {
      const cod = []; i++;
      while (i < L.length && !/^```\s*$/.test(L[i])) cod.push(L[i++]);
      i++;
      out.push(`<div class="code"><div class="code-h"><span>${esc(f[1] || 'código')}</span><button class="copiar" type="button">Copiar</button></div><pre><code>${esc(cod.join('\n'))}</code></pre></div>`);
      continue;
    }
    const h = ln.match(/^(#{1,4})\s+(.*)/);
    if (h) { const n = Math.min(4, h[1].length + 1); out.push(`<h${n}>${inline(h[2])}</h${n}>`); i++; continue; }
    if (/^\s*([-*•]|\d+[.)])\s+/.test(ln)) {
      const ord = /^\s*\d+[.)]/.test(ln), itens = [];
      while (i < L.length && /^\s*([-*•]|\d+[.)])\s+/.test(L[i])) itens.push(`<li>${inline(L[i++].replace(/^\s*([-*•]|\d+[.)])\s+/, ''))}</li>`);
      out.push(`<${ord ? 'ol' : 'ul'}>${itens.join('')}</${ord ? 'ol' : 'ul'}>`); continue;
    }
    if (/^>\s?/.test(ln)) {
      const q = []; while (i < L.length && /^>\s?/.test(L[i])) q.push(inline(L[i++].replace(/^>\s?/, '')));
      out.push(`<blockquote>${q.join('<br>')}</blockquote>`); continue;
    }
    if (!ln.trim()) { i++; continue; }
    const p = [];
    while (i < L.length && L[i].trim() && !/^(```|#{1,4}\s|>\s?|\s*([-*•]|\d+[.)])\s+)/.test(L[i])) p.push(inline(L[i++]));
    out.push(`<p>${p.join('<br>')}</p>`);
  }
  return out.join('');
}
function copiaTexto(t) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(t);
  const a = document.createElement('textarea'); a.value = t; a.style.position = 'fixed'; a.style.opacity = '0';
  document.body.appendChild(a); a.select(); try { document.execCommand('copy'); } catch (e) {} a.remove(); return Promise.resolve();
}
coluna.addEventListener('click', e => {
  const b = e.target.closest('.copiar'); if (!b) return;
  copiaTexto(b.closest('.code').querySelector('code').textContent).then(() => { b.textContent = 'Copiado ✓'; setTimeout(() => b.textContent = 'Copiar', 1500); });
});

/* ======================= Conversas (guardadas no navegador) ======================= */
let convs = [], atualId = null;
try { convs = JSON.parse(le('iaConvs', '[]')); } catch (e) { convs = []; }
const novaConv = () => ({id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6), titulo: '', msgs: [], t: Date.now()});
const conv = () => convs.find(c => c.id === atualId);
function salva() {
  const cs = convs.filter(c => c.msgs.length).sort((a, b) => b.t - a.t).slice(0, 60);
  const pode = cs.map(c => ({...c, msgs: c.msgs.slice(-80)}));
  guarda('iaConvs', JSON.stringify(pode)); guarda('iaAtual', atualId || '');
}
function listaLateral() {
  const box = $('convs'); box.textContent = '';
  const cs = convs.filter(c => c.msgs.length).sort((a, b) => b.t - a.t);
  if (cs.length) { const r = document.createElement('div'); r.className = 'rotulo'; r.textContent = 'Conversas'; box.appendChild(r); }
  for (const c of cs) {
    const d = document.createElement('div'); d.className = 'conv' + (c.id === atualId ? ' ativa' : '');
    const sp = document.createElement('span'); sp.textContent = c.titulo || 'Nova conversa';
    const x = document.createElement('button'); x.textContent = '✕'; x.title = 'Apagar conversa';
    x.onclick = ev => { ev.stopPropagation(); apaga(c.id); };
    d.onclick = () => abre(c.id); d.append(sp, x); box.appendChild(d);
  }
}
function abre(id) {
  if (busy) return;
  pararVoz(); atualId = id; document.body.classList.remove('lateral'); desenha(); salva();
}
function nova() {
  if (busy) return;
  pararVoz(); const c = conv();
  // conversa nova começa do zero: modo Conversa, sem anexos nem item a modificar
  anexos = []; baseMod = null; $('modo').value = 'chat'; $('modo').onchange(); $('web').checked = false; $('speak').checked = false; atualizaChips(); renderAnexos();
  if (!(c && !c.msgs.length)) { const n = novaConv(); convs.push(n); atualId = n.id; }
  document.body.classList.remove('lateral'); desenha(); txt.focus();
}
function apaga(id) {
  convs = convs.filter(c => c.id !== id);
  if (atualId === id) { const n = novaConv(); convs.push(n); atualId = n.id; }
  desenha(); salva();
}
$('new').onclick = nova;

/* ======================= Desenho das mensagens ======================= */
const SUGESTOES = [
  ['📱 Criar um app Android de lista de compras', 'app', 'um app de lista de compras com campo para adicionar itens e marcar como comprado'],
  ['🔌 Firmware ESP32 que pisca um LED', 'esp32', 'piscar o LED da placa a cada meio segundo e escrever na serial'],
  ['🌐 Novidades de hoje na web', 'chat', 'Quais são as principais notícias de tecnologia hoje?', true],
  ['💡 Explicar o que é o Tailscale', 'chat', 'Explique de forma simples o que é o Tailscale e para que serve'],
  ['🎨 Criar uma imagem', 'img', 'um farol numa ilha ao pôr do sol, estilo pintura a óleo'],
  ['🐍 Programa Python', 'python', 'um programa que renomeia todos os arquivos de uma pasta colocando a data na frente'],
];
function boasVindas() {
  const d = document.createElement('div'); d.className = 'vazio'; d.id = 'vazio';
  d.innerHTML = '<h1><i>✦</i>Olá! Como posso ajudar hoje?</h1>';
  const s = document.createElement('div'); s.className = 'sugestoes';
  for (const [rot, modo, texto, web] of SUGESTOES) {
    const b = document.createElement('button'); b.textContent = rot;
    b.onclick = () => { $('modo').value = modo; $('modo').onchange(); $('web').checked = !!web; atualizaChips(); txt.value = texto; autoAltura(); txt.focus(); };
    s.appendChild(b);
  }
  d.appendChild(s); return d;
}
function criaMsg(m) {
  const el = document.createElement('div'); el.className = 'msg ' + m.role + (m.erro ? ' erro' : '');
  if (m.kind === 'criacao') { el.className = 'msg assistant'; el.appendChild(cartaoCriacao(m)); return el; }
  if (m.role === 'user' && ((m.anexos && m.anexos.length) || m.modifica)) {
    const lista = document.createElement('div'); lista.className = 'anexos-msg';
    if (m.modifica) { const d = document.createElement('div'); d.className = 'anx base'; d.innerHTML = '<span class="ico">✏️</span><span class="nome"></span>'; d.querySelector('.nome').textContent = m.modifica; lista.appendChild(d); }
    for (const a of m.anexos || []) {
      const d = document.createElement('div'); d.className = 'anx';
      if (a.tipo === 'imagem') { const im = document.createElement('img'); im.src = a.url; d.appendChild(im); } else { const i = document.createElement('span'); i.className = 'ico'; i.textContent = ICONES[a.tipo] || '📄'; d.appendChild(i); }
      const n = document.createElement('span'); n.className = 'nome'; n.textContent = a.name; d.appendChild(n); lista.appendChild(d);
    }
    el.appendChild(lista);
  }
  const corpo = document.createElement('div'); corpo.className = 'corpo'; el.appendChild(corpo);
  if (m.role === 'user') corpo.textContent = m.content; else corpo.innerHTML = mdHtml(m.content || '');
  if (m.role === 'assistant' && !m.erro && m.content) acoesMsg(el, m);
  return el;
}
function acoesMsg(el, m) {
  el.querySelector('.ferr')?.remove(); el.querySelector('.aviso')?.remove();
  if (m.interrompido) { const a = document.createElement('div'); a.className = 'aviso'; a.textContent = '⏹ Interrompido por você'; el.appendChild(a); }
  const f = document.createElement('div'); f.className = 'ferr';
  const c = document.createElement('button'); c.textContent = '⧉ Copiar'; c.onclick = () => copiaTexto(m.content).then(() => { c.textContent = 'Copiado ✓'; setTimeout(() => c.textContent = '⧉ Copiar', 1500); });
  const l = document.createElement('button'); l.textContent = '📌 Lembrar disso';
  l.onclick = async () => {
    const i = conv().msgs.indexOf(m), perg = conv().msgs.slice(0, i).reverse().find(x => x.role === 'user');
    try { await salvaMemoria(`Pergunta: ${perg ? perg.content : ''}\nResposta: ${m.content}`, 'usuario'); l.textContent = '✔ Guardado'; l.disabled = true; } catch (e) { l.textContent = 'Erro ao guardar'; }
  };
  f.append(c, l); el.appendChild(f);
}
function desenha() {
  coluna.textContent = '';
  const c = conv();
  if (!c || !c.msgs.length) { coluna.appendChild(boasVindas()); }
  else for (const m of c.msgs) coluna.appendChild(criaMsg(m));
  $('titulo').textContent = (c && c.titulo) || 'Nova conversa';
  document.title = (c && c.titulo) ? c.titulo + ' · Betina & IA' : 'Betina & IA';
  listaLateral(); desceFim(true);
}
function desceFim(forca) {
  const perto = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 140;
  if (forca || perto) logEl.scrollTop = logEl.scrollHeight;
}
function tiraBoasVindas() { $('vazio')?.remove(); }
let _raf = null;
function renderiza(el, m) {  // atualiza o texto da resposta durante o streaming (no máx. 1x por quadro)
  if (_raf) return;
  _raf = requestAnimationFrame(() => { _raf = null; el.querySelector('.corpo').innerHTML = mdHtml(m.content || ''); desceFim(false); });
}

/* ======================= Memória ======================= */
async function salvaMemoria(text, source) {
  const r = await fetch('/memory', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text, source})});
  if (!r.ok) throw new Error('Servidor respondeu ' + r.status);
  return r.json();
}
const TEACH = /^\s*(lembre-se|lembre|guarde|aprenda|anote)(\s+disso|\s+que)?[:,]?\s+(.{4,})/is;

/* ======================= Anexos e modificação ======================= */
let anexos = [];      // arquivos enviados e ainda não usados: {id, name, tipo, size, url, analise, entrega}
let baseMod = null;   // item que a IA já criou e que será modificado: {id, nome, kind}
const ICONES = {apk: '📱', bin: '🔌', ino: '🔌', imagem: '🖼️', texto: '📄', img: '🎨', web: '🌐', python: '🐍'};
function renderAnexos() {
  const box = $('anexos'); box.textContent = '';
  if (baseMod) {
    const d = document.createElement('div'); d.className = 'anx base';
    d.innerHTML = '<span class="ico">✏️</span><span class="nome"></span><small>será modificado</small><button type="button" title="Cancelar a modificação">✕</button>';
    d.querySelector('.nome').textContent = baseMod.nome; d.querySelector('button').onclick = () => { baseMod = null; renderAnexos(); };
    box.appendChild(d);
  }
  for (const a of anexos) {
    const d = document.createElement('div'); d.className = 'anx'; d.title = (a.analise && a.analise.resumo) || a.name;
    if (a.tipo === 'imagem') { const im = document.createElement('img'); im.src = a.url; d.appendChild(im); }
    else { const i = document.createElement('span'); i.className = 'ico'; i.textContent = ICONES[a.tipo] || '📄'; d.appendChild(i); }
    const n = document.createElement('span'); n.className = 'nome'; n.textContent = a.name; const t = document.createElement('small'); t.textContent = fmtTam(a.size);
    const x = document.createElement('button'); x.type = 'button'; x.textContent = '✕'; x.title = 'Remover';
    x.onclick = () => { fetch('/upload/' + a.id, {method: 'DELETE'}).catch(() => {}); anexos = anexos.filter(y => y !== a); renderAnexos(); };
    d.append(n, t, x); box.appendChild(d);
  }
  atualizaBotoes(); if (typeof atualizaForca === 'function') atualizaForca();
}
async function sobe(arquivos) {
  for (const f of arquivos) {
    if (f.size > 100 * 1048576) { setStatus('“' + f.name + '” passa de 100 MB.'); continue; }
    setStatus('Enviando ' + f.name + '…');
    try {
      const fd = new FormData(); fd.append('arquivo', f, f.name || 'imagem.png');
      const r = await fetch('/upload', {method: 'POST', body: fd});
      if (!r.ok) throw new Error((await r.json()).detail || ('Servidor respondeu ' + r.status));
      const a = await r.json(); anexos.push(a);
      if (a.tipo === 'apk' && a.entrega) { baseMod = {id: a.entrega, nome: (a.analise && a.analise.nome) || a.name, kind: 'apk'}; setStatus('Este app foi criado aqui: dá para modificar pelo código-fonte. Escolha “App Android” e peça a mudança.'); }
      else setStatus(a.analise && a.analise.resumo ? a.analise.resumo.split('\n')[0] : '');
      sugereModo(a);
    } catch (e) { setStatus('Não consegui enviar: ' + e.message); }
  }
  renderAnexos();
}
function sugereModo(a) {   // escolhe o modo mais provável para o tipo de arquivo, se ainda estiver em Conversa
  if ($('modo').value !== 'chat') return;
  const alvo = {ino: 'esp32', bin: null, apk: null, imagem: 'img'}[a.tipo];
  if (alvo) { $('modo').value = alvo; $('modo').onchange(); }
}
$('clipe').onclick = () => $('arq').click();
$('arq').onchange = () => { sobe([...$('arq').files]); $('arq').value = ''; };
const caixaEl = document.querySelector('.caixa');
['dragenter', 'dragover'].forEach(ev => caixaEl.addEventListener(ev, e => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { e.preventDefault(); caixaEl.classList.add('arrastando'); } }));
['dragleave', 'drop'].forEach(ev => caixaEl.addEventListener(ev, () => caixaEl.classList.remove('arrastando')));
caixaEl.addEventListener('drop', e => { if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); sobe([...e.dataTransfer.files]); } });
txt.addEventListener('paste', e => { const fs = [...(e.clipboardData?.files || [])]; if (fs.length) { e.preventDefault(); sobe(fs); } });
function modificar(item) {   // botão ✏ Modificar de um cartão ou da lista de arquivos
  baseMod = {id: item.id, nome: item.name || item.nome, kind: item.kind};
  const m = {apk: item.modo === 'avancado' ? 'appx' : 'app', bin: 'esp32', web: 'web', python: 'python', img: 'img'}[item.kind] || 'chat';
  $('modo').value = m; $('modo').onchange(); renderAnexos(); document.getElementById('filesDlg').close(); txt.focus();
  setStatus('Descreva o que mudar em “' + baseMod.nome + '”.');
}

/* ======================= Enviar mensagem ======================= */
const historicoModelo = c => c.msgs.filter(m => !m.kind && !m.ignorar && m.content).map(m => ({role: m.role, content: m.content}));
function mensagemAmigavel(e) {
  const caiu = e instanceof TypeError || /network|fetch/i.test(e.message);
  return caiu ? 'A conexão com o servidor caiu. O modelo pode ter reiniciado (falta de memória da placa?). Espere alguns minutos e tente de novo.' : e.message;
}
async function send(text) {
  text = (text || '').trim();
  if ((!text && !podeEnviarVazio()) || busy) return;
  if (!text) text = '(compilar o sketch anexado como está)';
  pararVoz(); busy = true; atualizaBotoes();
  txt.value = ''; autoAltura(); setStatus('');
  let c = conv(); if (!c) { c = novaConv(); convs.push(c); atualId = c.id; }
  if (!c.titulo) c.titulo = text.length > 42 ? text.slice(0, 42) + '…' : text;
  c.t = Date.now();
  const usados = anexos.slice(), base = baseMod;
  const mu = {role: 'user', content: text, anexos: usados.map(a => ({name: a.name, tipo: a.tipo, url: a.url, size: a.size}))};
  if (base) mu.modifica = base.nome;
  c.msgs.push(mu); anexos = []; baseMod = null; renderAnexos();
  tiraBoasVindas(); coluna.appendChild(criaMsg(mu)); $('titulo').textContent = c.titulo; desceFim(true);
  try {
    if ($('modo').value !== 'chat') { await criarArquivo($('modo').value, text, c, usados, base); return; }
    const historico = historicoModelo(c);
    const teach = text.match(TEACH);
    if (teach) { try { await salvaMemoria(teach[3].trim(), 'usuario'); } catch (e) {} }
    const wantWeb = $('web').checked; let webResults = 0;
    const ma = {role: 'assistant', content: ''}; c.msgs.push(ma);
    const el = criaMsg(ma); el.querySelector('.corpo').innerHTML = '<span class="pensando"><i></i><i></i><i></i></span>';
    coluna.appendChild(el); desceFim(true);
    setStatus(wantWeb ? 'Pesquisando na web e pensando…' : 'Pensando…');
    try {
      ctrl = new AbortController();
      const r = await fetch('/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, signal: ctrl.signal,
        body: JSON.stringify({messages: historico.slice(-14), web: wantWeb, anexos: usados.map(a => a.id)})});
      if (!r.ok) throw new Error('Servidor respondeu ' + r.status);
      webResults = parseInt(r.headers.get('X-Web-Results') || '0', 10);
      const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
      for (;;) {
        const {value, done} = await reader.read(); if (done) break;
        buf += dec.decode(value, {stream: true});
        const linhas = buf.split('\n'); buf = linhas.pop();
        for (const ln of linhas) {
          if (!ln.startsWith('data:')) continue;
          const d = ln.slice(5).trim(); if (!d || d === '[DONE]') continue;
          const j = JSON.parse(d); if (j.error) throw new Error(j.error);
          ma.content += (j.choices?.[0]?.delta?.content) || ''; setStatus(''); renderiza(el, ma);
        }
      }
      el.querySelector('.corpo').innerHTML = mdHtml(ma.content); acoesMsg(el, ma); setStatus('');
      if (wantWeb && webResults === 0 && ma.content) setStatus('Não consegui pesquisar na web agora (sem internet?). Respondi só com o que eu sei.');
      if (webResults > 0 && $('learn').checked && ma.content && !teach) {
        try { await salvaMemoria(`Pergunta: ${text}\nResposta (pesquisa na web em ${new Date().toLocaleDateString('pt-BR')}): ${ma.content}`, 'web'); } catch (e) {}
      }
      if ($('speak').checked && ma.content) speak(ma.content);
    } catch (e) {
      if (e.name === 'AbortError') {
        if (ma.content) { ma.interrompido = true; el.querySelector('.corpo').innerHTML = mdHtml(ma.content); acoesMsg(el, ma); }
        else { c.msgs.splice(c.msgs.indexOf(ma), 1); el.remove(); mu.ignorar = true; }
        setStatus('');
      } else {
        ma.erro = true; ma.ignorar = true; mu.ignorar = true;
        ma.content = (ma.content ? ma.content + '\n\n' : '') + '⚠ ' + mensagemAmigavel(e);
        el.className = 'msg assistant erro'; el.querySelector('.corpo').innerHTML = mdHtml(ma.content); setStatus('');
      }
    }
  } finally { busy = false; ctrl = null; atualizaBotoes(); salva(); listaLateral(); txt.focus(); }
}

/* ======================= Voz: falar as respostas ======================= */
function pedacos(text, max = 220) {
  const frases = text.split(/(?<=[.!?…])\s+|\n+/).map(f => f.trim()).filter(Boolean);
  const out = []; let cur = '';
  for (const f of frases) { if (cur && (cur + ' ' + f).length > max) { out.push(cur); cur = f; } else cur = (cur + ' ' + f).trim(); }
  if (cur) out.push(cur); return out;
}
let vozPref = {voice: '', speed: 1.0};
try { vozPref = Object.assign(vozPref, JSON.parse(le('vozPref', '{}'))); } catch (e) {}
const salvaVoz = () => guarda('vozPref', JSON.stringify(vozPref));
async function audioDe(texto) {
  const r = await fetch('/tts', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({text: texto, voice: vozPref.voice || null, speed: vozPref.speed})});
  if (!r.ok) throw new Error((await r.json()).detail || r.status);
  return URL.createObjectURL(await r.blob());
}
let falaId = 0, falaAtual = null;
function pararVoz() { falaId++; falando = false; if (falaAtual) falaAtual.pause(); atualizaBotoes(); }
async function speak(text) {
  const limpo = text.replace(/```[\s\S]*?```/g, ' (código omitido) ').replace(/https?:\/\/\S+/g, ' link ').replace(/[*_#`>]/g, '');
  const partes = pedacos(limpo).slice(0, 40); if (!partes.length) return;
  const id = ++falaId; falando = true; atualizaBotoes(); setStatus('Gerando voz…');
  try {
    let proximo = audioDe(partes[0]);
    for (let i = 0; i < partes.length; i++) {
      const url = await proximo; if (id !== falaId) return;
      if (i + 1 < partes.length) proximo = audioDe(partes[i + 1]);
      const a = new Audio(url); falaAtual = a; setStatus('Falando… (■ ou Esc para interromper)');
      await a.play(); await new Promise(res => { a.onended = res; a.onpause = res; });
      URL.revokeObjectURL(url); if (id !== falaId) return;
    }
  } catch (e) { setStatus('Voz indisponível: ' + e.message); return; }
  finally { if (id === falaId) { falando = false; atualizaBotoes(); } }
  setStatus('');
}

/* ======================= Voz: falar com a IA (microfone) ======================= */
let rec = null, chunks = [];
function reconhecido(text) {
  setStatus('');
  if (text) { $('speak').checked = true; atualizaChips(); send(text); } else setStatus('Não consegui ouvir nada. Tente de novo.');
}
function micNativo() {
  pararVoz();
  if (!gravandoNativo) {
    if (window.AndroidBridge.startRec()) { gravandoNativo = true; $('mic').classList.add('gravando'); setStatus('Gravando… toque no microfone de novo quando terminar.'); atualizaBotoes(); }
    else setStatus('Libere o microfone para o app (o Android mostra um aviso) e toque de novo.');
  } else {
    gravandoNativo = false; $('mic').classList.remove('gravando'); atualizaBotoes();
    setStatus('Entendendo o que você disse…'); window.AndroidBridge.stopRec();
  }
}
window.onNativeStt = (text, erro) => { if (erro) { setStatus('Erro na voz: ' + erro); return; } reconhecido(text); };
$('mic').onclick = async () => {
  if (NATIVO) { micNativo(); return; }
  pararVoz();
  if (rec) { rec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1}});
    rec = new MediaRecorder(stream); chunks = [];
    rec.ondataavailable = e => chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach(t => t.stop()); $('mic').classList.remove('gravando');
      const blob = new Blob(chunks, {type: rec.mimeType}); rec = null; atualizaBotoes();
      if (descartar) { descartar = false; setStatus(''); return; }
      setStatus('Entendendo o que você disse…');
      try {
        const fd = new FormData(); fd.append('audio', blob, 'voz.webm');
        const r = await fetch('/stt', {method: 'POST', body: fd});
        if (!r.ok) throw new Error('Servidor respondeu ' + r.status);
        reconhecido((await r.json()).text);
      } catch (e) { setStatus('Erro na voz: ' + e.message); }
    };
    rec.start(); $('mic').classList.add('gravando'); atualizaBotoes(); setStatus('Gravando… clique no microfone de novo quando terminar.');
  } catch (e) { setStatus('Sem acesso ao microfone: ' + e.message); }
};

/* ======================= Parar (resposta, voz, gravação, criação) ======================= */
function parar() {
  if (ctrl) ctrl.abort();
  pararVoz();
  if (rec) { descartar = true; rec.stop(); }
  if (gravandoNativo) { gravandoNativo = false; window.AndroidBridge.cancelRec(); $('mic').classList.remove('gravando'); }
  setStatus(''); atualizaBotoes();
}
$('stop').onclick = parar;
document.addEventListener('keydown', e => { if (e.key === 'Escape') { fechaMenus(); parar(); } });
// no modo ESP32, um .ino anexado (ou um firmware a modificar) pode ser compilado mesmo sem texto
function podeEnviarVazio() { return $('modo').value === 'esp32' && (anexos.some(a => a.tipo === 'ino') || (baseMod && baseMod.kind === 'bin')); }
function atualizaBotoes() {
  const ativo = busy || falando || !!rec || gravandoNativo;
  $('stop').hidden = !ativo; $('send').hidden = busy;
  $('send').disabled = !txt.value.trim() && !podeEnviarVazio();
}
setInterval(atualizaBotoes, 300);

/* ======================= Campo de digitação ======================= */
function autoAltura() { txt.style.height = 'auto'; txt.style.height = Math.min(txt.scrollHeight, 200) + 'px'; atualizaBotoes(); }
txt.addEventListener('input', autoAltura);
txt.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(txt.value); } });
$('send').onclick = () => send(txt.value);

/* ======================= Menus ======================= */
function fechaMenus() { $('plusMenu').hidden = true; $('gearMenu').hidden = true; }
$('plus').onclick = e => { e.stopPropagation(); const v = $('plusMenu').hidden; fechaMenus(); $('plusMenu').hidden = !v; };
$('gearBtn').onclick = e => { e.stopPropagation(); const v = $('gearMenu').hidden; fechaMenus(); $('gearMenu').hidden = !v; };
document.addEventListener('click', e => { if (!e.target.closest('.menu')) fechaMenus(); });
$('menuBtn').onclick = () => document.body.classList.add('lateral');
$('fundo').onclick = () => document.body.classList.remove('lateral');

const PLACEHOLDERS = {
  chat: 'Escreva sua mensagem…', app: 'Descreva o app (ex.: lista de compras)… ou anexe um app para modificar',
  appx: 'Descreva o app com várias telas (ex.: agenda com lista e detalhes)…', esp32: 'Descreva o firmware… ou anexe um .ino para modificar/compilar',
  web: 'Descreva a página ou o sistema web (ex.: calculadora de gorjeta)…', python: 'Descreva o programa Python (ex.: renomear arquivos em lote)…',
  img: 'Descreva a imagem… ou anexe uma imagem para modificar'};
const ROTULOS_MODO = {app: '📱 App Android rápido', appx: '📱 App Android avançado', esp32: '🔌 Firmware ESP32', web: '🌐 Página web', python: '🐍 Python', img: '🎨 Imagem'};
function atualizaChips() {
  const box = $('modoChips'); box.textContent = '';
  const m = $('modo').value;
  const add = t => { const s = document.createElement('span'); s.textContent = t; box.appendChild(s); };
  if (ROTULOS_MODO[m]) add(ROTULOS_MODO[m]);
  if ($('web').checked) add('🌐 Web'); if ($('speak').checked) add('🔊 Voz');
  txt.placeholder = PLACEHOLDERS[m];
}
for (const id of ['web', 'speak', 'learn']) $(id).onchange = atualizaChips;
function atualizaForca() { $('imgForcaLinha').hidden = !(anexos.some(a => a.tipo === 'imagem') || (baseMod && baseMod.kind === 'img')); }
$('imgPassos').oninput = () => $('imgPassosV').textContent = $('imgPassos').value;
$('imgForca').oninput = () => $('imgForcaV').textContent = parseFloat($('imgForca').value).toFixed(2);
let imgTimer = null;
async function checaModeloImagem() {
  try {
    const e = await (await fetch('/imagem/modelo')).json();
    $('imgModeloTxt').textContent = !e.motor ? 'Motor de imagens não instalado (rode: bash atualizar.sh)' : e.presente ? 'Modelo de imagens pronto ✓' : e.baixando ? 'Baixando o modelo… ' + Math.round(e.progresso * 100) + '%' : 'Falta o modelo de imagens (2 GB)';
    $('imgBaixar').hidden = !(e.motor && !e.presente && !e.baixando); $('imgBaixarBarra').hidden = !e.baixando;
    if (e.baixando) $('imgBaixarBarra').firstChild.style.width = Math.round(e.progresso * 100) + '%';
    if (e.baixando && !imgTimer) imgTimer = setInterval(checaModeloImagem, 2500); if (!e.baixando && imgTimer) { clearInterval(imgTimer); imgTimer = null; }
  } catch (e) {}
}
$('imgBaixar').onclick = async () => { await fetch('/imagem/modelo', {method: 'POST'}); checaModeloImagem(); };
$('modo').onchange = async () => {
  const m = $('modo').value; $('placa').hidden = m !== 'esp32'; $('imgOpts').hidden = m !== 'img'; atualizaChips(); atualizaForca();
  if (m === 'img') checaModeloImagem();
  if (m === 'esp32' && !$('placa').options.length) {
    try { for (const b of await (await fetch('/boards')).json()) { const o = document.createElement('option'); o.value = b.id; o.textContent = b.label; $('placa').appendChild(o); } } catch (e) {}
  }
  if (m !== 'chat') {
    try {
      const j = await (await fetch('/llm')).json(), cod = j.perfis.find(p => p.id === 'codigo');
      if (j.atual !== 'codigo' && cod && cod.presente) setStatus('Dica: o perfil "Código" cria apps e firmware com mais precisão (⚙ Configurações › Modelo de IA).');
    } catch (e) {}
  }
};

/* ======================= Criar app / firmware ======================= */
const fmtTam = n => n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : Math.max(1, Math.round(n / 1024)) + ' KB';
function linkArquivo(f) {
  const a = document.createElement('a'); a.className = 'dl'; a.href = f.url; a.download = f.name;
  a.innerHTML = '⬇ <span></span> <small></small>'; a.querySelector('span').textContent = f.label; a.querySelector('small').textContent = '· ' + fmtTam(f.size); return a;
}
function cartaoCriacao(m) {
  const k = m.criacao, d = document.createElement('div'); d.className = 'cartao' + (k.erro ? ' erro' : '');
  const st = document.createElement('div'); st.className = 'st'; st.textContent = k.status; d.appendChild(st);
  if (k.codigo) { const det = document.createElement('details'), sm = document.createElement('summary'), pre = document.createElement('pre'); sm.textContent = 'Ver o código que a IA escreveu'; pre.textContent = k.codigo; det.append(sm, pre); d.appendChild(det); }
  if (k.preview) { const im = document.createElement('img'); im.className = 'miniatura'; im.src = k.preview; im.alt = 'Imagem criada'; d.appendChild(im); }
  for (const f of k.files || []) d.appendChild(linkArquivo(f));
  if (k.passo) { const b = document.createElement('div'); b.className = 'barra-img'; b.innerHTML = '<i></i>'; b.firstChild.style.width = Math.round(100 * k.passo[0] / k.passo[1]) + '%'; d.appendChild(b); }
  if (k.id && !k.erro && (k.files || []).length) {
    const ac = document.createElement('div'); ac.className = 'acoes-cartao';
    const mb = document.createElement('button'); mb.textContent = '✏ Modificar'; mb.onclick = () => modificar({id: k.id, name: k.nome, kind: k.kind, modo: k.modo}); ac.appendChild(mb);
    if (k.kind === 'img' && k.descricao) { const ob = document.createElement('button'); ob.textContent = '↻ Outra versão'; ob.onclick = () => { $('modo').value = 'img'; $('modo').onchange(); txt.value = k.descricao; autoAltura(); send(txt.value); }; ac.appendChild(ob); }
    d.appendChild(ac);
  }
  if (k.nota) { const n = document.createElement('div'); n.className = 'dica'; n.textContent = k.nota; d.appendChild(n); }
  if (k.log) { const det = document.createElement('details'), sm = document.createElement('summary'), pre = document.createElement('pre'); sm.textContent = 'Ver os erros'; pre.textContent = k.log; det.append(sm, pre); d.appendChild(det); }
  return d;
}
async function criarArquivo(tipo, desc, c, usados, base) {
  usados = usados || []; const rotulo = {app: 'app', appx: 'app', esp32: 'firmware', web: 'página web', python: 'programa', img: 'imagem'}[tipo];
  const m = {role: 'assistant', kind: 'criacao', ignorar: true, criacao: {status: '⏳ Enviando o pedido…', descricao: desc}}; c.msgs.push(m);
  let atual = criaMsg(m); coluna.appendChild(atual); desceFim(true);
  const k = m.criacao, att = () => { const n = criaMsg(m); atual.replaceWith(n); atual = n; desceFim(false); };
  // o que será usado como base: um item já criado (✏) ou um arquivo anexado
  const body = {description: desc === '(compilar o sketch anexado como está)' ? '' : desc, board: $('placa').value || 'esp32'};
  if (base) body.base_entrega = base.id;
  else {
    const preferido = {esp32: ['ino'], app: ['texto'], appx: ['texto'], web: ['texto'], python: ['texto'], img: ['imagem']}[tipo] || [];
    const a = usados.find(x => preferido.includes(x.tipo)); if (a) body.base_upload = a.id;
  }
  let rota = '/app/generate';
  if (tipo === 'appx') body.avancado = true;
  else if (tipo === 'esp32') rota = '/esp32/generate';
  else if (tipo === 'web' || tipo === 'python') { rota = '/codigo/generate'; body.alvo = tipo; }
  else if (tipo === 'img') { rota = '/imagem/generate'; body.tamanho = $('imgTam').value; body.passos = parseInt($('imgPassos').value, 10); body.forca = parseFloat($('imgForca').value); body.melhorar = $('imgMelhorar').checked; }
  setStatus(`Criando: ${rotulo}… (■ cancela)`); let terminou = false;
  try {
    ctrl = new AbortController();
    const r = await fetch(rota, {method: 'POST', headers: {'Content-Type': 'application/json'}, signal: ctrl.signal, body: JSON.stringify(body)});
    if (!r.ok) throw new Error(r.status === 400 || r.status === 404 ? (await r.json()).detail : 'Servidor respondeu ' + r.status);
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const {value, done} = await reader.read(); if (done) break;
      buf += dec.decode(value, {stream: true});
      const linhas = buf.split('\n'); buf = linhas.pop();
      for (const ln of linhas) {
        if (!ln.startsWith('data:')) continue;
        const ev = JSON.parse(ln.slice(5).trim());
        if (ev.type === 'status') { k.status = '⏳ ' + ev.msg; att(); }
        else if (ev.type === 'passo') { k.passo = [ev.passo, ev.total]; k.status = `⏳ Gerando a imagem… passo ${ev.passo} de ${ev.total}`; att(); }
        else if (ev.type === 'progress') setStatus(`A IA já escreveu ${ev.tokens} pedaços de código… (■ cancela)`);
        else if (ev.type === 'code') { k.codigo = ev.text; att(); }
        else if (ev.type === 'done') { terminou = true; k.passo = null; k.status = `✅ "${ev.name}" pronto!`; k.files = ev.files; k.nota = ev.note; k.preview = ev.preview; k.id = ev.id; k.nome = ev.name; k.kind = ev.kind; k.modo = tipo === 'appx' ? 'avancado' : undefined; att(); }
        else if (ev.type === 'error') {
          terminou = true; k.passo = null; k.erro = true; k.status = '⚠ ' + ev.msg; k.log = ev.log; att();
          if (ev.precisa_modelo) { $('plusMenu').hidden = false; checaModeloImagem(); }
        }
      }
    }
    if (!terminou) { k.erro = true; k.status = `⚠ A conexão terminou antes de ficar pronto.`; att(); }
  } catch (e) {
    if (e.name === 'AbortError') k.status = `⏹ Criação cancelada por você.`;
    else { k.erro = true; k.status = '⚠ ' + mensagemAmigavel(e); }
    att();
  } finally { setStatus(''); }
}

/* ======================= Janelas ======================= */
const abreDlg = id => { fechaMenus(); document.body.classList.remove('lateral'); $(id).showModal(); };
for (const [btn, dlg] of [['memClose', 'memDlg'], ['filesClose', 'filesDlg'], ['vozClose', 'vozDlg'], ['pairClose', 'pairDlg'], ['sisClose', 'sisDlg'], ['libClose', 'libDlg'], ['llmClose', 'llmDlg']]) $(btn).onclick = () => $(dlg).close();

// --- memória
async function carregaMem() {
  const box = $('memlist'); box.textContent = 'Carregando…';
  const itens = await (await fetch('/memory')).json();
  box.textContent = itens.length ? '' : 'Ainda não há nada guardado.';
  for (const m of itens) {
    const row = document.createElement('div'); row.className = 'item-l';
    const t = document.createElement('div'); t.className = 't'; t.textContent = m.text;
    const sm = document.createElement('small'); sm.textContent = (m.source === 'web' ? 'aprendido na web' : 'você ensinou') + ' · ' + new Date(m.created * 1000).toLocaleDateString('pt-BR'); t.appendChild(sm);
    const del = document.createElement('button'); del.className = 'bt'; del.textContent = 'Apagar';
    del.onclick = async () => { await fetch('/memory/' + m.id, {method: 'DELETE'}); carregaMem(); };
    row.append(t, del); box.appendChild(row);
  }
}
$('memBtn').onclick = () => { abreDlg('memDlg'); carregaMem(); };
$('memAdd').onclick = async () => { const v = $('memNew').value.trim(); if (!v) return; await salvaMemoria(v, 'usuario'); $('memNew').value = ''; carregaMem(); };

// --- arquivos
async function carregaArquivos() {
  const box = $('fileslist'); box.textContent = 'Carregando…';
  const itens = await (await fetch('/files')).json();
  box.textContent = itens.length ? '' : 'Ainda não há nenhum arquivo criado.';
  for (const it of itens) {
    const row = document.createElement('div'); row.className = 'item-l'; row.style.flexDirection = 'column';
    const t = document.createElement('div'); t.textContent = (ICONES[it.kind === 'bin' ? 'bin' : it.kind] || '📄') + ' ' + it.name;
    const sm = document.createElement('small'); sm.textContent = it.description + ' · ' + new Date(it.created * 1000).toLocaleDateString('pt-BR');
    row.append(t, sm);
    if (it.kind === 'img') { const im = document.createElement('img'); im.className = 'miniatura'; im.src = it.files[0].url; row.appendChild(im); }
    for (const f of it.files) row.appendChild(linkArquivo(f));
    if (it.editavel) { const ac = document.createElement('div'); ac.className = 'acoes-cartao'; const mb = document.createElement('button'); mb.textContent = '✏ Modificar'; mb.onclick = () => modificar(it); ac.appendChild(mb); row.appendChild(ac); }
    box.appendChild(row);
  }
}
$('filesBtn').onclick = () => { abreDlg('filesDlg'); carregaArquivos(); };

// --- voz
let vozesCarregadas = false;
async function carregaVozes() {
  if (vozesCarregadas) return; const sel = $('vozSel');
  try {
    const lista = await (await fetch('/voices')).json(), grupos = {};
    for (const v of lista) {
      if (!grupos[v.group]) { grupos[v.group] = document.createElement('optgroup'); grupos[v.group].label = v.group; sel.appendChild(grupos[v.group]); }
      const o = document.createElement('option'); o.value = v.id; o.textContent = v.label; grupos[v.group].appendChild(o);
    }
    sel.value = vozPref.voice || (lista[0] ? lista[0].id : ''); vozesCarregadas = true;
  } catch (e) {}
}
$('voiceBtn').onclick = async () => { abreDlg('vozDlg'); await carregaVozes(); $('vel').value = vozPref.speed; $('velVal').textContent = Number(vozPref.speed).toFixed(2); };
$('vozSel').onchange = () => { vozPref.voice = $('vozSel').value; salvaVoz(); };
$('vel').oninput = () => { vozPref.speed = parseFloat($('vel').value); $('velVal').textContent = vozPref.speed.toFixed(2); salvaVoz(); };
$('vozTest').onclick = async () => {
  pararVoz(); const id = ++falaId; falando = true; atualizaBotoes(); setStatus('Gerando amostra da voz…');
  try {
    const url = await audioDe('Olá! Eu sou a sua assistente. Esta é a minha voz. Gostou?'); if (id !== falaId) return;
    const a = new Audio(url); falaAtual = a; await a.play(); await new Promise(res => { a.onended = res; a.onpause = res; });
  } catch (e) { setStatus('Voz indisponível: ' + e.message); return; }
  finally { if (id === falaId) { falando = false; atualizaBotoes(); } }
  setStatus('');
};

// --- modelo de IA (perfis)
let llmTimer = null;
async function carregaLlm() {
  try {
    const j = await (await fetch('/llm')).json(), box = $('llmLista'); box.textContent = '';
    for (const p of j.perfis) {
      const row = document.createElement('div'); row.className = 'item-l'; row.style.flexDirection = 'column';
      const topo = document.createElement('div'); topo.style.cssText = 'display:flex;gap:10px;align-items:center;width:100%';
      const t = document.createElement('div'); t.className = 't'; t.innerHTML = '<b></b> <span class="dica"></span><small></small>';
      t.querySelector('b').textContent = p.nome + (j.atual === p.id ? ' · em uso' : ''); t.querySelector('span').textContent = '(' + p.modelo + ', ' + fmtTam(p.tam) + ')'; t.querySelector('small').textContent = p.nota;
      const b = document.createElement('button'); b.className = 'bt' + (j.atual === p.id ? '' : ' pri');
      if (j.atual === p.id) { b.textContent = 'Em uso'; b.disabled = true; }
      else if (p.presente) { b.textContent = 'Usar este'; b.onclick = () => trocaModelo(p.id); }
      else if (p.baixando) { b.textContent = 'Baixando…'; b.disabled = true; }
      else { b.textContent = 'Baixar'; b.onclick = async () => { const r = await fetch('/llm/download', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id: p.id})}); if (!r.ok) $('llmMsg').textContent = (await r.json()).detail; carregaLlm(); }; }
      topo.append(t, b); row.appendChild(topo);
      if (p.baixando) { const bar = document.createElement('div'); bar.className = 'barra'; bar.innerHTML = '<i></i>'; bar.firstChild.style.width = Math.round(p.progresso * 100) + '%'; row.appendChild(bar); }
      box.appendChild(row);
    }
    atualizaEstadoLlm(j.estado);
  } catch (e) {}
}
async function trocaModelo(id) {
  $('llmMsg').textContent = 'Trocando o modelo… ele recarrega em alguns minutos.';
  const r = await fetch('/llm/select', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({id})});
  if (!r.ok) $('llmMsg').textContent = '⚠ ' + (await r.json()).detail; carregaLlm();
}
$('llmBtn').onclick = () => { abreDlg('llmDlg'); carregaLlm(); clearInterval(llmTimer); llmTimer = setInterval(() => { if ($('llmDlg').open) carregaLlm(); else clearInterval(llmTimer); }, 2500); };
function atualizaEstadoLlm(estado) {
  $('bolinha').className = 'bolinha ' + estado;
  $('estadoTxt').textContent = {ok: 'Modelo pronto', carregando: 'Modelo carregando…', desligado: 'Modelo desligado'}[estado] || '';
}
async function checaLlm() { try { atualizaEstadoLlm((await (await fetch('/llm')).json()).estado); } catch (e) { atualizaEstadoLlm('desligado'); } }

// --- sistema (telemetria)
let sisTimer = null;
function linhaSis(nome, valor, cls) { return `<tr><td>${esc(nome)}</td><td${cls ? ' style="color:var(--' + cls + ')"' : ''}>${esc(String(valor))}</td></tr>`; }
async function lerTele() {
  try {
    const t = await (await fetch('/telemetry')).json(); let h = '';
    if (t.gpu) {
      h += linhaSis('Placa de vídeo', t.gpu.nome) + linhaSis('Temperatura da GPU', t.gpu.temp + ' °C', t.gpu.temp >= 80 ? 'err' : t.gpu.temp >= 70 ? 'warn' : '') + linhaSis('Uso da GPU', t.gpu.uso + ' %') +
           linhaSis('Memória da placa', t.gpu.mem_usada + ' / ' + t.gpu.mem_total + ' MiB') + linhaSis('Ventoinha da GPU', t.gpu.ventoinha + ' %') + linhaSis('Consumo da GPU', t.gpu.watts + ' W');
    }
    h += linhaSis('Temperatura da CPU', t.cpu.temp != null ? t.cpu.temp.toFixed(0) + ' °C' : 'indisponível', t.cpu.temp >= 85 ? 'err' : t.cpu.temp >= 75 ? 'warn' : '') + linhaSis('Uso da CPU', t.cpu.uso != null ? t.cpu.uso + ' %' : 'indisponível', t.cpu.uso >= 90 ? 'warn' : '') + linhaSis('Carga da CPU (1 min)', t.cpu.carga + ' (de ' + t.cpu.nucleos + ' threads)');
    for (const [k, v] of Object.entries(t.ventoinhas)) h += linhaSis('Ventoinha ' + k, v + ' RPM');
    h += linhaSis('Memória RAM', t.ram.usada + ' / ' + t.ram.total + ' MB');
    $('sisTab').innerHTML = h;
    $('sisNota').textContent = t.controle_ventoinha === 'active' ? '✔ Controle automático da ventoinha da GPU ligado.' : 'O controle automático da ventoinha da GPU não está ligado (rode: bash atualizar.sh).';
    montaChips(t);
  } catch (e) {}
}
$('sisBtn').onclick = () => { abreDlg('sisDlg'); lerTele(); clearInterval(sisTimer); sisTimer = setInterval(() => { if ($('sisDlg').open) lerTele(); else clearInterval(sisTimer); }, 2000); };
// --- biblioteca local
let libTimer = null;
async function lerBib() {
  try {
    const b = await (await fetch('/biblioteca')).json(); let h = '';
    h += linhaSis('Pasta', b.destino || b.pasta) + linhaSis('Disco', b.total_gb != null ? b.livre_gb + ' GB livres de ' + b.total_gb + ' GB' : 'indisponível');
    for (const f of b.fontes) h += linhaSis(f.nome, f.arquivos + ' arquivos · ' + f.trechos + ' trechos');
    for (const z of b.zims) h += linhaSis('Wikipedia offline', z.nome.replace(/\.zim$/, '') + ' · ' + z.gb + ' GB');
    $('libTab').innerHTML = h;
    $('libNota').textContent = !b.disponivel ? 'A pasta da biblioteca não está acessível. Rode: bash atualizar.sh (ele escolhe o disco grande) ou conecte/monte o disco.'
      : b.indexando ? '⏳ Indexando em segundo plano… (os números sobem sozinhos)'
      : b.fontes.length ? '✔ Biblioteca pronta.' : 'Ainda vazia: o download roda em segundo plano depois do atualizar.sh (acompanhe: tail -f ~/localai/biblioteca.log).';
  } catch (e) { $('libNota').textContent = 'Não consegui ler a biblioteca.'; }
}
$('libBtn').onclick = () => { abreDlg('libDlg'); lerBib(); clearInterval(libTimer); libTimer = setInterval(() => { if ($('libDlg').open) lerBib(); else clearInterval(libTimer); }, 4000); };
$('libIdx').onclick = async () => { await fetch('/biblioteca/indexar', { method: 'POST' }); setTimeout(lerBib, 400); };
async function buscaBib() {
  const q = $('libQ').value.trim(); if (!q) return;
  $('libRes').textContent = 'Buscando…';
  try {
    const r = await (await fetch('/biblioteca/buscar?q=' + encodeURIComponent(q))).json(); let h = '';
    for (const t of r.trechos) h += `<div class="item-l"><div class="t"><small>${esc(t.caminho)}</small>${esc(t.texto)}</div></div>`;
    for (const w of r.wikipedia) h += `<div class="item-l"><div class="t"><small>Wikipedia · ${esc(w.titulo)}</small>${esc(w.texto)}</div></div>`;
    $('libRes').innerHTML = h || '<div class="dica">Nada encontrado.</div>';
  } catch (e) { $('libRes').textContent = 'Falha na busca.'; }
}
$('libBusca').onclick = buscaBib; $('libQ').onkeydown = e => { if (e.key === 'Enter') buscaBib(); };
function montaChips(t) {
  const box = $('chips'); box.textContent = '';
  const add = (txtc, cls) => { const s = document.createElement('span'); s.textContent = txtc; if (cls) s.className = cls; box.appendChild(s); };
  if (t.gpu) add(`GPU ${t.gpu.temp}°C · ${t.gpu.uso}%`, t.gpu.temp >= 80 ? 'perigo' : t.gpu.temp >= 70 ? 'quente' : '');
  if (t.cpu.temp != null) add(`CPU ${t.cpu.temp.toFixed(0)}°C`, t.cpu.temp >= 85 ? 'perigo' : t.cpu.temp >= 75 ? 'quente' : '');
  add(`RAM ${(t.ram.usada / 1024).toFixed(1)}/${(t.ram.total / 1024).toFixed(0)} GB`);
}
async function chipsLoop() { if (document.hidden || window.innerWidth <= 860) return; try { montaChips(await (await fetch('/telemetry')).json()); } catch (e) {} }

// --- conectar celular (só no próprio PC) / servidor (só no app)
if (NATIVO) { $('cfgBtn').hidden = false; $('cfgBtn').onclick = () => window.AndroidBridge.openSettings(); }
else fetch('/pair').then(r => r.ok ? r.json() : null).then(d => {
  if (!d) return;
  $('pairBtn').hidden = false;
  $('pairBtn').onclick = async () => {
    abreDlg('pairDlg');
    const box = $('pairUrls'); box.textContent = '';
    for (const u of d.urls) { const x = document.createElement('div'); x.className = 'copia'; x.textContent = u; box.appendChild(x); }
    const ts = d.tailscale || {}; $('pairEstado').textContent =
      ts.estado === 'Running' ? '✔ Tailscale conectado neste PC. Use o endereço que começa com 100. no celular.' :
      ts.estado === 'NeedsLogin' ? '⚠ O Tailscale ainda não está logado neste PC. Rode no terminal: sudo tailscale up' :
      ts.estado === 'ausente' ? '⚠ Tailscale não instalado neste PC. Rode: bash atualizar.sh' : 'Tailscale: ' + (ts.estado || 'desconhecido') + '. Rode: sudo tailscale up';
    $('pairToken').textContent = d.token;
  };
}).catch(() => {});

// --- tema
const TEMAS = ['automático', 'claro', 'escuro'];
function aplicaTema(t) {
  const r = document.documentElement; if (t === 'claro') r.dataset.tema = 'claro'; else if (t === 'escuro') r.dataset.tema = 'escuro'; else delete r.dataset.tema;
  $('temaBtn').textContent = '🌓 Tema: ' + t; guarda('iaTema', t);
}
$('temaBtn').onclick = () => aplicaTema(TEMAS[(TEMAS.indexOf(le('iaTema', 'automático')) + 1) % 3]);
aplicaTema(le('iaTema', 'automático'));

/* ======================= Início ======================= */
atualId = le('iaAtual', '');
if (!conv()) { const n = novaConv(); convs.push(n); atualId = n.id; }
desenha(); atualizaChips(); autoAltura(); checaLlm(); setInterval(checaLlm, 5000); chipsLoop(); setInterval(chipsLoop, 3000);
txt.focus();
</script>
</body>
</html>
EOF
  cat > run.sh <<'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
set -a; source .env; set +a
# 0.0.0.0 = aceita o celular (Tailscale) e a rede de casa; quem não está no PC precisa do token.
# Para aceitar só o próprio PC: LISTEN_HOST=127.0.0.1 no arquivo .env
exec .venv/bin/uvicorn main:app --host "${LISTEN_HOST:-0.0.0.0}" --port 8080
EOF
  cat > "$BASE/start_llm.sh" <<'EOF'
#!/usr/bin/env bash
# Inicia o modelo de IA. O perfil (arquivo do modelo e camadas na placa) pode ser trocado pela
# tela do Betina & IA (Configurações > Modelo de IA); a escolha fica em ~/localai/modelo.env.
# NGL = camadas na GPU: "auto" (padrão) calcula pela VRAM livre; ou um número fixo (ex.: NGL=4).
NGL="${NGL:-auto}"
MODEL="${MODEL:-$HOME/models/__MODEL__}"
[ -f "$HOME/localai/modelo.env" ] && . "$HOME/localai/modelo.env"

# NGL=auto: calcula quantas camadas cabem na placa pela VRAM LIVRE agora, deixando folga para as contas
# do modelo e para a tela (RESERVA_VRAM, em MiB). Assim a placa é aproveitada ao máximo sem estourar.
if [ "$NGL" = "auto" ]; then
  LIVRE=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -dc '0-9')
  TAM=$(( $(stat -c %s "$MODEL" 2>/dev/null || echo 0) / 1048576 ))
  case "$MODEL" in *14B*|*14b*) CAMADAS=48 ;; *3B*|*3b*) CAMADAS=36 ;; *7B*|*7b*|*1.5B*) CAMADAS=28 ;; *) CAMADAS=32 ;; esac
  if [ -n "$LIVRE" ] && [ "$LIVRE" -gt 0 ] && [ "$TAM" -gt 0 ]; then
    POR_CAMADA=$(( TAM / CAMADAS + 1 ))
    NGL=$(( (LIVRE - ${RESERVA_VRAM:-1000}) / POR_CAMADA ))
    [ "$NGL" -lt 0 ] && NGL=0
    [ "$NGL" -gt "$CAMADAS" ] && NGL=$CAMADAS
  else
    NGL=0
  fi
  echo "[start_llm] camadas na placa (automático): $NGL | VRAM livre: ${LIVRE:-?} MiB | camadas do modelo: $CAMADAS"
fi

BIN="$HOME/llama.cpp/build/bin/llama-server"
AJUDA="$("$BIN" --help 2>&1)"
tem() { grep -q -- "$1" <<<"$AJUDA"; }   # só usa uma opção se esta versão do llama.cpp a conhece

NUCLEOS=$(lscpu -p=CORE,SOCKET 2>/dev/null | grep -v '^#' | sort -u | wc -l)
[ "$NUCLEOS" -ge 1 ] 2>/dev/null || NUCLEOS=4
LOGICOS=$(nproc 2>/dev/null || echo 8)

# -t  : threads de geração = núcleos FÍSICOS (a geração é limitada pela memória; threads extras não ajudam)
# -tb : threads do processamento do prompt = todos os threads
# -c  : contexto de 8192 (apps e firmware usam bastante)
# -b/-ub menores gastam menos memória da placa (importante com só 2 GB)
ARGS=(-m "$MODEL" -ngl "$NGL" -c "${CTX:-8192}" -t "$NUCLEOS" -b 512 -ub 256 --host 127.0.0.1 --port 8081)
tem '--threads-batch' && ARGS+=(-tb "$LOGICOS")
tem '--parallel'      && ARGS+=(-np 1)              # 1 conversa por vez: usa o contexto todo e o cache do prompt
tem '--mlock'         && ARGS+=(--mlock)            # mantém o modelo na RAM (evita lentidão por paginação)
tem '--cache-reuse'   && ARGS+=(--cache-reuse 256)  # reaproveita o que já foi calculado entre perguntas
exec "$BIN" "${ARGS[@]}"
EOF
  sed -i "s/__NGL__/$NGL_PADRAO/; s/__MODEL__/$MODEL_FILE/" "$BASE/start_llm.sh"
  chmod +x run.sh "$BASE/start_llm.sh"
  python3 -m venv .venv && .venv/bin/pip install --upgrade pip &&
  .venv/bin/pip install -r requirements.txt || return 1
  if [ ! -f .env ]; then
    TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    printf 'LOCALAI_TOKEN=%s\nLLAMA_URL=http://127.0.0.1:8081\nSEARXNG_URL=\n' "$TOKEN" > .env
    chmod 600 .env
  fi
}

# ------------------------------------------------- Gradle (compila os apps da IA)
instalar_gradle() {
  local G="$HOME/gradle/gradle-8.7"
  [ -x "$G/bin/gradle" ] && return 0
  mkdir -p "$HOME/gradle"
  baixar https://services.gradle.org/distributions/gradle-8.7-bin.zip /tmp/gradle-8.7-bin.zip &&
  unzip -q -o /tmp/gradle-8.7-bin.zip -d "$HOME/gradle"
}

# --------------------------------------- arduino-cli + ESP32 (compila firmware .bin)
instalar_esp32() {
  local CLI="$HOME/bin/arduino-cli"
  mkdir -p "$HOME/bin"
  if [ ! -x "$CLI" ]; then
    baixar https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_64bit.tar.gz /tmp/arduino-cli.tgz &&
    tar xzf /tmp/arduino-cli.tgz -C "$HOME/bin" arduino-cli || return 1
  fi
  "$CLI" core list 2>/dev/null | grep -q '^esp32:esp32' && return 0
  echo "==> Núcleo ESP32 (~700 MB, só na primeira vez; demora)"
  "$CLI" config init >/dev/null 2>&1 || true
  "$CLI" config add board_manager.additional_urls https://espressif.github.io/arduino-esp32/package_esp32_index.json
  "$CLI" core update-index && "$CLI" core install esp32:esp32
}

# ---------------------------------------------------- SSD: o que é lido o tempo todo vai para a parte rápida
# Se o sistema está num HD (lento) e existe um SSD à parte, os modelos de IA, o Gradle, o Android SDK e o núcleo
# ESP32 passam a ficar no SSD (a pasta antiga vira um link, então nada mais precisa mudar).
# Nunca formata nem apaga disco: só usa partição Linux (ext4/btrfs/xfs) que já exista no SSD.
# Para pular:  SEM_SSD=1 bash atualizar.sh        Para escolher a pasta:  SSD_DESTINO=/caminho bash atualizar.sh
mover_para() {   # mover_para ORIGEM DESTINO  (ORIGEM vira um link para DESTINO)
  local o="$1" d="$2" kb livre
  [ -L "$o" ] && return 0
  if [ ! -e "$o" ]; then mkdir -p "$d" "$(dirname "$o")" && ln -s "$d" "$o"; return 0; fi
  kb=$(du -sk "$o" 2>/dev/null | cut -f1); livre=$(df -k --output=avail "$(dirname "$d")" 2>/dev/null | tail -1 | tr -d ' ')
  if [ "${kb:-0}" -ge "${livre:-0}" ]; then echo "AVISO: sem espaço no SSD para $o; deixei onde estava."; return 1; fi
  mkdir -p "$d" || return 1
  if command -v rsync >/dev/null; then rsync -a "$o"/ "$d"/ || return 1; else cp -a "$o"/. "$d"/ || return 1; fi
  mv "$o" "$o.antigo" && ln -s "$d" "$o" && rm -rf "$o.antigo"
  SSD_MOVEU=1
}

ssd_rapido() {
  local raiz_dev raiz_disk rota dest="" nome tam fs mp tipo pk dev mnt disco_ok
  [ -n "${SSD_DESTINO:-}" ] && dest="$SSD_DESTINO"
  if [ -z "$dest" ] && [ -L "$HOME/models" ] && [ -d "$(readlink -f "$HOME/models")" ]; then
    echo "SSD: os modelos já estão em $(readlink -f "$HOME/models")"; return 0
  fi
  if [ -z "$dest" ]; then
    raiz_dev=$(findmnt -no SOURCE / 2>/dev/null)
    raiz_disk=$(lsblk -no PKNAME "$raiz_dev" 2>/dev/null | head -1)
    rota=$(cat "/sys/block/$raiz_disk/queue/rotational" 2>/dev/null || echo "?")
    if [ "$rota" = 0 ]; then echo "SSD: o sistema já está num SSD; nada a mover."; return 0; fi
    echo "SSD: o sistema está em /dev/${raiz_disk:-?} ($([ "$rota" = 1 ] && echo HD || echo desconhecido)). Procurando um SSD à parte..."
    while read -r nome tam fs mp tipo pk; do
      [ "$tipo" = part ] && [ -n "$pk" ] && [ "$pk" != "$raiz_disk" ] || continue
      [ "$(cat "/sys/block/$pk/queue/rotational" 2>/dev/null)" = 0 ] || continue
      [ "${tam:-0}" -ge 30000000000 ] || continue
      case "$fs" in ext4|btrfs|xfs) ;; *) echo "SSD: /dev/$nome é $fs (não uso; deixo como está)."; continue;; esac
      dev="/dev/$nome"; mnt="$mp"
      if [ -z "$mnt" ]; then
        mnt=$(udisksctl mount -b "$dev" 2>/dev/null | sed -n 's/.* at \(.*\)\.$/\1/p' | head -1)
        [ -n "$mnt" ] && persistir_montagem "$dev" "$fs" /mnt/ssd-betina && mnt=/mnt/ssd-betina
      fi
      [ -n "$mnt" ] && [ -d "$mnt" ] || continue
      [ "$(df -k --output=avail "$mnt" | tail -1 | tr -d ' ')" -ge 20000000 ] || { echo "SSD: pouco espaço livre em $mnt."; continue; }
      dest="$mnt/betina-rapido"; break
    done < <(lsblk -rnbo NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE,PKNAME 2>/dev/null)
  fi
  if [ -z "$dest" ]; then
    echo "SSD: não achei um SSD utilizável. Tudo continua no disco do sistema."
    echo "     (Se o SSD estiver VAZIO e você quiser usá-lo: abra o app 'Discos', crie uma partição ext4 nele e rode de novo: bash atualizar.sh)"
    return 0
  fi
  mkdir -p "$dest" 2>/dev/null || { sudo mkdir -p "$dest" && sudo chown "$USER": "$dest"; } || return 1
  [ -w "$dest" ] || sudo chown "$USER": "$dest" || return 1
  echo "SSD: usando $dest para modelos, Gradle, Android SDK e núcleo ESP32."
  SSD_MOVEU=0
  for item in models .gradle gradle Android .arduino15 .cache/huggingface; do
    mover_para "$HOME/$item" "$dest/$(echo "$item" | tr '/' '_')" || true
  done
  if [ "${SSD_MOVEU:-0}" = 1 ]; then
    LLM_UNIT_MUDOU=1   # o modelo mudou de lugar: o atualizador recarrega o llama-server no fim
    echo "SSD: pronto. A IA vai carregar os modelos do SSD (bem mais rápido que do HD)."
  fi
}

# ---------------------------------------------------- Biblioteca no disco grande (documentação e exemplos para a IA)
# Escolhe sozinho o disco grande (>= 100 GB, que não seja o do sistema). Nunca formata nem apaga nada.
# Para escolher outro lugar:  BIBLIOTECA_DESTINO=/media/$USER/MEUDISCO/biblioteca bash atualizar.sh
disco_biblioteca() {
  local alvo tam livre fs src melhor="" melhor_tam=0 dev nome tipo mp mnt
  if [ -n "${BIBLIOTECA_DESTINO:-}" ]; then echo "$BIBLIOTECA_DESTINO"; return 0; fi
  if [ -L "$HOME/biblioteca" ] && [ -d "$(readlink -f "$HOME/biblioteca")" ]; then readlink -f "$HOME/biblioteca"; return 0; fi
  while read -r alvo tam livre fs src; do
    alvo=$(printf '%b' "$alvo")
    case "$alvo" in /|/boot*|/snap*|/var*|/run*|/sys*|/proc*|/dev*|/tmp*|/usr*|/home|/home/*) continue;; esac
    case "$src" in /dev/loop*|/dev/ram*|tmpfs*|overlay*) continue;; esac
    [ "${tam:-0}" -ge 100000000000 ] && [ "${livre:-0}" -ge 40000000000 ] || continue
    [ "$tam" -gt "$melhor_tam" ] && { melhor="$alvo"; melhor_tam="$tam"; }
  done < <(findmnt -brno TARGET,SIZE,AVAIL,FSTYPE,SOURCE 2>/dev/null)
  if [ -z "$melhor" ]; then   # disco grande ainda não montado: tenta montar (só lê; nunca formata)
    while read -r nome tam fs mp tipo; do
      [ "$tipo" = part ] && [ -z "$mp" ] && [ "${tam:-0}" -ge 100000000000 ] || continue
      case "$fs" in ext4|ntfs|exfat|btrfs|xfs) ;; *) continue;; esac
      dev="/dev/$nome"
      mnt=$(udisksctl mount -b "$dev" 2>/dev/null | sed -n 's/.* at \(.*\)\.$/\1/p' | head -1)
      if [ -n "$mnt" ] && [ -d "$mnt" ]; then melhor="$mnt"; DISCO_DEV="$dev"; DISCO_FS="$fs"; break; fi
    done < <(lsblk -rnbo NAME,SIZE,FSTYPE,MOUNTPOINT,TYPE 2>/dev/null)
  fi
  [ -n "$melhor" ] && echo "$melhor/biblioteca-betina"
}

# Se o disco foi montado agora, deixa montado em todo boot (entrada com "nofail": se o disco faltar, o PC liga normal).
persistir_montagem() {
  local dev="$1" fs="$2" uuid ponto="${3:-/mnt/disco-betina}" opts="defaults,nofail,x-systemd.device-timeout=5"
  uuid=$(lsblk -no UUID "$dev" 2>/dev/null | head -1); [ -n "$uuid" ] || return 1
  grep -q "$uuid" /etc/fstab 2>/dev/null && return 0
  case "$fs" in ntfs) fs=ntfs-3g; opts="$opts,uid=$(id -u),gid=$(id -g),umask=022";; exfat) opts="$opts,uid=$(id -u),gid=$(id -g),umask=022";; esac
  sudo mkdir -p "$ponto"
  udisksctl unmount -b "$dev" >/dev/null 2>&1 || true
  sudo cp /etc/fstab /etc/fstab.betina.bak
  echo "UUID=$uuid $ponto $fs $opts 0 0" | sudo tee -a /etc/fstab >/dev/null
  if sudo mount "$ponto" 2>/dev/null; then
    echo "Disco montado em $ponto e vai montar sozinho em todo boot (cópia do fstab: /etc/fstab.betina.bak)."
  else
    sudo cp /etc/fstab.betina.bak /etc/fstab   # não deu: desfaz
    udisksctl mount -b "$dev" >/dev/null 2>&1 || true
    echo "AVISO: não consegui deixar o disco montado fixo (se for do Windows, desligue a 'inicialização rápida'). Desfiz a alteração."
    return 1
  fi
}

instalar_biblioteca() {
  local dest
  DISCO_DEV=""; DISCO_FS=""
  local saida; saida=$(mktemp)
  disco_biblioteca > "$saida" || true   # (sem $(...): precisa gravar DISCO_DEV no shell atual)
  dest=$(cat "$saida"); rm -f "$saida"
  if [ -z "$dest" ]; then
    dest="$HOME/biblioteca-local"
    echo "AVISO: não achei o disco grande (>= 100 GB livre). Vou usar $dest no disco do sistema por enquanto."
    echo "       Quando conectar/montar o disco de 500 GB, rode de novo: bash atualizar.sh"
  elif [ -n "$DISCO_DEV" ]; then
    persistir_montagem "$DISCO_DEV" "$DISCO_FS" && dest="/mnt/disco-betina/biblioteca-betina"
  fi
  mkdir -p "$dest" 2>/dev/null || { sudo mkdir -p "$dest" && sudo chown "$USER": "$dest"; } || return 1
  mkdir -p "$dest"/docs "$dest"/codigo "$dest"/zim "$dest"/meus
  # ~/biblioteca vira um link para o disco grande (o servidor sempre olha ~/biblioteca)
  if [ -d "$HOME/biblioteca" ] && [ ! -L "$HOME/biblioteca" ]; then
    cp -an "$HOME/biblioteca/." "$dest/" 2>/dev/null && rm -rf "$HOME/biblioteca"
  fi
  ln -sfn "$dest" "$HOME/biblioteca"
  echo "Biblioteca em: $dest   (livre: $(df -h --output=avail "$dest" | tail -1 | tr -d ' '))"

  # leitor de Wikipedia offline (.zim)
  "$SRV/.venv/bin/pip" install -q libzim >/dev/null 2>&1 || echo "AVISO: não instalei o leitor de Wikipedia offline (libzim); a biblioteca de textos funciona do mesmo jeito."

  # bibliotecas do Arduino que a IA mais usa em firmware ESP32 (assim o código que ela escreve compila de primeira)
  if [ -x "$HOME/bin/arduino-cli" ]; then
    "$HOME/bin/arduino-cli" lib install ArduinoJson PubSubClient "Adafruit NeoPixel" "Adafruit GFX Library" "Adafruit SSD1306" \
      "DHT sensor library" "Adafruit Unified Sensor" FastLED NTPClient WiFiManager OneWire DallasTemperature "LiquidCrystal I2C" \
      "Adafruit BME280 Library" TFT_eSPI >/dev/null 2>&1 || echo "AVISO: algumas bibliotecas do Arduino não instalaram (ficam para a próxima)."
  fi

  cat > "$BASE/biblioteca_baixar.sh" <<'BIBEOF'
#!/usr/bin/env bash
# Baixa (ou continua baixando) a biblioteca no disco grande. Pode rodar de novo à vontade: o que já veio é pulado.
# Acompanhe:  tail -f ~/localai/biblioteca.log
BIB="$HOME/biblioteca"
[ -d "$BIB" ] || { echo "Não achei ~/biblioteca. Rode: bash atualizar.sh"; exit 1; }
mkdir -p "$BIB"/docs "$BIB"/codigo "$BIB"/zim "$BIB"/meus
log() { echo "[$(date +%H:%M:%S)] $*"; }
git_baixa() { [ -d "$BIB/codigo/$1/.git" ] && return 0; log "baixando $1"; git clone --depth 1 --quiet "$2" "$BIB/codigo/$1" || { rm -rf "$BIB/codigo/$1"; log "AVISO: falhou $1"; }; }

log "== Exemplos e bibliotecas para ESP32/Arduino"
git_baixa arduino-esp32     https://github.com/espressif/arduino-esp32
git_baixa ArduinoJson       https://github.com/bblanchon/ArduinoJson
git_baixa PubSubClient      https://github.com/knolleary/pubsubclient
git_baixa Adafruit_NeoPixel https://github.com/adafruit/Adafruit_NeoPixel
git_baixa Adafruit_SSD1306  https://github.com/adafruit/Adafruit_SSD1306
git_baixa Adafruit_BME280   https://github.com/adafruit/Adafruit_BME280_Library
git_baixa FastLED           https://github.com/FastLED/FastLED
git_baixa TFT_eSPI          https://github.com/Bodmer/TFT_eSPI
git_baixa WiFiManager       https://github.com/tzapu/WiFiManager
git_baixa ESPAsyncWebServer https://github.com/ESP32Async/ESPAsyncWebServer
log "== Exemplos de apps Android"
git_baixa android-architecture-samples    https://github.com/android/architecture-samples
git_baixa android-user-interface-samples  https://github.com/android/user-interface-samples
git_baixa android-platform-samples        https://github.com/android/platform-samples
log "== Documentação: Arduino, Python e Web (MDN)"
[ -d "$BIB/docs/arduino-reference/.git" ] || git clone --depth 1 --quiet https://github.com/arduino/reference-en "$BIB/docs/arduino-reference" || log "AVISO: falhou arduino-reference"
if [ ! -f "$BIB/docs/python/.ok" ]; then
  log "baixando documentação do Python"; mkdir -p "$BIB/docs/python"
  curl -L --fail -s https://docs.python.org/3/archives/python-3.12-docs-text.tar.bz2 | tar xj -C "$BIB/docs/python" --strip-components=1 && touch "$BIB/docs/python/.ok" || log "AVISO: falhou a documentação do Python"
fi
[ -d "$BIB/docs/mdn/.git" ] || { log "baixando MDN (HTML/CSS/JavaScript; ~1 GB)"; git clone --depth 1 --quiet https://github.com/mdn/content "$BIB/docs/mdn" || { rm -rf "$BIB/docs/mdn"; log "AVISO: falhou o MDN"; }; }
log "== Wikipedia em português (offline)"
if ! ls "$BIB"/zim/wikipedia_pt*.zim >/dev/null 2>&1; then
  ARQ=$(curl -s --max-time 30 https://download.kiwix.org/zim/wikipedia/ | grep -o 'wikipedia_pt_all_nopic_[0-9-]*\.zim' | sort -u | tail -1)
  if [ -n "$ARQ" ]; then
    log "baixando $ARQ (alguns GB; pode demorar e continua de onde parou se interromper)"
    curl -L --fail -C - -o "$BIB/zim/$ARQ.part" "https://download.kiwix.org/zim/wikipedia/$ARQ" && mv "$BIB/zim/$ARQ.part" "$BIB/zim/$ARQ" || log "AVISO: a Wikipedia não terminou; rode este script de novo"
  else
    log "AVISO: não achei a Wikipedia em português no servidor do Kiwix agora."
  fi
fi
log "FIM. A IA já pode consultar tudo isso (o índice é atualizado sozinho a cada poucos minutos)."
BIBEOF
  chmod +x "$BASE/biblioteca_baixar.sh"
  if [ -z "${SEM_DOWNLOAD_BIBLIOTECA:-}" ]; then
    nohup nice -n 19 ionice -c3 bash "$BASE/biblioteca_baixar.sh" >> "$BASE/biblioteca.log" 2>&1 &
    echo "Baixando a biblioteca em segundo plano (vários GB). Acompanhe: tail -f ~/localai/biblioteca.log"
  fi
}

# ------------------------------------------------ gerador de imagens (stable-diffusion.cpp)
# Modelo de imagens (SD-Turbo, ~2 GB): baixa sozinho em segundo plano, para você não precisar baixar pelo app.
baixar_modelo_imagem() {
  local d="$HOME/models/imagens" f=sd_turbo-f16-q8_0.gguf tam=2023745376 atual=0
  mkdir -p "$d"
  [ -f "$d/$f" ] && atual=$(stat -c %s "$d/$f")
  [ "$atual" -ge $((tam * 999 / 1000)) ] && return 0
  pgrep -f "sd_turbo-f16-q8_0.gguf.part" >/dev/null 2>&1 && return 0   # já está baixando
  echo "==> Modelo de imagens (~2 GB) baixando em segundo plano (continua de onde parou se interromper)."
  nohup nice -n 10 bash -c "curl -L --fail -C - -o '$d/$f.part' 'https://huggingface.co/Green-Sky/SD-Turbo-GGUF/resolve/main/$f' && mv '$d/$f.part' '$d/$f'" >> "$BASE/imagens-modelo.log" 2>&1 &
}

instalar_imagens() {
  baixar_modelo_imagem || true
  if "$SRV/.venv/bin/python" -c "import stable_diffusion_cpp" 2>/dev/null; then
    echo "Motor de imagens já instalado."; return 0
  fi
  echo "==> Motor de imagens (stable-diffusion.cpp). Compila com suporte à placa de vídeo; demora de 10 a 25 minutos."
  local PIP="$SRV/.venv/bin/pip"
  if [ -x "$CUDA_DIR/bin/nvcc" ] && PATH="$CUDA_DIR/bin:$PATH" CUDACXX="$CUDA_DIR/bin/nvcc" \
       CMAKE_ARGS="-DSD_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=52 -DCMAKE_CUDA_COMPILER=$CUDA_DIR/bin/nvcc" \
       "$PIP" install --no-cache-dir stable-diffusion-cpp-python; then
    echo "Motor de imagens instalado COM suporte à placa de vídeo."
  else
    echo "AVISO: a versão com placa de vídeo não compilou; instalando a versão só de CPU (funciona, mais lenta)."
    "$PIP" install --no-cache-dir stable-diffusion-cpp-python || return 1
  fi
}

# ---------------------------------------------------- Tailscale (acesso de qualquer lugar)
instalar_tailscale() {
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh || return 1
  sudo systemctl enable --now tailscaled >/dev/null 2>&1 || true   # liga sozinho a cada boot do PC
  if tailscale status >/dev/null 2>&1; then
    echo "Tailscale já está conectado."
  elif [ -n "${SEM_TAILSCALE_LOGIN:-}" ]; then
    echo "Pulei o login do Tailscale. Depois rode: sudo tailscale up"; return 0
  else
    if [ -n "${TS_AUTHKEY:-}" ] && sudo tailscale up --hostname=betina --authkey "$TS_AUTHKEY"; then :
    else
      echo "==> Login do Tailscale: abra o link que aparece abaixo e entre na sua conta (espero até 5 minutos)."
      timeout 300 sudo tailscale up --hostname=betina || { echo "AVISO: login não concluído. Rode depois: sudo tailscale up"; return 1; }
    fi
  fi
  sudo tailscale set --operator="$USER" >/dev/null 2>&1 || true    # permite usar o tailscale sem sudo
  sudo tailscale set --hostname=betina >/dev/null 2>&1 || true     # nome fixo: o app acha o PC em http://betina:8080
  echo "Tailscale conectado. Depois deste login ele reconecta sozinho em todo boot."
}

# ----------------------------------------------------------- 6. Android SDK
instalar_android_sdk() {
  local SDK="$HOME/Android/Sdk" JH=/usr/lib/jvm/java-17-openjdk-amd64
  if [ -x "$SDK/build-tools/34.0.0/aapt2" ]; then
    echo "Android SDK já instalado, pulando"
    grep -q '^ANDROID_HOME=' "$SRV/.env" || printf 'ANDROID_HOME=%s\nJAVA_HOME=%s\n' "$SDK" "$JH" >> "$SRV/.env"
    return 0
  fi
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
  echo "==> Modelo $MODELO ($MODEL_TAM; retoma se cair)"
  baixar "$MODEL_URL" "$MODEL"
}

# ------------------------------------------------- 8. Voz (ouvir e falar)
preparar_whisper() {
  echo "==> Modelo que entende a sua voz (large-v3-turbo, ~1,6 GB; só na primeira vez)"
  "$SRV/.venv/bin/python" - <<'PYEOF'
from faster_whisper import WhisperModel
for nome in ("large-v3-turbo", "small"):
    try:
        WhisperModel(nome, device="cpu", compute_type="int8")
        print("modelo de voz pronto:", nome)
        break
    except Exception as e:
        print("não consegui preparar", nome, "-", e)
PYEOF
}

instalar_voz() {
  mkdir -p "$HOME/models"
  echo "==> Baixando a voz feminina (Kokoro 'pf_dora', ~350 MB)"
  local K=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
  baixar "$K/kokoro-v1.0.onnx" "$HOME/models/kokoro-v1.0.onnx" &&
  baixar "$K/voices-v1.0.bin" "$HOME/models/voices-v1.0.bin" || return 1
  echo "==> Baixando a voz reserva (Piper, masculina)"
  local U=https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium
  baixar "$U/pt_BR-faber-medium.onnx" "$HOME/models/pt_BR-faber-medium.onnx" &&
  baixar "$U/pt_BR-faber-medium.onnx.json" "$HOME/models/pt_BR-faber-medium.onnx.json" || return 1
    preparar_whisper
}

instalar_atalho() {
  mkdir -p "$HOME/.local/share/applications" "$HOME/Desktop"
  cat > "$HOME/.local/share/applications/ia-local.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Betina & IA
Comment=Conversar com a IA local por texto e voz
Exec=xdg-open http://localhost:8080
Icon=utilities-terminal
Terminal=false
Categories=Utility;
DESK
  cp "$HOME/.local/share/applications/ia-local.desktop" "$HOME/Desktop/" && chmod +x "$HOME/Desktop/ia-local.desktop"
}

# --------------------------------- desempenho: troca de modelo pela tela e ventoinhas
instalar_desempenho() {
  # (a) a tela troca o modelo reiniciando SÓ o serviço do modelo (permissão restrita a esse comando)
  local SC; SC=$(command -v systemctl)
  printf '%s ALL=(root) NOPASSWD: %s restart localai-llm.service, /bin/systemctl restart localai-llm.service\n' "$USER" "$SC" |
    sudo tee /etc/sudoers.d/localai >/dev/null
  sudo chmod 440 /etc/sudoers.d/localai
  sudo visudo -cf /etc/sudoers.d/localai >/dev/null || { sudo rm -f /etc/sudoers.d/localai; echo "AVISO: permissão inválida, removida"; return 1; }

  # (b) deixa o modelo travar a memória (mlock) sem limite
  if ! grep -q '^LimitMEMLOCK=' /etc/systemd/system/localai-llm.service 2>/dev/null; then
    sudo sed -i '/^\[Service\]/a LimitMEMLOCK=infinity' /etc/systemd/system/localai-llm.service
    LLM_UNIT_MUDOU=1
  fi

  # (c) controle automático das ventoinhas (aumentam sob demanda; devolvem ao automático ao parar)
  [ -f "$SRV/fan.env" ] || cat > "$SRV/fan.env" <<'FANENV'
# Controle automático das ventoinhas (reinicie com: sudo systemctl restart localai-fan)
# Ventoinha da CPU pela placa-mãe: descubra o PWM com  bash ~/localai/diagnostico_fans.sh
# e tire o # da linha abaixo, ajustando o caminho:
#FANCTL_CPU_PWM=/sys/class/hwmon/hwmon3/pwm2
FANENV
  sudo tee /etc/systemd/system/localai-fan.service >/dev/null <<UNIT
[Unit]
Description=Local AI - controle automatico das ventoinhas
After=multi-user.target

[Service]
EnvironmentFile=-$SRV/fan.env
ExecStart=$SRV/.venv/bin/python $SRV/fanctl.py
Restart=on-failure
RestartSec=5
TimeoutStopSec=15

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now localai-fan.service
}

# ---------------------------------------------------- 9. Serviços no boot
instalar_servicos() {
  sudo tee /etc/systemd/system/localai-llm.service >/dev/null <<UNIT
[Unit]
Description=Local AI - llama-server
After=network.target

[Service]
User=$USER
Environment=NGL=$NGL_PADRAO
ExecStart=/usr/bin/env bash $BASE/start_llm.sh
LimitMEMLOCK=infinity
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
  sudo systemctl try-restart localai-llm.service localai-server.service || true  # só se já estiverem ativos
}

step "1/15 Pacotes"                  instalar_pacotes
step "Acelerar com o SSD"            ssd_rapido
step "2/15 Driver NVIDIA 580"        instalar_driver
step "3/15 CUDA 12.6"                instalar_cuda
step "4/15 Telemetria e GPU"         instalar_telemetria
step "5/15 Servidor"                 instalar_servidor
step "6/15 Android SDK"              instalar_android_sdk
step "7/15 Gradle (compila apps)"    instalar_gradle
step "8/15 ESP32 (compila firmware)" instalar_esp32
step "9/15 llama.cpp + modelo"       instalar_llama
step "10/15 Voz (ouvir e falar)"     instalar_voz
step "11/15 Gerador de imagens"      instalar_imagens
step "12/15 Tailscale (automático)"  instalar_tailscale
step "13/15 Serviços automáticos"    instalar_servicos
step "14/15 Desempenho e ventoinhas" instalar_desempenho
step "15/15 Atalho na área de trabalho" instalar_atalho
step "Biblioteca no disco grande"     instalar_biblioteca

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
echo "        3) sudo tailscale up   (abra o link e entre na sua conta; faça o mesmo no celular)"
echo "        4) espere ~1 minuto e abra o atalho 'Betina & IA' na área de trabalho;"
echo "           lá, clique em 'Conectar celular' e siga as instruções do app Betina & IA"
echo "        (Modelos antigos que não usar mais podem ser apagados de ~/models)"
echo "           (ou o navegador em http://localhost:8080)"
echo "        Se não abrir:  systemctl status localai-llm localai-server"
echo "=============================================================="
