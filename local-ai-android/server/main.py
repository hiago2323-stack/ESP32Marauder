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
