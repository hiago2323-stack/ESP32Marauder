"""Gerenciador de IAs: procura pelo NOME no Hugging Face, baixa e passa a usar. Sem lista fechada.

A busca é neutra: traz o que o Hugging Face tem com aquele nome (não escolhemos, não recomendamos e não
bloqueamos nenhum modelo). Os avisos sobre o tamanho para o seu PC são só informação; dá para baixar e usar
mesmo assim. O que cada modelo aceita ou recusa depende do próprio modelo.

  texto  : modelos de conversa (.gguf) vão para ~/models; "usar" grava ~/localai/modelo.env e reinicia o llama-server
  imagem : modelos Stable Diffusion completos (.gguf num arquivo só) vão para ~/models/imagens e aparecem na lista
           de modelos de imagem
Os modelos adicionados ficam registrados em ~/localai/modelos_extra.json.
"""
import asyncio
import hashlib
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

import httpx

import config

HF = "https://huggingface.co"
REPO_RE = re.compile(r"^[\w.\-]+/[\w.\-]+$")
ARQ_RE = re.compile(r"^[\w.\-+ ()\[\]]+(/[\w.\-+ ()\[\]]+)*\.gguf$")
PARTE_RE = re.compile(r"^(.*)-(\d{5})-of-(\d{5})\.gguf$")
QUANT_RE = re.compile(r"(?i)(IQ\d_\w+|Q\d(?:_K)?(?:_[SML01])?|BF16|F16|F32)")
PREFERENCIA = ["Q4_K_M", "Q4_K_S", "Q4_0", "Q5_K_M", "Q5_K_S", "Q4_1", "Q3_K_M", "Q6_K", "IQ4_XS", "Q8_0", "Q3_K_S", "Q2_K"]
SEM_TEXTO = {"text-to-image", "image-to-image", "text-to-video", "image-to-video", "text-to-speech",
             "automatic-speech-recognition", "feature-extraction"}

REGISTRO = config.MODELO_ENV.parent / "modelos_extra.json"
_baixando: dict[str, dict] = {}   # id -> {"task", "partes", "tam", "erro"}


# ------------------------------------------------------------------ registro dos modelos adicionados
def lista() -> list[dict]:
    try:
        return json.loads(REGISTRO.read_text())
    except (OSError, ValueError):
        return []


def _salva(itens: list[dict]) -> None:
    REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    REGISTRO.write_text(json.dumps(itens, ensure_ascii=False, indent=1))


def _pasta(tipo: str) -> Path:
    if tipo == "visao":
        return config.MODELS_DIR / "visao"
    return config.MODELS_DIR / "imagens" if tipo == "imagem" else config.MODELS_DIR


def _id(repo: str, arquivo: str) -> str:
    return hashlib.sha1(f"{repo}/{arquivo}".encode()).hexdigest()[:10]


def extras_imagem() -> dict:
    """Modelos de imagem adicionados, no mesmo formato de config.IMG_MODELOS (usado pelo gerador de imagens)."""
    out = {}
    for m in lista():
        if m.get("tipo") != "imagem":
            continue
        rapido = bool(re.search(r"(?i)turbo|lcm|lightning|hyper|distill", m["nome"] + m["arquivo"]))
        out["x" + m["id"]] = {"nome": m["nome"], "arquivo": Path(m["caminho"]), "tam": m["tam"],
                              "cfg": 1.5 if rapido else 7.0, "passos": 4 if rapido else 20,
                              "max_passos": 8 if rapido else 30, "url": "",
                              "negativo": "" if rapido else "(worst quality, low quality:1.4), blurry, deformed, watermark, text"}
    return out


# ------------------------------------------------------------------ hardware (só para avisos)
def _ram_gb() -> float:
    try:
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemTotal:"):
                return int(ln.split()[1]) / 1048576
    except OSError:
        pass
    return 16.0


def aviso_tamanho(tam: int, tipo: str = "texto") -> str:
    gb = tam / 1e9
    ram = _ram_gb()
    if tipo == "imagem":
        return "" if gb <= 3 else f"Grande ({gb:.1f} GB): vai demorar bastante e usar muita memória."
    if gb <= ram * 0.40:
        return ""
    if gb <= ram * 0.62:
        return f"Pesado ({gb:.1f} GB): cabe, mas fica lento."
    return f"Muito grande ({gb:.1f} GB) para {ram:.0f} GB de RAM: pode travar o PC. Dá para baixar mesmo assim."


# ------------------------------------------------------------------ busca e arquivos
def _repo_de(q: str) -> str | None:
    """Aceita 'dono/repositório', 'dono/repositório:Q4_K_M' e links do Hugging Face."""
    q = q.strip()
    m = re.match(r"https?://huggingface\.co/([\w.\-]+/[\w.\-]+)", q)
    if m:
        return m.group(1)
    q = q.split(":")[0]
    return q if REPO_RE.match(q) else None


async def buscar(q: str, tipo: str = "texto", limite: int = 20) -> list[dict]:
    q = q.strip()
    if not q:
        return []
    repo = _repo_de(q)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        if repo:  # nome exato do repositório: mostra direto
            r = await c.get(f"{HF}/api/models/{repo}")
            if r.status_code == 200:
                d = r.json()
                return [{"repo": d["id"], "downloads": d.get("downloads", 0), "likes": d.get("likes", 0),
                         "tipo": d.get("pipeline_tag") or "", "tags": (d.get("tags") or [])[:6]}]
        r = await c.get(f"{HF}/api/models", params={"search": q, "filter": "gguf", "sort": "downloads",
                                                    "direction": "-1", "limit": limite * 2})
        r.raise_for_status()
        saida = []
        for m in r.json():
            pt = m.get("pipeline_tag") or ""
            if tipo == "texto" and pt in SEM_TEXTO:
                continue
            if tipo == "imagem" and pt not in ("text-to-image", "image-to-image", ""):
                continue
            saida.append({"repo": m["id"], "downloads": m.get("downloads", 0), "likes": m.get("likes", 0),
                          "tipo": pt, "tags": [t for t in (m.get("tags") or []) if ":" not in t][:6]})
        return saida[:limite]


def _quant(nome: str) -> str:
    m = QUANT_RE.search(nome)
    return m.group(1).upper() if m else ""


async def arquivos(repo: str, tipo: str = "texto") -> list[dict]:
    """Arquivos .gguf do repositório (partes de um modelo dividido viram um item só), com a escolha recomendada."""
    if not REPO_RE.match(repo):
        raise ValueError("Nome de repositório inválido.")
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        r = await c.get(f"{HF}/api/models/{repo}", params={"blobs": "true"})
    if r.status_code != 200:
        raise ValueError("Não achei esse modelo no Hugging Face.")
    grupos: dict[str, dict] = {}
    for f in r.json().get("siblings", []):
        n = f["rfilename"]
        if not n.lower().endswith(".gguf") or "mmproj" in n.lower() or not ARQ_RE.match(n):
            continue
        m = PARTE_RE.match(n)
        base = (m.group(1) + ".gguf") if m else n
        g = grupos.setdefault(base, {"nome": base, "inteiro": None, "partes": [], "quant": _quant(base)})
        if m:
            g["partes"].append((n, f.get("size") or 0))
        else:
            g["inteiro"] = (n, f.get("size") or 0)
    # Alguns repositórios (ex.: Qwen) publicam a mesma quantização inteira E dividida em partes: baixar os dois
    # dobraria o tamanho. Havendo o arquivo inteiro, ele vale; senão, vale o conjunto de partes.
    for g in grupos.values():
        usados = [g["inteiro"]] if g["inteiro"] else g["partes"]
        g["arquivos"] = [n for n, _ in usados]
        g["tam"] = sum(t for _, t in usados)
    itens = sorted(grupos.values(), key=lambda g: g["tam"])
    for g in itens:
        g["arquivos"].sort()
        g["aviso"] = aviso_tamanho(g["tam"], tipo)
    # recomendado: a quantização preferida que cabe bem na RAM (texto) / o menor completo (imagem)
    limite = min(_ram_gb() * 0.40, 8.0) * 1e9
    rec = None
    if tipo == "texto":
        for q in PREFERENCIA:
            rec = next((g for g in itens if g["quant"] == q and g["tam"] <= limite), None)
            if rec:
                break
    elif itens:
        rec = itens[0] if len(itens) == 1 else next((g for g in itens if g["quant"] in ("Q8_0", "F16")), itens[0])
    if rec is None and itens:
        rec = min(itens, key=lambda g: abs(g["tam"] - limite))
    for g in itens:
        g["recomendado"] = g is rec
    return itens


# ------------------------------------------------------------------ download
def _camadas(arquivo: Path) -> int | None:
    """Lê o número de camadas ('block_count') do cabeçalho do .gguf (usado para dividir CPU/placa com precisão)."""
    try:
        with open(arquivo, "rb") as f:
            if f.read(4) != b"GGUF":
                return None
            _ver, _nt, nkv = struct.unpack("<IQQ", f.read(20))

            def le_str():
                (n,) = struct.unpack("<Q", f.read(8))
                return f.read(n).decode("utf-8", "ignore")

            tam = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}

            def le_valor(t):
                if t == 8:
                    return le_str()
                if t == 9:
                    (et,) = struct.unpack("<I", f.read(4))
                    (n,) = struct.unpack("<Q", f.read(8))
                    if et in tam and et != 8:
                        f.seek(tam[et] * n, 1)
                    else:
                        for _ in range(n):
                            le_valor(et)
                    return None
                n = tam[t]
                b = f.read(n)
                return int.from_bytes(b, "little") if t in (0, 1, 2, 3, 4, 5, 10, 11) else None

            for _ in range(nkv):
                k = le_str()
                (t,) = struct.unpack("<I", f.read(4))
                v = le_valor(t)
                if k.endswith(".block_count") and isinstance(v, int):
                    return v
    except (OSError, struct.error, KeyError):
        return None
    return None


async def _baixa(job: dict, repo: str, tipo: str, nome: str, arquivos_: list[str]) -> None:
    pasta = _pasta(tipo)
    pasta.mkdir(parents=True, exist_ok=True)
    try:
        for n in arquivos_:
            destino = pasta / Path(n).name
            if destino.exists() and destino.stat().st_size > 0:
                continue
            parte = destino.with_suffix(".gguf.part")
            proc = await asyncio.create_subprocess_exec(
                "curl", "-L", "--fail", "-C", "-", "-o", str(parte), f"{HF}/{repo}/resolve/main/{n}",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
            job["proc"] = proc
            if await proc.wait() != 0:
                job["erro"] = "O download falhou (sem internet, ou o arquivo exige login no Hugging Face). Tente de novo."
                return
            parte.rename(destino)
        primeiro = pasta / Path(arquivos_[0]).name
        total = sum((pasta / Path(n).name).stat().st_size for n in arquivos_)
        itens = [m for m in lista() if m["id"] != job["id"]]
        itens.append({"id": job["id"], "nome": nome, "repo": repo, "tipo": tipo, "arquivo": primeiro.name,
                      "caminho": str(primeiro), "tam": total, "quant": _quant(primeiro.name),
                      "camadas": _camadas(primeiro) if tipo == "texto" else None})
        _salva(itens)
        if job.get("usar_depois") and tipo == "texto":   # pediram "instala e já deixa selecionado"
            try:
                await asyncio.to_thread(usar, job["id"])
                job["selecionado"] = True
            except Exception as e:   # baixou, mas não deu para trocar sozinho: o modelo fica na lista
                job["aviso"] = str(e)[:240]
    except asyncio.CancelledError:
        raise
    except Exception as e:
        job["erro"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        job["fim"] = True


def baixar(repo: str, arquivos_: list[str], tipo: str = "texto", total: int = 0, usar_depois: bool = False) -> str:
    if not REPO_RE.match(repo) or not arquivos_ or any(not ARQ_RE.match(a) or ".." in a for a in arquivos_):
        raise ValueError("Pedido inválido.")
    id_ = _id(repo, arquivos_[0])
    atual = _baixando.get(id_)
    if atual and not atual.get("fim"):
        return id_
    pasta = _pasta(tipo)
    pasta.mkdir(parents=True, exist_ok=True)
    if total and shutil.disk_usage(pasta).free < total * 1.05:
        raise ValueError(f"Falta espaço no disco: preciso de uns {total / 1e9:.1f} GB livres.")
    nome = repo.split("/")[-1].replace("-GGUF", "").replace("_GGUF", "")
    job = {"id": id_, "repo": repo, "tipo": tipo, "arquivos": [Path(a).name for a in arquivos_], "tam": total,
           "pasta": pasta, "fim": False, "erro": "", "usar_depois": usar_depois,
           "selecionado": False, "aviso": ""}
    job["task"] = asyncio.get_running_loop().create_task(_baixa(job, repo, tipo, nome, arquivos_))
    _baixando[id_] = job
    return id_


def estado() -> dict:
    """Downloads em andamento (com progresso) e modelos já adicionados."""
    andamento = []
    for id_, j in list(_baixando.items()):
        feito = 0
        for n in j["arquivos"]:
            f = j["pasta"] / n
            p = f.with_suffix(".gguf.part")
            feito += f.stat().st_size if f.exists() else (p.stat().st_size if p.exists() else 0)
        andamento.append({"id": id_, "repo": j["repo"], "tipo": j["tipo"], "progresso": round(min(feito / j["tam"], 1.0), 3) if j["tam"] else 0,
                          "feito": feito, "tam": j["tam"], "fim": j["fim"], "erro": j["erro"],
                          "selecionado": j.get("selecionado", False), "aviso": j.get("aviso", "")})
        if j["fim"] and not j["erro"] and any(m["id"] == id_ for m in lista()):
            _baixando.pop(id_, None)
    return {"baixando": andamento, "modelos": lista()}


def cancelar(id_: str) -> bool:
    j = _baixando.get(id_)
    if not j or j.get("fim"):
        return False
    j["task"].cancel()
    p = j.get("proc")
    try:
        if p:
            p.kill()
    except ProcessLookupError:
        pass
    j["fim"], j["erro"] = True, "Cancelado."
    return True


def apagar(id_: str) -> None:
    itens = lista()
    m = next((x for x in itens if x["id"] == id_), None)
    if not m:
        raise ValueError("Modelo não encontrado.")
    pasta = _pasta(m["tipo"])
    nome = m["arquivo"]
    mp = PARTE_RE.match(nome)
    alvos = [nome] if not mp else [p.name for p in pasta.glob(f"{mp.group(1)}-*-of-{mp.group(3)}.gguf")]
    for a in alvos:
        (pasta / Path(a).name).unlink(missing_ok=True)
    _salva([x for x in itens if x["id"] != id_])
    _baixando.pop(id_, None)


def usar(id_: str) -> None:
    """Texto: passa a usar este modelo (grava modelo.env e reinicia o llama-server). Imagem: já fica na lista."""
    m = next((x for x in lista() if x["id"] == id_), None)
    if not m:
        raise ValueError("Modelo não encontrado.")
    if not Path(m["caminho"]).exists():
        raise ValueError("O arquivo do modelo não está mais na pasta.")
    if m["tipo"] != "texto":
        return
    config.MODELO_ENV.parent.mkdir(parents=True, exist_ok=True)
    extra = f"CAMADAS={m['camadas']}\n" if m.get("camadas") else ""
    config.MODELO_ENV.write_text(f"MODEL={m['caminho']}\nNGL=auto\n{extra}")
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", "restart", "localai-llm.service"],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        r = None
    if r is None or r.returncode != 0:
        raise RuntimeError("Salvei a escolha, mas não consegui reiniciar o modelo sozinho. "
                           "Rode no PC: bash atualizar.sh (ele libera essa permissão) ou sudo systemctl restart localai-llm")


# ------------------------------------------------------------------ "ache, instale e já deixe selecionado"
_RUIDO = {"gguf", "modelo", "modelos", "llm", "llms", "ia", "ias", "o", "a", "os", "as", "de", "da", "do", "um", "uma",
          "pra", "para", "no", "na", "me", "meu", "minha", "the", "model", "hugging", "face", "huggingface"}
_QUANT_PEDIDA = re.compile(r"(?i)(?<![\w.])(iq\d_\w+|q\d(?:_k)?(?:_[sml01])?|bf16|f16)(?![\w.])")


def separa_pedido(q: str) -> tuple[str, str]:
    """('qwen2.5 coder 14b q5_k_m') -> ('qwen2.5 coder 14b', 'Q5_K_M'). Links e 'dono/repo:Q4_K_M' ficam como estão."""
    q = q.strip()
    if _repo_de(q):
        return q.split(":")[0] if not q.startswith("http") else q, (q.split(":")[1].upper() if ":" in q and not q.startswith("http") else "")
    m = _QUANT_PEDIDA.search(q)
    quant = m.group(1).upper() if m else ""
    if m:
        q = (q[:m.start()] + " " + q[m.end():]).strip()
    return re.sub(r"\s+", " ", q), quant


def _tokens(q: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9.]+", q.lower()) if t and t not in _RUIDO]


def _pontua(repo: str, tokens: list[str]) -> float:
    r = repo.lower()
    return sum(t in r for t in tokens) / len(tokens) if tokens else 0.0


def escolhe_repo(candidatos: list[dict], q: str) -> tuple[dict | None, bool]:
    """Entre os resultados da busca, o que contém TODAS as palavras do pedido e tem mais downloads.
    Se nenhum contém todas, devolve o que casa mais (aproximado=True)."""
    tk = _tokens(q)
    if not candidatos:
        return None, False
    pont = [(_pontua(c["repo"], tk), c.get("downloads", 0), c) for c in candidatos]
    completos = [p for p in pont if p[0] >= 0.999]
    if completos:
        return max(completos, key=lambda p: p[1])[2], False
    return max(pont, key=lambda p: (p[0], p[1]))[2], True


def _escolhe_arquivo(itens: list[dict], quant: str) -> dict | None:
    if quant:
        q = next((g for g in itens if g["quant"] == quant), None) or \
            next((g for g in itens if g["quant"].startswith(quant)), None)
        if q:
            return q
    return next((g for g in itens if g.get("recomendado")), itens[0] if itens else None)


async def instalar_e_usar(pedido: str, tipo: str = "texto") -> dict:
    """Acha o modelo pelo nome, escolhe a versão que cabe no PC, baixa e, ao terminar, deixa selecionado.

    Devolve {estado: 'baixando'|'pronto', repo, arquivo, tam, aviso, alternativas, aproximado, quant_pedida_ok}.
    Se o modelo já estava baixado, só passa a usá-lo (estado 'pronto')."""
    nome, quant = separa_pedido(pedido)
    if not nome:
        raise ValueError("Diga o nome do modelo que você quer (por exemplo: qwen2.5 coder 7b).")
    resultados = await buscar(nome, tipo, limite=30)
    if not resultados:
        raise ValueError(f"Não achei nada no Hugging Face para \"{nome}\".")
    repo, aproximado = escolhe_repo(resultados, nome)
    alternativas = [r["repo"] for r in resultados if r["repo"] != repo["repo"]][:3]
    itens = await arquivos(repo["repo"], tipo)
    if not itens:
        raise ValueError(f"{repo['repo']} não tem arquivo .gguf para baixar.")
    g = _escolhe_arquivo(itens, quant)
    primeiro = Path(g["arquivos"][0]).name
    info = {"repo": repo["repo"], "arquivo": g["nome"], "quant": g["quant"], "tam": g["tam"], "aviso": g["aviso"],
            "alternativas": alternativas, "aproximado": aproximado, "quant_pedida_ok": (not quant) or g["quant"].startswith(quant)}
    ja = next((m for m in lista() if m["repo"] == repo["repo"] and m["arquivo"] == primeiro
               and Path(m["caminho"]).exists() and m["tipo"] == tipo), None)
    if ja:
        await asyncio.to_thread(usar, ja["id"])
        return {**info, "estado": "pronto", "id": ja["id"]}
    id_ = baixar(repo["repo"], g["arquivos"], tipo, g["tam"], usar_depois=True)
    return {**info, "estado": "baixando", "id": id_}


def acha_instalado(pedido: str) -> dict | None:
    """Procura, entre o que já está no PC (perfis e modelos adicionados), o que casa com o pedido."""
    import perfis
    tk = _tokens(separa_pedido(pedido)[0])
    if not tk:
        return None
    melhor, pts = None, 0.0
    for id_, p in perfis.PERFIS.items():
        if perfis.presente(p):
            v = _pontua(f"{p['nome']} {p['modelo']} {p['arquivo']}", tk)
            if v > pts:
                melhor, pts = {"id": id_, "nome": p["modelo"], "perfil": True}, v
    for m in lista():
        if m["tipo"] == "texto" and Path(m["caminho"]).exists():
            v = _pontua(f"{m['nome']} {m['repo']} {m['arquivo']}", tk)
            if v > pts:
                melhor, pts = {"id": m["id"], "nome": m["nome"], "perfil": False}, v
    return melhor if pts >= 0.999 else None
