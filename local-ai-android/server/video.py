"""Vídeo curto a partir de texto, 100% local (Wan 2.1 pelo stable-diffusion.cpp).

Na CPU de 4 núcleos, um clipe de ~1 s (320x192) leva de 15 a 25 minutos. Por isso o trabalho roda em segundo
plano no servidor: se o celular desconectar, ele continua; o app volta a acompanhar em /video/seguir e o
resultado também fica na lista de arquivos criados. Só um por vez (junto com apps, firmware e imagens).
Não há filtro nosso; o modelo é o oficial, sem alterações.
"""
import asyncio
import json
import os
import random
import shutil
import signal
import sys
import time
import uuid
from pathlib import Path

import androidgen
import config
import entregas
import imagens
import recursos

RESOLUCOES = {"320x192": (320, 192), "192x320": (192, 320), "480x272": (480, 272)}
QUADROS = (9, 17, 33)          # o Wan exige 4n+1 quadros
NEGATIVO = ("static, blurry, low quality, worst quality, jpeg artifacts, deformed, extra fingers, "
            "watermark, text, subtitles, overexposed")
_download: asyncio.Task | None = None
_job: dict | None = None        # {"eventos": [...], "fim": bool, "task": Task, "descricao": str, "estado": {...}}
_cond: asyncio.Condition | None = None


# ------------------------------------------------------------------ modelos (download)
def _faltam() -> list[dict]:
    return [m for m in config.VIDEO_ARQUIVOS.values()
            if not (m["arquivo"].exists() and m["arquivo"].stat().st_size >= m["tam"] * 0.999)]


def presente() -> bool:
    return not _faltam()


def ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def estado() -> dict:
    total = sum(m["tam"] for m in config.VIDEO_ARQUIVOS.values())
    feito = 0
    for m in config.VIDEO_ARQUIVOS.values():
        f, parte = m["arquivo"], m["arquivo"].with_suffix(m["arquivo"].suffix + ".part")
        if f.exists():
            feito += min(f.stat().st_size, m["tam"])
        elif parte.exists():
            feito += parte.stat().st_size
    baixando = _download is not None and not _download.done()
    return {"presente": presente(), "baixando": baixando, "progresso": round(min(feito / total, 1.0), 3),
            "tam": total, "motor": imagens.motor_instalado(), "ffmpeg": ffmpeg() is not None}


async def _baixa() -> None:
    for m in _faltam():
        m["arquivo"].parent.mkdir(parents=True, exist_ok=True)
        parte = m["arquivo"].with_suffix(m["arquivo"].suffix + ".part")
        proc = await asyncio.create_subprocess_exec(
            "curl", "-L", "--fail", "-C", "-", "-o", str(parte), m["url"],
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        if await proc.wait() == 0 and parte.exists() and parte.stat().st_size >= m["tam"] * 0.999:
            parte.rename(m["arquivo"])
        else:
            return


def baixar() -> None:
    global _download
    if presente() or (_download is not None and not _download.done()):
        return
    pasta = next(iter(config.VIDEO_ARQUIVOS.values()))["arquivo"].parent
    pasta.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(pasta).free < 5.5e9:
        raise ValueError("Falta espaço no disco: preciso de uns 6 GB livres para o modelo de vídeo.")
    _download = asyncio.get_running_loop().create_task(_baixa())


# ------------------------------------------------------------------ geração
def _estimativa_min(largura: int, altura: int, quadros: int, passos: int) -> int:
    """Estimativa grosseira para a CPU (medida num PC de 4 núcleos): difusão + decodificação + leitura do texto."""
    f = (largura * altura * quadros) / (320 * 192 * 17)
    seg = 53 * passos * f ** 1.1 + 270 * f + 90
    return max(1, round(seg / 60))


async def _roda_worker(cfg: dict, emit) -> tuple[bool, str]:
    cmd = recursos.baixa_prioridade([sys.executable, str(Path(__file__).with_name("vidgen.py"))])
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True)
    proc.stdin.write(json.dumps(cfg).encode())
    await proc.stdin.drain()
    proc.stdin.close()
    erro, fase, fixo_dec = "", 1, 270 * (cfg["largura"] * cfg["altura"] * cfg["quadros"]) / (320 * 192 * 17)
    try:
        async for linha in proc.stdout:
            try:
                ev = json.loads(linha)
            except ValueError:
                continue
            if ev.get("tipo") == "passo":
                i, n, seg = ev["passo"], ev["total"], ev.get("seg", 0)
                if fase == 1 and i == n and n == cfg["passos"]:
                    fase = 2                      # terminou a difusão; os próximos "passos" são a decodificação
                    await emit({"type": "status", "msg": "Montando os quadros do vídeo (decodificação)…"})
                    continue
                if fase == 1:
                    resta = (n - i) * seg + fixo_dec if seg else None
                    txt = f"Gerando o vídeo… passo {i} de {n}"
                    if resta:
                        txt += f" · faltam uns {max(1, round(resta / 60))} min"
                    await emit({"type": "passo", "passo": i, "total": n, "msg": txt})
            elif ev.get("tipo") == "erro":
                erro = ev.get("msg", "")
        await proc.wait()
    except asyncio.CancelledError:  # o usuário clicou em Parar
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise
    return proc.returncode == 0, erro


def _junta_mp4(pasta: Path, saida: Path) -> bool:
    import subprocess
    exe = ffmpeg()
    if not exe:
        return False
    r = subprocess.run(recursos.baixa_prioridade([
        exe, "-y", "-framerate", str(config.VIDEO_FPS), "-i", str(pasta / "q%04d.png"), "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(saida)]), capture_output=True, timeout=300)
    return r.returncode == 0 and saida.exists()


async def gera_video(descricao: str, tamanho: str, quadros: int, passos: int, melhorar: bool, emit) -> None:
    if not imagens.motor_instalado():
        await emit({"type": "error", "msg": "O motor de imagens/vídeo não está instalado. Rode: bash atualizar.sh"})
        return
    if not presente():
        await emit({"type": "error", "msg": "Falta baixar o modelo de vídeo (uns 5 GB). Use o botão de baixar abaixo.",
                    "precisa_modelo_video": True})
        return
    if ffmpeg() is None:
        await emit({"type": "error", "msg": "Falta o conversor de vídeo (ffmpeg). Rode: bash atualizar.sh"})
        return
    if androidgen._trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outra coisa. Espere terminar ou clique em Parar."})
        return
    largura, altura = RESOLUCOES.get(tamanho, RESOLUCOES["320x192"])
    quadros = quadros if quadros in QUADROS else 17
    passos = max(4, min(int(passos), 30))
    async with androidgen._trava:
        try:
            recursos.garante_ram(9000, "gerar o vídeo")
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
            return
        prompt = descricao
        if melhorar:
            await emit({"type": "status", "msg": "A IA está traduzindo e detalhando o seu pedido…"})
            prompt = await imagens.melhora_prompt(descricao, video=True)
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"vid-{id_}"
        pasta.mkdir(parents=True, exist_ok=True)
        seed = random.randint(1, 2**31 - 1)
        # com a placa o vídeo roda em partes (limite de memória); se ela falhar, repete só pela CPU
        vram = recursos.vram_livre_mb()
        modo = recursos.modo_imagem(vram)
        if modo != "cpu" and not recursos.gpu_liberada("video"):
            modo = "cpu"
        d = config.VIDEO_ARQUIVOS
        base = {"difusao": str(d["difusao"]["arquivo"]), "texto": str(d["texto"]["arquivo"]), "vae": str(d["vae"]["arquivo"]),
                "threads": os.cpu_count() or recursos.nucleos_fisicos(), "prompt": prompt, "negativo": NEGATIVO,
                "largura": largura, "altura": altura, "quadros": quadros, "passos": passos, "seed": seed,
                "cfg": 6.0, "pasta": str(pasta / "quadros"), "orcamento": recursos.orcamento_vram_gib(vram or 0)}
        try:
            minutos = _estimativa_min(largura, altura, quadros, passos)
            await emit({"type": "status", "msg": f"Gerando o vídeo ({quadros} quadros, {largura}×{altura}). Leva uns "
                                                 f"{minutos} minutos; pode fechar o app, ele continua no PC."})
            ok, erro = await _roda_worker({**base, "modo": modo}, emit)
            if modo != "cpu":
                recursos.anota_gpu("video", ok)
            if not ok and modo != "cpu":
                await emit({"type": "status", "msg": "A placa de vídeo não deu conta; tentando só pela CPU…"})
                ok, erro = await _roda_worker({**base, "modo": "cpu"}, emit)
            if not ok:
                await emit({"type": "error", "msg": "Não consegui gerar o vídeo.", "log": erro})
                return
            await emit({"type": "status", "msg": "Juntando os quadros num arquivo .mp4…"})
            mp4 = pasta / "video.mp4"
            if not await asyncio.to_thread(_junta_mp4, pasta / "quadros", mp4):
                await emit({"type": "error", "msg": "Gerei os quadros, mas não consegui montar o .mp4."})
                return
            slug = androidgen.slugify(descricao) or "video"
            nome = (descricao[:40] or "Vídeo").strip()
            meta = entregas.salva(id_, nome, descricao, "video", [(mp4, f"{slug}.mp4", "Vídeo (.mp4)")],
                                  {"prompt": prompt, "seed": seed, "tamanho": f"{largura}x{altura}", "quadros": quadros})
            await emit({"type": "done", "id": id_, "name": nome, "kind": "video", "files": meta["files"],
                        "preview": meta["files"][0]["url"], "prompt": prompt, "note": f"Prompt usado: {prompt}"})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)


# ------------------------------------------------------------------ trabalho em segundo plano
def _condicao() -> asyncio.Condition:
    global _cond
    if _cond is None:
        _cond = asyncio.Condition()
    return _cond


def rodando() -> bool:
    return _job is not None and not _job["fim"]


def inicia(descricao: str, tamanho: str, quadros: int, passos: int, melhorar: bool) -> None:
    """Começa a gerar em segundo plano (sobrevive à desconexão do celular)."""
    global _job
    if rodando():
        raise ValueError("Já tem um vídeo sendo gerado. Espere terminar ou cancele.")
    job = {"eventos": [], "fim": False, "descricao": descricao, "inicio": time.time(), "ultimo": None}
    _job = job
    cond = _condicao()

    async def emit(ev: dict):
        async with cond:
            job["eventos"].append(ev)
            if ev.get("type") in ("passo", "status"):
                job["ultimo"] = ev
            cond.notify_all()

    async def roda():
        try:
            await gera_video(descricao, tamanho, quadros, passos, melhorar, emit)
        except asyncio.CancelledError:
            await asyncio.shield(emit({"type": "error", "msg": "Vídeo cancelado."}))
        except Exception as e:
            await emit({"type": "error", "msg": f"Erro inesperado: {type(e).__name__}: {e}"})
        finally:
            async with cond:
                job["fim"] = True
                cond.notify_all()

    job["task"] = asyncio.get_running_loop().create_task(roda())


def cancela() -> bool:
    if rodando():
        _job["task"].cancel()
        return True
    return False


def situacao() -> dict:
    if _job is None:
        return {"rodando": False}
    ult = _job.get("ultimo") or {}
    return {"rodando": rodando(), "descricao": _job["descricao"], "inicio": _job["inicio"],
            "msg": ult.get("msg", ""), "passo": ult.get("passo"), "total": ult.get("total")}


async def segue():
    """Entrega (como eventos SSE) tudo o que já aconteceu no trabalho atual/último e depois acompanha ao vivo."""
    if _job is None:
        return
    job, cond, i = _job, _condicao(), 0
    while True:
        async with cond:
            await cond.wait_for(lambda: i < len(job["eventos"]) or job["fim"])
            novos = job["eventos"][i:]
            i = len(job["eventos"])
            fim = job["fim"]
        for ev in novos:
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n".encode()
        if fim and i >= len(job["eventos"]):
            return
