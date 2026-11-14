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
         ".png": "image/png", ".mp4": "video/mp4", ".html": "text/html", ".py": "text/x-python", ".zip": "application/zip"}


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
