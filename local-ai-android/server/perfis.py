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
