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
