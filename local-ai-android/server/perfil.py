"""Perfil do usuário: nome e foto. A IA descreve a foto uma vez (modelo de visão) e usa essa descrição para saber
quem ele é e como é. Tudo fica só neste PC, em ~/localai/perfil, e pode ser corrigido ou apagado pela tela."""
import asyncio
import json
import time
from pathlib import Path

import config
import visao

ARQ = lambda: config.PERFIL_DIR / "perfil.json"   # noqa: E731
EXT = {".jpg", ".jpeg", ".png", ".webp"}
_tarefa: asyncio.Task | None = None
PADRAO = {"nome": "", "descricao": "", "foto": "", "estado": "vazio", "erro": ""}


def le() -> dict:
    try:
        return {**PADRAO, **json.loads(ARQ().read_text())}
    except (OSError, ValueError):
        return dict(PADRAO)


def _grava(p: dict) -> None:
    config.PERFIL_DIR.mkdir(parents=True, exist_ok=True)
    ARQ().write_text(json.dumps(p, ensure_ascii=False, indent=1))


def _muda(**kw) -> dict:
    p = {**le(), **kw}
    _grava(p)
    return p


def estado() -> dict:
    p = le()
    ocupado = _tarefa is not None and not _tarefa.done()
    if p["estado"] in ("baixando_visao", "descrevendo") and not ocupado:   # o servidor reiniciou no meio
        p["estado"], p["erro"] = "interrompido", "A análise foi interrompida. Toque em \"Analisar de novo\"."
    out = {"nome": p["nome"], "descricao": p["descricao"], "tem_foto": bool(p["foto"]) and Path(p["foto"]).exists(),
           "estado": p["estado"], "erro": p["erro"], "progresso": None}
    if p["estado"] == "baixando_visao":
        j = visao.progresso()
        out["progresso"] = j["progresso"] if j else None
    return out


def define_nome(nome: str) -> None:
    _muda(nome=nome.strip()[:60])


def define_descricao(txt: str) -> None:
    _muda(descricao=txt.strip()[:1200], estado="pronto" if txt.strip() else le()["estado"], erro="")


def caminho_foto() -> Path | None:
    f = le()["foto"]
    return Path(f) if f and Path(f).exists() else None


def salva_foto(dados: bytes, ext: str) -> None:
    ext = ext.lower()
    if ext not in EXT:
        raise ValueError("Use uma foto .jpg, .png ou .webp.")
    if len(dados) > 25 * 1024 * 1024:
        raise ValueError("A foto é grande demais (máximo 25 MB).")
    config.PERFIL_DIR.mkdir(parents=True, exist_ok=True)
    antiga = caminho_foto()
    destino = config.PERFIL_DIR / f"foto-{int(time.time())}{ext}"
    destino.write_bytes(dados)
    if antiga and antiga != destino:
        antiga.unlink(missing_ok=True)
    _muda(foto=str(destino), descricao="", estado="descrevendo", erro="")


def apaga() -> None:
    f = caminho_foto()
    if f:
        f.unlink(missing_ok=True)
    _grava(dict(PADRAO))


def contexto() -> str:
    """Texto para o prompt do sistema; vazio se o usuário não preencheu nada."""
    p = le()
    partes = []
    if p["nome"]:
        partes.append(f"O nome do usuário é {p['nome']}.")
    if p["descricao"]:
        partes.append(f"Aparência do usuário (descrita a partir da foto que ele mesmo enviou): {p['descricao']}")
    return ("Sobre o usuário: " + " ".join(partes) + " Use isso com naturalidade, sem repetir sem necessidade.") if partes else ""


async def _analisa() -> None:
    try:
        foto = caminho_foto()
        if not foto:
            _muda(estado="vazio", erro="")
            return
        if not visao.pronto():
            _muda(estado="baixando_visao", erro="")
            try:
                visao.baixar()
            except ValueError as e:
                _muda(estado="erro", erro=str(e))
                return
            while not visao.pronto():
                j = visao.progresso()
                if j is None or j.get("erro"):
                    _muda(estado="erro", erro=(j or {}).get("erro") or "O download do modelo de visão não terminou. Tente de novo.")
                    return
                await asyncio.sleep(3)
        _muda(estado="descrevendo", erro="")
        texto = await visao.descreve(foto)
        if caminho_foto() == foto:   # a foto não foi trocada enquanto analisava
            _muda(descricao=texto, estado="pronto", erro="")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _muda(estado="erro", erro=str(e)[:240])


def analisar() -> None:
    """Começa (ou recomeça) a análise da foto em segundo plano."""
    global _tarefa
    if _tarefa is not None and not _tarefa.done():
        return
    if not caminho_foto():
        raise ValueError("Envie uma foto primeiro.")
    _muda(estado="descrevendo", erro="")
    _tarefa = asyncio.get_running_loop().create_task(_analisa())
