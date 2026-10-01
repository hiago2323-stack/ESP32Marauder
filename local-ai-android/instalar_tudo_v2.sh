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

BASE="$HOME/localai"
SRV="$BASE/server"
CUDA_DIR="/usr/local/cuda-12.6"
MODELO="${MODELO:-7b}"
case "$MODELO" in
  3b) MODEL_FILE=Qwen2.5-3B-Instruct-Q4_K_M.gguf
      MODEL_URL=https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF/resolve/main/$MODEL_FILE
      NGL_PADRAO=16; MODEL_TAM="~1,9 GB" ;;
  *)  MODEL_FILE=Qwen2.5-Coder-7B-Instruct-Q4_K_M.gguf
      MODEL_URL=https://huggingface.co/bartowski/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/$MODEL_FILE
      NGL_PADRAO=4; MODEL_TAM="~4,7 GB" ;;
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
EOF
  cat > config.py <<'EOF'
"""Configuração do servidor, lida de variáveis de ambiente (arquivo .env via instalador)."""
import os
from pathlib import Path

HOME = Path.home()

# Token exigido de quem NÃO está no próprio PC (o PC local dispensa o token)
API_TOKEN = os.environ.get("LOCALAI_TOKEN", "")

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
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")

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
    "de forma clara e direta. Quando houver resultados de pesquisa na web no contexto, use-os e cite as "
    "fontes (endereços). Se não souber algo, diga que não sabe em vez de inventar.",
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
import hmac
import io
import json
import re
import shutil
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
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

import androidgen
import config
import memory

app = FastAPI(title="Local AI Server")
config.WORK_DIR.mkdir(parents=True, exist_ok=True)
config.APPS_DIR.mkdir(parents=True, exist_ok=True)


STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def require_token(request: Request, authorization: str = Header(default="")) -> None:
    if request.client and request.client.host in LOCAL_HOSTS:
        return
    if not config.API_TOKEN:
        raise HTTPException(500, "LOCALAI_TOKEN não configurado no servidor")
    expected = f"Bearer {config.API_TOKEN}"
    if not hmac.compare_digest(authorization, expected):
        raise HTTPException(401, "Token inválido")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


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
    temperature: float = 0.7
    web: bool = False


def _sse_error(msg: str) -> bytes:
    return f"data: {json.dumps({'error': msg})}\n\n".encode()


async def _build_messages(req: ChatRequest) -> tuple[list[dict], int]:
    system = f"{config.SYSTEM_PROMPT}\nData de hoje: {date.today().isoformat()}."
    msgs = [{"role": "system", "content": system}]
    last = next((m["content"] for m in reversed(req.messages) if m.get("role") == "user"), "")
    mems = await asyncio.to_thread(memory.search, last, 4)
    if mems:
        notes = "\n".join(f"- {m['text'][:600]}" for m in mems)
        msgs[0]["content"] += (
            "\n\nCoisas que você já aprendeu ou que o usuário te ensinou (podem estar desatualizadas; "
            "use quando forem relevantes):\n" + notes
        )
    n_web = 0
    if req.web:
        try:
            results = await asyncio.to_thread(_web_search_sync, last, 5)
        except Exception as e:  # sem internet, bloqueio etc.: segue sem a pesquisa
            results = []
            msgs[0]["content"] += f"\n(A pesquisa na web falhou: {type(e).__name__}.)"
        if results:
            ctx = "\n".join(f"[{i+1}] {r['title']} - {r['url']}\n{r['snippet']}" for i, r in enumerate(results))
            msgs[0]["content"] += "\n\nResultados da pesquisa na web:\n" + ctx
            n_web = len(results)
    return msgs + req.messages, n_web


@app.post("/chat", dependencies=[Depends(require_token)])
async def chat(req: ChatRequest):
    messages, n_web = await _build_messages(req)
    payload = {"messages": messages, "max_tokens": req.max_tokens,
               "temperature": req.temperature, "stream": True}

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


# ----------------------------------------------------------- apps Android pela IA
class AppRequest(BaseModel):
    description: str


@app.post("/app/generate", dependencies=[Depends(require_token)])
async def app_generate(req: AppRequest):
    desc = req.description.strip()[:1500]
    if not desc:
        raise HTTPException(400, "Descreva o app que você quer.")

    async def stream():
        fila: asyncio.Queue = asyncio.Queue()

        async def emit(ev: dict):
            await fila.put(ev)

        async def roda():
            try:
                await androidgen.gera_app(desc, emit)
            except Exception as e:  # nada deve derrubar a conexão sem avisar
                await fila.put({"type": "error", "msg": f"Erro inesperado: {type(e).__name__}: {e}"})
            finally:
                await fila.put(None)

        tarefa = asyncio.create_task(roda())
        try:
            while (ev := await fila.get()) is not None:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
        finally:
            tarefa.cancel()  # cliente desconectou (botão Parar): cancela a IA e o Gradle

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/apps", dependencies=[Depends(require_token)])
async def apps_list():
    return await asyncio.to_thread(androidgen.lista_apps)


@app.get("/apk/{app_id}", dependencies=[Depends(require_token)])
async def apk_download(app_id: str):
    if not re.fullmatch(r"[0-9a-f]{10}", app_id):
        raise HTTPException(400, "Id inválido")
    f = config.APPS_DIR / f"{app_id}.apk"
    if not f.exists():
        raise HTTPException(404, "App não encontrado")
    nome = "app"
    meta = config.APPS_DIR / f"{app_id}.json"
    if meta.exists():
        nome = androidgen.slugify(json.loads(meta.read_text()).get("name", "app")) or "app"
    return FileResponse(f, media_type="application/vnd.android.package-archive", filename=f"{nome}.apk")


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


def _stt_sync(path: str) -> str:
    global _whisper
    with _voice_lock:
        if _whisper is None:
            from faster_whisper import WhisperModel
            _whisper = WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8")
        segments, _ = _whisper.transcribe(path, language="pt", vad_filter=True)
        return " ".join(s.text.strip() for s in segments).strip()


def _get_kokoro():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(config.KOKORO_MODEL), str(config.KOKORO_VOICES))
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
    payload = {"messages": messages, "stream": True, "temperature": 0.2,
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
        gradle, "assembleDebug", "--no-daemon", "--console=plain", "-q",
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
_trava = asyncio.Lock()


async def gera_app(descricao: str, emit) -> None:
    """Executa o fluxo completo, mandando eventos por emit(dict)."""
    if _trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outro app. Espere ele terminar ou clique em Parar."})
        return
    async with _trava:
        mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Crie: {EXEMPLO_PEDIDO}"},
            {"role": "assistant", "content": EXEMPLO_RESPOSTA},
            {"role": "user", "content": f"Crie: {descricao}"},
        ]
        id_ = uuid.uuid4().hex[:10]
        pasta = config.APPS_DIR / id_
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
                    destino = config.APPS_DIR / f"{id_}.apk"
                    shutil.copy(apk, destino)
                    meta = {"id": id_, "name": nome, "description": descricao, "created": time.time(),
                            "code": codigo, "app_id": app_id}
                    (config.APPS_DIR / f"{id_}.json").write_text(json.dumps(meta, ensure_ascii=False))
                    shutil.rmtree(pasta, ignore_errors=True)
                    await emit({"type": "done", "id": id_, "name": nome, "apk": f"/apk/{id_}",
                                "size": destino.stat().st_size})
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


def lista_apps() -> list[dict]:
    apps = []
    for f in config.APPS_DIR.glob("*.json"):
        try:
            m = json.loads(f.read_text())
        except Exception:
            continue
        if (config.APPS_DIR / f"{m['id']}.apk").exists():
            apps.append({k: m[k] for k in ("id", "name", "description", "created")})
    return sorted(apps, key=lambda a: a["created"], reverse=True)
EOF
  mkdir -p static
  cat > static/index.html <<'EOF'
<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>IA Local</title>
<style>
  :root { --bg:#14161a; --panel:#1d2026; --line:#2c313a; --txt:#e8eaed; --mut:#9aa0a8; --acc:#5aa9ff; --me:#27405f; }
  @media (prefers-color-scheme: light) {
    :root { --bg:#f4f5f7; --panel:#fff; --line:#d9dce1; --txt:#1c1e22; --mut:#5b6068; --acc:#1a6fd6; --me:#d9e8fb; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--txt); font:16px/1.5 system-ui,sans-serif;
         height:100dvh; display:flex; flex-direction:column; }
  header { display:flex; gap:16px; align-items:center; padding:10px 16px; border-bottom:1px solid var(--line);
           background:var(--panel); flex-wrap:wrap; }
  header h1 { font-size:17px; margin:0; margin-right:auto; }
  label { color:var(--mut); font-size:14px; display:flex; gap:6px; align-items:center; cursor:pointer; }
  button { font:inherit; border:1px solid var(--line); background:var(--panel); color:var(--txt);
           border-radius:8px; padding:8px 14px; cursor:pointer; }
  button:hover { border-color:var(--acc); }
  button:disabled { opacity:.5; cursor:default; }
  #log { flex:1; overflow-y:auto; padding:16px; display:flex; flex-direction:column; gap:12px; }
  .msg { max-width:860px; width:fit-content; padding:10px 14px; border-radius:12px; border:1px solid var(--line);
         background:var(--panel); white-space:pre-wrap; word-wrap:break-word; }
  .msg.user { align-self:flex-end; background:var(--me); }
  .msg.err { border-color:#c0392b; color:#e57368; }
  .msg pre { background:var(--bg); padding:10px; border-radius:8px; overflow-x:auto; margin:8px 0 0; white-space:pre; }
  #bar { display:flex; gap:8px; padding:12px 16px; border-top:1px solid var(--line); background:var(--panel); }
  textarea { flex:1; resize:none; font:inherit; color:var(--txt); background:var(--bg); border:1px solid var(--line);
             border-radius:10px; padding:10px 12px; height:48px; max-height:160px; }
  #mic.rec { background:#c0392b; border-color:#c0392b; color:#fff; }
  .msg .tools { margin-top:6px; }
  .msg .tools button { font-size:12px; padding:2px 8px; color:var(--mut); }
  dialog { background:var(--panel); color:var(--txt); border:1px solid var(--line); border-radius:12px;
           width:min(720px,94vw); max-height:84vh; padding:16px; }
  dialog::backdrop { background:rgba(0,0,0,.5); }
  #memlist { max-height:50vh; overflow-y:auto; margin:10px 0; display:flex; flex-direction:column; gap:8px; }
  .mem { border:1px solid var(--line); border-radius:8px; padding:8px 10px; display:flex; gap:8px;
         align-items:flex-start; font-size:14px; white-space:pre-wrap; }
  .mem span { flex:1; word-break:break-word; }
  .mem small { color:var(--mut); display:block; }
  #stop:not(:disabled) { border-color:#c0392b; color:#e57368; font-weight:600; }
  a.btn { display:inline-block; padding:8px 14px; border:1px solid var(--acc); border-radius:8px;
          color:var(--acc); text-decoration:none; margin-top:8px; }
  .msg details { margin-top:8px; }
  .msg summary { cursor:pointer; color:var(--mut); font-size:14px; }
  .dica { color:var(--mut); font-size:14px; margin-top:6px; }
  select, input[type=range] { width:100%; font:inherit; padding:6px; border-radius:8px; color:var(--txt);
          background:var(--bg); border:1px solid var(--line); }
  #status { color:var(--mut); font-size:13px; padding:0 16px 8px; min-height:22px; background:var(--panel); }
</style>
</head>
<body>
<header>
  <h1>IA Local</h1>
  <label><input type="checkbox" id="web"> Pesquisar na web</label>
  <label title="Guarda na memória o que descobrir pesquisando"><input type="checkbox" id="learn" checked> Aprender com pesquisas</label>
  <label><input type="checkbox" id="speak"> Falar as respostas</label>
  <label title="A IA escreve, compila e entrega o APK de um app Android"><input type="checkbox" id="appmode"> 📱 Criar app Android</label>
  <button id="voiceBtn">🔊 Voz</button>
  <button id="appsBtn">📱 Meus apps</button>
  <button id="memBtn">🧠 Memória</button>
  <button id="new">Nova conversa</button>
</header>
<dialog id="memDlg">
  <strong>Memória de longo prazo</strong>
  <div style="color:var(--mut);font-size:14px">Tudo que a IA sabe sobre você e o que aprendeu. Apague o que estiver errado.</div>
  <div id="memlist"></div>
  <div style="display:flex;gap:8px">
    <input id="memNew" placeholder="Ensinar algo novo…" style="flex:1;font:inherit;padding:8px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--txt)">
    <button id="memAdd">Ensinar</button><button id="memClose">Fechar</button>
  </div>
</dialog>
<dialog id="vozDlg">
  <strong>Voz da IA</strong>
  <div class="dica">Escolha uma voz e clique em Testar. As vozes de outros países leem o português com sotaque.</div>
  <label style="display:block;margin:12px 0 4px">Voz</label>
  <select id="vozSel"></select>
  <label style="display:block;margin:12px 0 4px">Velocidade: <span id="velVal">1.00</span>×</label>
  <input type="range" id="vel" min="0.7" max="1.4" step="0.05" value="1">
  <div style="display:flex;gap:8px;margin-top:14px"><button id="vozTest">▶ Testar</button><button id="vozClose">Fechar</button></div>
</dialog>
<dialog id="appsDlg">
  <strong>Apps que a IA criou</strong>
  <div class="dica">Ficam guardados no PC em ~/localai/apps. Passe o arquivo .apk para o celular (cabo USB, Bluetooth ou nuvem) e abra-o para instalar.</div>
  <div id="appslist" style="margin:10px 0;display:flex;flex-direction:column;gap:8px"></div>
  <button id="appsClose">Fechar</button>
</dialog>
<div id="log"></div>
<div id="status"></div>
<div id="bar">
  <button id="mic" title="Clique para gravar, clique de novo para enviar">🎤 Falar</button>
  <textarea id="txt" placeholder="Escreva aqui (Enter envia, Shift+Enter quebra linha)"></textarea>
  <button id="stop" disabled title="Para a resposta, a voz ou a gravação (atalho: Esc)">⏹ Parar</button>
  <button id="send">Enviar</button>
</div>
<script>
const $ = id => document.getElementById(id);
const log = $('log'), txt = $('txt'), statusEl = $('status');
let history = [];
let busy = false, ctrl = null, falando = false, descartar = false;
const setStatus = t => statusEl.textContent = t || '';

function render(el, text) {
  // Mostra blocos ``` como <pre>; o resto como texto puro (sem HTML injetado)
  el.textContent = '';
  text.split(/```/).forEach((part, i) => {
    if (i % 2) {
      const pre = document.createElement('pre');
      pre.textContent = part.replace(/^[\w+-]*\n/, '');
      el.appendChild(pre);
    } else if (part) {
      el.appendChild(document.createTextNode(part));
    }
  });
}
function addMsg(role, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  render(d, text);
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
  return d;
}

async function saveMemory(text, source) {
  const r = await fetch('/memory', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                    body: JSON.stringify({text, source})});
  if (!r.ok) throw new Error('Servidor respondeu ' + r.status);
  return r.json();
}
function addRememberButton(el, question, answer) {
  const t = document.createElement('div'); t.className = 'tools';
  const b = document.createElement('button'); b.textContent = '📌 Lembrar disso';
  b.onclick = async () => {
    try { await saveMemory(`Pergunta: ${question}\nResposta: ${answer}`, 'usuario'); b.textContent = '✔ Guardado'; b.disabled = true; }
    catch (e) { b.textContent = 'Erro ao guardar'; }
  };
  t.appendChild(b); el.appendChild(t);
}
// "lembre que ..." / "guarde que ..." / "aprenda que ..." ensina direto, sem precisar do botão
const TEACH = /^\s*(lembre-se|lembre|guarde|aprenda|anote)(\s+disso|\s+que)?[:,]?\s+(.{4,})/is;

async function send(text) {
  text = text.trim();
  if (!text || busy) return;
  pararVoz();
  busy = true; $('send').disabled = true;
  txt.value = '';
  addMsg('user', text);
  if ($('appmode').checked) {
    try { await criarApp(text); }
    finally { busy = false; ctrl = null; $('send').disabled = false; txt.focus(); }
    return;
  }
  history.push({role: 'user', content: text});
  const teach = text.match(TEACH);
  if (teach) { try { await saveMemory(teach[3].trim(), 'usuario'); } catch (e) {} }
  const wantWeb = $('web').checked;
  let webResults = 0;
  const out = addMsg('assistant', '…');
  setStatus($('web').checked ? 'Pesquisando na web e pensando…' : 'Pensando…');
  let answer = '';
  try {
    ctrl = new AbortController();
    const r = await fetch('/chat', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, signal: ctrl.signal,
      body: JSON.stringify({messages: history.slice(-12), web: $('web').checked})
    });
    if (!r.ok) throw new Error('Servidor respondeu ' + r.status);
    webResults = parseInt(r.headers.get('X-Web-Results') || '0', 10);
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const lines = buf.split('\n'); buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const data = line.slice(5).trim();
        if (!data || data === '[DONE]') continue;
        const j = JSON.parse(data);
        if (j.error) throw new Error(j.error);
        answer += (j.choices?.[0]?.delta?.content) || '';
        render(out, answer || '…');
        log.scrollTop = log.scrollHeight;
      }
    }
    history.push({role: 'assistant', content: answer});
    setStatus('');
    if (answer) addRememberButton(out, text, answer);
    if (wantWeb && webResults === 0 && answer) {
      setStatus('Não consegui pesquisar na web agora (sem internet?). Respondi só com o que eu sei.');
    }
    if (webResults > 0 && $('learn').checked && answer && !teach) {
      try { await saveMemory(`Pergunta: ${text}\nResposta (pesquisa na web em ${new Date().toLocaleDateString('pt-BR')}): ${answer}`, 'web'); } catch (e) {}
    }
    if ($('speak').checked && answer) speak(answer);  // sem await: já dá para escrever outra coisa
  } catch (e) {
    if (e.name === 'AbortError') {  // o usuário clicou em Parar
      render(out, (answer ? answer + '\n\n' : '') + '⏹ (interrompido por você)');
      if (answer) history.push({role: 'assistant', content: answer}); else history.pop();
      setStatus('');
      return;
    }
    const caiu = e instanceof TypeError || /network|fetch/i.test(e.message);
    const msg = caiu
      ? 'A conexão com o servidor caiu no meio da resposta. O modelo pode ter reiniciado (falta de memória da placa?). Espere alguns minutos e tente de novo.'
      : e.message;
    out.classList.add('err');
    render(out, (answer ? answer + '\n\n' : '') + '⚠ ' + msg);
    history.pop();
    setStatus('');
  } finally {
    busy = false; ctrl = null; $('send').disabled = false; txt.focus();
  }
}

// Divide o texto em pedaços de frases para a voz começar logo, sem esperar gerar tudo
function pedacos(text, max = 220) {
  const frases = text.split(/(?<=[.!?…])\s+|\n+/).map(f => f.trim()).filter(Boolean);
  const out = []; let cur = '';
  for (const f of frases) {
    if (cur && (cur + ' ' + f).length > max) { out.push(cur); cur = f; }
    else cur = (cur + ' ' + f).trim();
  }
  if (cur) out.push(cur);
  return out;
}
let vozPref = {voice: '', speed: 1.0};
try { vozPref = Object.assign(vozPref, JSON.parse(localStorage.getItem('vozPref') || '{}')); } catch (e) {}
const salvaVoz = () => { try { localStorage.setItem('vozPref', JSON.stringify(vozPref)); } catch (e) {} };

async function audioDe(texto) {
  const r = await fetch('/tts', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                 body: JSON.stringify({text: texto, voice: vozPref.voice || null, speed: vozPref.speed})});
  if (!r.ok) throw new Error((await r.json()).detail || r.status);
  return URL.createObjectURL(await r.blob());
}
let falaId = 0, falaAtual = null;
function pararVoz() { falaId++; falando = false; if (falaAtual) falaAtual.pause(); }

async function speak(text) {
  // Não lê blocos de código nem símbolos de formatação
  const clean = text.replace(/```[\s\S]*?```/g, ' (código omitido) ')
                    .replace(/https?:\/\/\S+/g, ' link ').replace(/[*_#`>]/g, '');
  const partes = pedacos(clean).slice(0, 40);
  if (!partes.length) return;
  const id = ++falaId;
  falando = true;
  setStatus('Gerando voz…');
  try {
    let proximo = audioDe(partes[0]);
    for (let i = 0; i < partes.length; i++) {
      const url = await proximo;
      if (id !== falaId) return;
      if (i + 1 < partes.length) proximo = audioDe(partes[i + 1]);  // prepara a próxima enquanto esta toca
      const a = new Audio(url); falaAtual = a;
      setStatus('Falando… (clique em ⏹ Parar ou aperte Esc para interromper)');
      await a.play();
      await new Promise(res => { a.onended = res; a.onpause = res; });
      URL.revokeObjectURL(url);
      if (id !== falaId) return;
    }
  } catch (e) { setStatus('Voz indisponível: ' + e.message); return; }
  finally { if (id === falaId) falando = false; }
  setStatus('');
}

let rec = null, chunks = [];
$('mic').onclick = async () => {
  pararVoz();
  if (rec) { rec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    rec = new MediaRecorder(stream); chunks = [];
    rec.ondataavailable = e => chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      $('mic').classList.remove('rec'); $('mic').textContent = '🎤 Falar';
      const blob = new Blob(chunks, {type: rec.mimeType}); rec = null;
      if (descartar) { descartar = false; setStatus(''); return; }  // clicou em Parar: joga fora a gravação
      setStatus('Entendendo o que você disse…');
      try {
        const fd = new FormData(); fd.append('audio', blob, 'voz.webm');
        const r = await fetch('/stt', {method: 'POST', body: fd});
        if (!r.ok) throw new Error('Servidor respondeu ' + r.status);
        const {text} = await r.json();
        setStatus('');
        if (text) { $('speak').checked = true; await send(text); }
        else setStatus('Não consegui ouvir nada. Tente de novo.');
      } catch (e) { setStatus('Erro na voz: ' + e.message); }
    };
    rec.start();
    $('mic').classList.add('rec'); $('mic').textContent = '⏹ Enviar';
    setStatus('Gravando… clique em "Enviar" quando terminar de falar.');
  } catch (e) { setStatus('Sem acesso ao microfone: ' + e.message); }
};

async function loadMem() {
  const box = $('memlist'); box.textContent = 'Carregando…';
  const r = await fetch('/memory'); const items = await r.json();
  box.textContent = items.length ? '' : 'Ainda não há nada guardado.';
  for (const m of items) {
    const row = document.createElement('div'); row.className = 'mem';
    const sp = document.createElement('span'); sp.textContent = m.text;
    const sm = document.createElement('small');
    sm.textContent = (m.source === 'web' ? 'aprendido na web' : 'você ensinou') + ' · ' + new Date(m.created * 1000).toLocaleDateString('pt-BR');
    sp.appendChild(sm);
    const del = document.createElement('button'); del.textContent = 'Apagar';
    del.onclick = async () => { await fetch('/memory/' + m.id, {method: 'DELETE'}); loadMem(); };
    row.append(sp, del); box.appendChild(row);
  }
}
$('memBtn').onclick = () => { $('memDlg').showModal(); loadMem(); };
$('memClose').onclick = () => $('memDlg').close();
$('memAdd').onclick = async () => {
  const v = $('memNew').value.trim(); if (!v) return;
  await saveMemory(v, 'usuario'); $('memNew').value = ''; loadMem();
};
// ---------------- botão Parar: interrompe resposta, criação de app, voz e gravação
function parar() {
  if (ctrl) ctrl.abort();
  pararVoz();
  if (rec) { descartar = true; rec.stop(); }
  setStatus('');
}
$('stop').onclick = parar;
document.addEventListener('keydown', e => { if (e.key === 'Escape') parar(); });
setInterval(() => { $('stop').disabled = !(busy || falando || rec); }, 150);

// ---------------- escolha da voz
let vozesCarregadas = false;
async function carregaVozes() {
  if (vozesCarregadas) return;
  const sel = $('vozSel');
  try {
    const lista = await (await fetch('/voices')).json();
    const grupos = {};
    for (const v of lista) {
      if (!grupos[v.group]) { grupos[v.group] = document.createElement('optgroup'); grupos[v.group].label = v.group; sel.appendChild(grupos[v.group]); }
      const o = document.createElement('option'); o.value = v.id; o.textContent = v.label; grupos[v.group].appendChild(o);
    }
    sel.value = vozPref.voice || (lista[0] ? lista[0].id : '');
    vozesCarregadas = true;
  } catch (e) { sel.textContent = ''; }
}
$('voiceBtn').onclick = async () => {
  $('vozDlg').showModal(); await carregaVozes();
  $('vel').value = vozPref.speed; $('velVal').textContent = Number(vozPref.speed).toFixed(2);
};
$('vozSel').onchange = () => { vozPref.voice = $('vozSel').value; salvaVoz(); };
$('vel').oninput = () => { vozPref.speed = parseFloat($('vel').value); $('velVal').textContent = vozPref.speed.toFixed(2); salvaVoz(); };
$('vozTest').onclick = async () => {
  pararVoz();
  const id = ++falaId; falando = true; setStatus('Gerando amostra da voz…');
  try {
    const url = await audioDe('Olá! Eu sou a sua assistente. Esta é a minha voz. Gostou?');
    if (id !== falaId) return;
    const a = new Audio(url); falaAtual = a; await a.play();
    await new Promise(res => { a.onended = res; a.onpause = res; });
  } catch (e) { setStatus('Voz indisponível: ' + e.message); return; }
  finally { if (id === falaId) falando = false; }
  setStatus('');
};
$('vozClose').onclick = () => $('vozDlg').close();

// ---------------- criar app Android
async function criarApp(desc) {
  const card = addMsg('assistant', '');
  const st = document.createElement('div'); const extra = document.createElement('div');
  card.append(st, extra);
  const rola = () => { log.scrollTop = log.scrollHeight; };
  st.textContent = '⏳ Enviando o pedido…';
  setStatus('Criando o app… (clique em ⏹ Parar para cancelar)');
  let terminou = false;
  try {
    ctrl = new AbortController();
    const r = await fetch('/app/generate', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                            body: JSON.stringify({description: desc}), signal: ctrl.signal});
    if (!r.ok) throw new Error('Servidor respondeu ' + r.status);
    const reader = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += dec.decode(value, {stream: true});
      const linhas = buf.split('\n'); buf = linhas.pop();
      for (const linha of linhas) {
        if (!linha.startsWith('data:')) continue;
        const ev = JSON.parse(linha.slice(5).trim());
        if (ev.type === 'status') st.textContent = '⏳ ' + ev.msg;
        else if (ev.type === 'progress') setStatus(`A IA já escreveu ${ev.tokens} pedaços de código… (⏹ Parar cancela)`);
        else if (ev.type === 'code') {
          extra.textContent = '';
          const d = document.createElement('details'), sm = document.createElement('summary'), pre = document.createElement('pre');
          sm.textContent = 'Ver o código que a IA escreveu'; pre.textContent = ev.text; d.append(sm, pre); extra.appendChild(d);
        } else if (ev.type === 'done') {
          terminou = true;
          st.textContent = `✅ App "${ev.name}" pronto! (${Math.max(1, Math.round(ev.size / 1024))} KB)`;
          const a = document.createElement('a'); a.className = 'btn'; a.href = ev.apk; a.download = ''; a.textContent = '⬇ Baixar o APK';
          const dica = document.createElement('div'); dica.className = 'dica';
          dica.textContent = 'Passe o arquivo para o celular (cabo USB, Bluetooth ou nuvem) e abra-o para instalar. Se o Android pedir, permita instalar de fontes desconhecidas.';
          extra.append(a, dica);
        } else if (ev.type === 'error') {
          terminou = true; card.classList.add('err'); st.textContent = '⚠ ' + ev.msg;
          if (ev.log) { const d = document.createElement('details'), sm = document.createElement('summary'), pre = document.createElement('pre');
            sm.textContent = 'Ver os erros'; pre.textContent = ev.log; d.append(sm, pre); extra.appendChild(d); }
        }
        rola();
      }
    }
    if (!terminou) { card.classList.add('err'); st.textContent = '⚠ A conexão terminou antes do app ficar pronto.'; }
  } catch (e) {
    if (e.name === 'AbortError') st.textContent = '⏹ Criação do app cancelada por você.';
    else { card.classList.add('err'); st.textContent = '⚠ ' + (e instanceof TypeError ? 'A conexão com o servidor caiu. O modelo pode ter reiniciado; espere e tente de novo.' : e.message); }
  } finally { setStatus(''); }
}
$('appmode').onchange = () => {
  txt.placeholder = $('appmode').checked ? 'Descreva o app que você quer criar (ex.: um app de lista de compras)…'
                                          : 'Escreva aqui (Enter envia, Shift+Enter quebra linha)';
};
async function listaApps() {
  const box = $('appslist'); box.textContent = 'Carregando…';
  const apps = await (await fetch('/apps')).json();
  box.textContent = apps.length ? '' : 'Ainda não há nenhum app criado.';
  for (const a of apps) {
    const row = document.createElement('div'); row.className = 'mem';
    const sp = document.createElement('span'); sp.textContent = a.name;
    const sm = document.createElement('small');
    sm.textContent = a.description + ' · ' + new Date(a.created * 1000).toLocaleDateString('pt-BR');
    sp.appendChild(sm);
    const dl = document.createElement('a'); dl.className = 'btn'; dl.style.marginTop = '0'; dl.href = '/apk/' + a.id; dl.download = ''; dl.textContent = '⬇ APK';
    row.append(sp, dl); box.appendChild(row);
  }
}
$('appsBtn').onclick = () => { $('appsDlg').showModal(); listaApps(); };
$('appsClose').onclick = () => $('appsDlg').close();

$('send').onclick = () => send(txt.value);
txt.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(txt.value); } });
$('new').onclick = () => { parar(); history = []; log.textContent = ''; setStatus(''); };
txt.focus();
</script>
</body>
</html>
EOF
  cat > run.sh <<'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
set -a; source .env; set +a
exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080
EOF
  cat > "$BASE/start_llm.sh" <<'EOF'
#!/usr/bin/env bash
# NGL = camadas na GPU. Com 2 GB de VRAM ajuste de 2 em 2 olhando o nvidia-smi:
# perto de 1800 MiB é o limite (7B: comece em 4; 3B: em 16).
# Para trocar de modelo, mude MODEL (qualquer arquivo .gguf em ~/models).
NGL="${NGL:-__NGL__}"
MODEL="${MODEL:-$HOME/models/__MODEL__}"
# -b/-ub menores gastam menos memória da placa (importante com só 2 GB)
exec "$HOME/llama.cpp/build/bin/llama-server" -m "$MODEL" -ngl "$NGL" -c 4096 -t 6 \
  -b 512 -ub 256 --host 127.0.0.1 --port 8081
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
  echo "==> Baixando o modelo que entende sua voz (~460 MB)"
  "$SRV/.venv/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
}

instalar_atalho() {
  mkdir -p "$HOME/.local/share/applications" "$HOME/Desktop"
  cat > "$HOME/.local/share/applications/ia-local.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=IA Local
Comment=Conversar com a IA local por texto e voz
Exec=xdg-open http://localhost:8080
Icon=utilities-terminal
Terminal=false
Categories=Utility;
DESK
  cp "$HOME/.local/share/applications/ia-local.desktop" "$HOME/Desktop/" && chmod +x "$HOME/Desktop/ia-local.desktop"
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

step "1/11 Pacotes"                  instalar_pacotes
step "2/11 Driver NVIDIA 580"        instalar_driver
step "3/11 CUDA 12.6"                instalar_cuda
step "4/11 Telemetria e GPU"         instalar_telemetria
step "5/11 Servidor"                 instalar_servidor
step "6/11 Android SDK"              instalar_android_sdk
step "7/11 Gradle (compila apps)"    instalar_gradle
step "8/11 llama.cpp + modelo"       instalar_llama
step "9/11 Voz (ouvir e falar)"      instalar_voz
step "10/11 Serviços automáticos"    instalar_servicos
step "11/11 Atalho na área de trabalho" instalar_atalho

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
echo "        3) espere ~1 minuto e abra o atalho 'IA Local' na área de trabalho"
echo "        (Modelos antigos que não usar mais podem ser apagados de ~/models)"
echo "           (ou o navegador em http://localhost:8080)"
echo "        Se não abrir:  systemctl status localai-llm localai-server"
echo "=============================================================="
