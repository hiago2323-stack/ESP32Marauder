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
