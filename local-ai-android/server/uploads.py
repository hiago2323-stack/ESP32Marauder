"""Anexos do usuário (imagens, .ino, .bin, .apk, código): guarda no disco e analisa."""
import glob
import json
import re
import shutil
import struct
import subprocess
import time
import uuid
from pathlib import Path

import config

IMAGENS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
CODIGO = {".py", ".java", ".kt", ".xml", ".gradle", ".html", ".htm", ".js", ".css", ".json", ".md", ".txt",
          ".c", ".cpp", ".h", ".hpp", ".ts", ".sh", ".ini", ".yaml", ".yml", ".toml", ".csv", ".cfg", ".properties"}
ID_RE = re.compile(r"[0-9a-f]{10}")
LIMITE_TEXTO = 14000   # caracteres de código que cabem no contexto do modelo

CHIPS = {0: "ESP32", 2: "ESP32-S2", 5: "ESP32-C3", 9: "ESP32-S3", 12: "ESP32-C2", 13: "ESP32-C6",
         16: "ESP32-H2", 18: "ESP32-P4", 23: "ESP32-C5"}
FLASH = {0: "1 MB", 1: "2 MB", 2: "4 MB", 3: "8 MB", 4: "16 MB", 5: "32 MB"}


def tipo_de(nome: str) -> str | None:
    ext = Path(nome).suffix.lower()
    if ext == ".ino":
        return "ino"
    if ext == ".bin":
        return "bin"
    if ext == ".apk":
        return "apk"
    if ext in IMAGENS:
        return "imagem"
    if ext in CODIGO:
        return "texto"
    return None


def nome_seguro(nome: str) -> str:
    nome = Path(nome).name
    nome = re.sub(r"[^\w.\- ]", "_", nome).strip(" .")[:80]
    return nome or "arquivo"


# ----------------------------------------------------------------------------- análises
def _ferramenta(nome: str) -> str | None:
    base = __import__("os").environ.get("ANDROID_HOME") or __import__("os").environ.get("ANDROID_SDK_ROOT") or ""
    achados = sorted(glob.glob(f"{base}/build-tools/*/{nome}"))
    return achados[-1] if achados else None


def _analisa_apk(p: Path) -> dict:
    out = {"pacote": None, "versao": None, "nome": None, "permissoes": [], "min_sdk": None, "alvo_sdk": None,
           "atividade": None, "assinatura": None}
    aapt = _ferramenta("aapt2")
    if not aapt:
        return {**out, "resumo": "Não consegui analisar: o Android SDK não está instalado no servidor."}
    r = subprocess.run([aapt, "dump", "badging", str(p)], capture_output=True, text=True, timeout=60)
    for ln in r.stdout.splitlines():
        if ln.startswith("package:"):
            m = re.search(r"name='([^']*)' versionCode='([^']*)' versionName='([^']*)'", ln)
            if m:
                out["pacote"], out["versao"] = m.group(1), f"{m.group(3)} ({m.group(2)})"
        elif ln.startswith("application-label:") and not out["nome"]:
            out["nome"] = ln.split(":", 1)[1].strip("'")
        elif ln.startswith("application:"):
            m = re.search(r"label='([^']*)'", ln)
            if m and m.group(1):
                out["nome"] = m.group(1)
        elif ln.startswith("uses-permission:"):
            m = re.search(r"name='([^']*)'", ln)
            if m:
                out["permissoes"].append(m.group(1).replace("android.permission.", ""))
        elif ln.startswith("sdkVersion:"):
            out["min_sdk"] = ln.split(":", 1)[1].strip("'")
        elif ln.startswith("targetSdkVersion:"):
            out["alvo_sdk"] = ln.split(":", 1)[1].strip("'")
        elif ln.startswith("launchable-activity:"):
            m = re.search(r"name='([^']*)'", ln)
            out["atividade"] = m.group(1) if m else None
    sg = _ferramenta("apksigner")
    if sg:
        s = subprocess.run([sg, "verify", "--print-certs", str(p)], capture_output=True, text=True, timeout=60)
        m = re.search(r"Signer #1 certificate DN: (.*)", s.stdout)
        out["assinatura"] = m.group(1) if m else ("não assinado ou inválido" if s.returncode else None)
    linhas = [f"Aplicativo Android: {out['nome'] or '(sem nome)'}", f"Pacote: {out['pacote']}",
              f"Versão: {out['versao']}", f"Android mínimo (API): {out['min_sdk']} | alvo: {out['alvo_sdk']}",
              f"Tela principal: {out['atividade']}", f"Assinado por: {out['assinatura']}",
              "Permissões: " + (", ".join(out["permissoes"]) if out["permissoes"] else "nenhuma")]
    out["resumo"] = "\n".join(linhas)
    return out


def _analisa_bin(p: Path) -> dict:
    dados = p.read_bytes()[: 0x9000 + 4096]
    tam = p.stat().st_size

    def cabecalho(off: int):
        if len(dados) < off + 24 or dados[off] != 0xE9:
            return None
        nseg, modo, fs = dados[off + 1], dados[off + 2], dados[off + 3]
        entrada = struct.unpack_from("<I", dados, off + 4)[0]
        chip = struct.unpack_from("<H", dados, off + 12)[0]
        segs, pos = [], off + 24
        for _ in range(min(nseg, 16)):
            if len(dados) < pos + 8:
                break
            addr, ln = struct.unpack_from("<II", dados, pos)
            segs.append((addr, ln))
            pos += 8 + ln
        return {"chip": CHIPS.get(chip, f"desconhecido ({chip})"), "segmentos": segs, "flash": FLASH.get(fs >> 4, "?"),
                "entrada": entrada}

    linhas = [f"Arquivo .bin de {tam / 1024:.0f} KB"]
    info: dict = {"tipo_bin": "desconhecido"}
    h0, h1 = cabecalho(0), cabecalho(0x1000)
    parts = []
    if len(dados) > 0x8000 + 32 and dados[0x8000:0x8002] == b"\xaa\x50":
        for i in range(0, 96):
            off = 0x8000 + i * 32
            if len(dados) < off + 32 or dados[off:off + 2] != b"\xaa\x50":
                break
            _, tp, sub, ofs, sz = struct.unpack_from("<2sBBII", dados, off)
            nome = dados[off + 12:off + 28].split(b"\0")[0].decode(errors="replace")
            parts.append((nome, tp, sub, ofs, sz))
    if h1 and parts:
        info["tipo_bin"] = "imagem completa de flash"
        linhas += [f"Tipo: imagem COMPLETA de flash (bootloader em 0x1000) para {h1['chip']}",
                   "Para gravar: endereço 0x0."]
    elif h0 and parts:
        info["tipo_bin"] = "imagem completa de flash"
        linhas += [f"Tipo: imagem COMPLETA de flash para {h0['chip']}", "Para gravar: endereço 0x0."]
    elif h0:
        info["tipo_bin"] = "aplicativo ou bootloader"
        linhas += [f"Tipo: imagem de aplicativo/bootloader para {h0['chip']}", f"Flash: {h0['flash']}",
                   f"Segmentos: {len(h0['segmentos'])} | entrada: 0x{h0['entrada']:08X}",
                   "Para gravar: normalmente 0x10000 (aplicativo) ou 0x1000/0x0 (bootloader)."]
    else:
        linhas += ["Não reconheci como imagem ESP32 (falta o byte mágico 0xE9). Pode ser outro tipo de binário."]
    if parts:
        linhas.append("Partições: " + "; ".join(f"{n} @0x{o:X} ({s // 1024} KB)" for n, _, _, o, s in parts))
    info["resumo"] = "\n".join(linhas)
    return info


def _analisa_ino(p: Path) -> dict:
    t = p.read_text(errors="replace")
    incs = sorted(set(re.findall(r'#include\s*[<"]([^>"]+)[>"]', t)))
    linhas = [f"Sketch Arduino com {len(t.splitlines())} linhas.",
              "Tem setup(): " + ("sim" if re.search(r"void\s+setup\s*\(", t) else "NÃO"),
              "Tem loop(): " + ("sim" if re.search(r"void\s+loop\s*\(", t) else "NÃO"),
              "Bibliotecas: " + (", ".join(incs) if incs else "nenhuma")]
    return {"resumo": "\n".join(linhas), "bibliotecas": incs}


def _analisa_imagem(p: Path) -> dict:
    from PIL import Image
    with Image.open(p) as im:
        return {"resumo": f"Imagem {im.format} de {im.width}×{im.height} pixels, modo {im.mode}.",
                "largura": im.width, "altura": im.height}


def _analisa_texto(p: Path) -> dict:
    t = p.read_text(errors="replace")
    return {"resumo": f"Arquivo de texto/código com {len(t.splitlines())} linhas e {len(t)} caracteres."}


ANALISES = {"apk": _analisa_apk, "bin": _analisa_bin, "ino": _analisa_ino, "imagem": _analisa_imagem,
            "texto": _analisa_texto}


# ----------------------------------------------------------------------------- armazenamento
def salva(nome: str, origem: Path) -> dict:
    """Move o arquivo recebido para a pasta de anexos e o analisa."""
    tipo = tipo_de(nome)
    if tipo is None:
        raise ValueError("Tipo de arquivo não aceito. Aceito: imagens, .ino, .bin, .apk e arquivos de código/texto.")
    id_ = uuid.uuid4().hex[:10]
    pasta = config.UPLOADS_DIR / id_
    pasta.mkdir(parents=True, exist_ok=True)
    seguro = nome_seguro(nome)
    destino = pasta / seguro
    shutil.move(str(origem), destino)
    try:
        analise = ANALISES[tipo](destino)
    except Exception as e:  # análise é um extra: nunca derruba o envio
        analise = {"resumo": f"Não consegui analisar o arquivo ({type(e).__name__})."}
    meta = {"id": id_, "name": seguro, "tipo": tipo, "size": destino.stat().st_size, "created": time.time(),
            "analise": analise, "url": f"/uploads/{id_}/{seguro}"}
    (pasta / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    return meta


def meta(id_: str) -> dict | None:
    if not ID_RE.fullmatch(id_):
        return None
    f = config.UPLOADS_DIR / id_ / "meta.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def caminho(id_: str) -> Path | None:
    m = meta(id_)
    if not m:
        return None
    p = config.UPLOADS_DIR / id_ / m["name"]
    return p if p.exists() else None


def texto(id_: str, limite: int = LIMITE_TEXTO) -> str | None:
    p = caminho(id_)
    if p is None:
        return None
    return p.read_text(errors="replace")[:limite]


def apaga(id_: str) -> None:
    if ID_RE.fullmatch(id_):
        shutil.rmtree(config.UPLOADS_DIR / id_, ignore_errors=True)


def limpa_antigos(dias: int = 14) -> None:
    limite = time.time() - dias * 86400
    for f in config.UPLOADS_DIR.glob("*/meta.json"):
        try:
            if json.loads(f.read_text())["created"] < limite:
                shutil.rmtree(f.parent, ignore_errors=True)
        except (OSError, ValueError, KeyError):
            pass


def contexto_do_anexo(id_: str) -> str:
    """Texto que descreve o anexo para o modelo de linguagem usar na conversa."""
    m = meta(id_)
    if not m:
        return ""
    cab = f"Arquivo anexado: {m['name']} ({m['tipo']}, {m['size'] // 1024} KB)\n{m['analise'].get('resumo', '')}"
    if m["tipo"] in ("ino", "texto"):
        conteudo = texto(id_)
        if conteudo:
            cab += f"\n--- conteúdo ---\n{conteudo}\n--- fim ---"
    return cab


def encontra_app_por_pacote(pacote: str) -> str | None:
    """Se o APK anexado foi criado por esta IA, devolve o id da entrega (para modificar pelo código-fonte)."""
    for f in config.APPS_DIR.glob("*/meta.json"):
        try:
            m = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        if m.get("app_id") == pacote:
            return m["id"]
    return None
