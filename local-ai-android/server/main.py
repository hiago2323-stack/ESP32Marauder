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
               "top_p": 0.9, "top_k": 40, "min_p": 0.05, "repeat_penalty": 1.05, "cache_prompt": True, "stream": True}

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
    modelo: str = "rapido"            # /imagem/generate: rapido | realista


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


_NOTA_SUMARIA = ("\n\n(Pedido curto: capte a essência, assuma padrões sensatos para o que não foi dito e entregue o "
                 "programa completo e funcionando, sem fazer perguntas.)")


async def _com_referencias(desc: str) -> str:
    """Junta ao pedido alguns exemplos/documentação da biblioteca local (se houver e se casarem com o pedido)."""
    if not desc:
        return desc
    try:
        return desc + await asyncio.to_thread(biblioteca.referencias, desc) + _NOTA_SUMARIA
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
    return _stream_job(lambda emit: imagens.gera_imagem(desc, init, req.tamanho, req.passos, req.forca, req.melhorar, emit, req.modelo))


@app.get("/imagem/modelo", dependencies=[Depends(require_token)])
async def imagem_modelo(m: str = "rapido"):
    return imagens.estado(m)


@app.post("/imagem/modelo", dependencies=[Depends(require_token)])
async def imagem_modelo_baixar(m: str = "rapido"):
    try:
        imagens.baixar(m)
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


def _limpa_audio(path: str) -> str | None:
    """Prepara a gravação para o Whisper: 16 kHz mono, tira graves de ruído (ventoinha, mesa), reduz o
    chiado e nivela o volume (fala baixa ou longe do microfone vira legível). Devolve o .wav limpo, ou
    None se o ffmpeg não existe ou falhou (aí o áudio original é usado, sem perda)."""
    if not config.STT_FILTRO or not shutil.which("ffmpeg"):
        return None
    saida = path + ".limpo.wav"
    filtro = "highpass=f=80,afftdn=nf=-30,loudnorm=I=-16:TP=-1.5:LRA=11"
    try:
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-af", filtro,
                            "-ar", "16000", "-ac", "1", saida], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not os.path.exists(saida) or os.path.getsize(saida) < 1000:
        Path(saida).unlink(missing_ok=True)
        return None
    return saida


def _stt_sync(path: str) -> str:
    global _whisper
    limpo = _limpa_audio(path)
    try:
        with _voice_lock:
            if _whisper is None:
                _whisper = _carrega_whisper()
                recursos.registra("whisper", _descarrega_whisper)
            recursos.usou("whisper")
            segments, _ = _whisper.transcribe(
                limpo or path, language="pt", beam_size=5, best_of=5,
                temperature=[0.0, 0.2, 0.4],           # se a primeira tentativa sair ruim, tenta de novo
                vad_filter=True,
                vad_parameters={"threshold": 0.45, "min_silence_duration_ms": 600, "speech_pad_ms": 300},
                initial_prompt=config.STT_DICA, condition_on_previous_text=False,
                no_speech_threshold=0.5, compression_ratio_threshold=2.2, log_prob_threshold=-1.0)
            return " ".join(s.text.strip() for s in segments).strip()
    finally:
        if limpo:
            Path(limpo).unlink(missing_ok=True)


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
