"""Criar e modificar imagens (texto para imagem e imagem para imagem), 100% local.

Usa o Stable Diffusion Turbo pelo stable-diffusion.cpp. Não existe filtro de conteúdo.
A IA de texto traduz e detalha o pedido para inglês (os modelos de imagem entendem inglês bem melhor).
"""
import asyncio
import os
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
_download: dict[str, asyncio.Task] = {}


# ------------------------------------------------------------------ modelo de imagem (download)
def _m(mid: str) -> dict:
    return config.IMG_MODELOS.get(mid) or config.IMG_MODELOS["rapido"]


def presente(mid: str = "rapido") -> bool:
    m = _m(mid)
    return m["arquivo"].exists() and m["arquivo"].stat().st_size >= m["tam"] * 0.999


def estado(mid: str = "rapido") -> dict:
    m = _m(mid)
    parte = m["arquivo"].with_suffix(".gguf.part")
    t = _download.get(mid)
    baixando = t is not None and not t.done()
    prog = (parte.stat().st_size / m["tam"]) if baixando and parte.exists() else 0.0
    return {"presente": presente(mid), "baixando": baixando, "progresso": round(min(prog, 1.0), 3),
            "tam": m["tam"], "motor": motor_instalado(), "nome": m["nome"]}


def motor_instalado() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("stable_diffusion_cpp") is not None
    except Exception:
        return False


async def _baixa(mid: str) -> None:
    m = _m(mid)
    m["arquivo"].parent.mkdir(parents=True, exist_ok=True)
    parte = m["arquivo"].with_suffix(".gguf.part")
    proc = await asyncio.create_subprocess_exec(
        "curl", "-L", "--fail", "-C", "-", "-o", str(parte), m["url"],
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    rc = await proc.wait()
    if rc == 0 and parte.exists() and parte.stat().st_size >= m["tam"] * 0.999:
        parte.rename(m["arquivo"])


def baixar(mid: str = "rapido") -> None:
    m = _m(mid)
    t = _download.get(mid)
    if presente(mid) or (t is not None and not t.done()):
        return
    m["arquivo"].parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(m["arquivo"].parent).free < m["tam"] * 1.1:
        raise ValueError("Falta espaço no disco: preciso de uns 3 GB livres.")
    _download[mid] = asyncio.get_running_loop().create_task(_baixa(mid))


async def melhora_prompt(texto: str, realista: bool = False) -> str:
    """Pede à IA de texto para traduzir e detalhar o pedido. Se ela não responder, usa o texto original."""
    estilo = ("a realistic photograph: RAW photo, camera and lens (e.g. 85mm f/1.8), natural lighting, skin and "
              "material texture, depth of field, film grain, 8k uhd" if realista else
              "style, lighting, camera/composition, quality keywords")
    msgs = [{"role": "system", "content": (
        "You write prompts for a text-to-image model. Translate the user's request to English and make it a "
        f"single detailed line: subject, setting, {estilo}. "
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
                      melhorar: bool, emit, modelo: str = "rapido") -> None:
    if not motor_instalado():
        await emit({"type": "error", "msg": "O gerador de imagens não está instalado. Rode: bash atualizar.sh"})
        return
    if modelo not in config.IMG_MODELOS:
        modelo = "rapido"
    cfgm = config.IMG_MODELOS[modelo]
    if not presente(modelo):
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
            prompt = await melhora_prompt(descricao, modelo == "realista")
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"img-{id_}"
        pasta.mkdir(parents=True, exist_ok=True)
        saida = pasta / "imagem.png"
        seed = random.randint(1, 2**31 - 1)
        modo = recursos.modo_imagem()
        # threads = todos os núcleos/threads do processador
        base = {"modelo": str(cfgm["arquivo"]), "threads": os.cpu_count() or recursos.nucleos_fisicos(), "prompt": prompt,
                "largura": largura, "altura": altura, "passos": passos, "seed": seed, "saida": str(saida),
                "cfg": cfgm["cfg"], "negativo": cfgm["negativo"], "init": str(init) if init else None, "forca": forca}
        try:
            rotulo = {"gpu": "na placa de vídeo", "hibrido": "na placa de vídeo e na CPU", "cpu": "na CPU"}[modo]
            vram = recursos.vram_livre_mb()
            motivo = (f" (a IA de texto está ocupando a placa: só {vram} MB livres)" if modo == "cpu" and vram is not None else "")
            await emit({"type": "status", "msg": f"Gerando a imagem {rotulo}{motivo}…{' (leva cerca de 1 minuto)' if modo == 'cpu' else ''}"})
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
