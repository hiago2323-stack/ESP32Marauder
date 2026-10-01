"""Biblioteca local: documentação e código de referência guardados no disco grande.

A pasta ~/biblioteca (um link para o disco de 500 GB) tem:
  docs/    documentação (Python, MDN/web...)       codigo/  exemplos e bibliotecas (ESP32, Android...)
  zim/     Wikipedia em português (offline)         meus/    qualquer arquivo seu: a IA passa a consultar
O servidor cria um índice de busca (SQLite FTS5) com tudo isso, em segundo plano e com prioridade baixa.
Na hora de responder ou de criar apps/firmware, os trechos mais parecidos com o pedido entram no contexto
da IA, assim ela acerta nomes de funções e usos sem depender da internet.
"""
import html
import os
import re
import shutil
import sqlite3
import threading
import time
from pathlib import Path

import config

EXTENSOES = {".md", ".rst", ".txt", ".h", ".hpp", ".c", ".cpp", ".ino", ".java", ".kt", ".py",
             ".html", ".htm", ".gradle", ".xml", ".js", ".ts", ".css"}
IGNORAR = {".git", "node_modules", "build", "__pycache__", ".gradle", "dist", "venv", ".venv"}
MAX_ARQUIVO = 400_000
TAM_TRECHO = 1300
PARADAS = {"para", "como", "qual", "quais", "com", "uma", "que", "por", "mais", "isso", "este", "esta", "the", "and",
           "for", "you", "with", "create", "crie", "faca", "fazer", "app", "quero", "preciso", "gere", "pode"}

_estado = {"rodando": False, "arquivos": 0, "trechos": 0, "inicio": 0.0, "fim": 0.0, "erro": ""}
_trava = threading.Lock()


def raiz() -> Path:
    return config.BIBLIOTECA_DIR


def disponivel() -> bool:
    try:
        return raiz().is_dir() and os.access(raiz(), os.W_OK)
    except OSError:
        return False


def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(raiz() / "indice.db"), timeout=30)
    con.execute("CREATE TABLE IF NOT EXISTS arquivos(caminho TEXT PRIMARY KEY, mtime REAL, fonte TEXT)")
    con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS trechos USING fts5("
                "texto, caminho UNINDEXED, fonte UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
    return con


def _fonte(rel: Path) -> str:
    return "/".join(rel.parts[:2]) if len(rel.parts) > 2 else (rel.parts[0] if rel.parts else "")


def _texto(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_ARQUIVO:
            return ""
        t = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    if path.suffix.lower() in (".html", ".htm"):
        t = re.sub(r"(?is)<(script|style).*?</\1>", " ", t)
        t = html.unescape(re.sub(r"<[^>]+>", " ", t))
    return t


def _trechos(texto: str) -> list[str]:
    saida, atual = [], ""
    for bloco in re.split(r"\n\s*\n", texto):
        bloco = bloco.strip()
        if not bloco:
            continue
        if len(atual) + len(bloco) + 2 <= TAM_TRECHO:
            atual = (atual + "\n\n" + bloco) if atual else bloco
            continue
        if atual:
            saida.append(atual)
        while len(bloco) > TAM_TRECHO:  # bloco enorme (código sem linhas em branco): corta nas quebras de linha
            corte = bloco.rfind("\n", 0, TAM_TRECHO)
            corte = corte if corte > 200 else TAM_TRECHO
            saida.append(bloco[:corte])
            bloco = bloco[corte:].strip()
        atual = bloco
    if atual:
        saida.append(atual)
    return [t for t in saida if len(t) > 40]


def indexar(forcar: bool = False) -> dict:
    """Indexa arquivos novos ou alterados (e esquece os apagados). Roda em segundo plano, devagar."""
    if not disponivel() or not _trava.acquire(blocking=False):
        return dict(_estado)
    try:
        _estado.update(rodando=True, arquivos=0, trechos=0, inicio=time.time(), erro="")
        try:
            os.nice(10)
        except (OSError, AttributeError):
            pass
        con = _db()
        if forcar:
            con.execute("DELETE FROM arquivos")
            con.execute("DELETE FROM trechos")
        conhecidos = {c: m for c, m in con.execute("SELECT caminho, mtime FROM arquivos")}
        vistos = set()
        base = raiz()
        feitos = 0
        for dirpath, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in IGNORAR and not d.startswith(".")]
            for nome in files:
                p = Path(dirpath) / nome
                if p.suffix.lower() not in EXTENSOES or nome == "indice.db":
                    continue
                rel = p.relative_to(base)
                chave = str(rel)
                vistos.add(chave)
                try:
                    mt = p.stat().st_mtime
                except OSError:
                    continue
                if conhecidos.get(chave) == mt:
                    continue
                con.execute("DELETE FROM trechos WHERE caminho=?", (chave,))
                partes = _trechos(_texto(p))
                fonte = _fonte(rel)
                con.executemany("INSERT INTO trechos(texto, caminho, fonte) VALUES (?,?,?)",
                                [(t, chave, fonte) for t in partes])
                con.execute("INSERT OR REPLACE INTO arquivos VALUES (?,?,?)", (chave, mt, fonte))
                _estado["arquivos"] += 1
                _estado["trechos"] += len(partes)
                feitos += 1
                if feitos % 200 == 0:
                    con.commit()
                    time.sleep(0.05)  # deixa o resto do PC respirar
        for velho in set(conhecidos) - vistos:
            con.execute("DELETE FROM trechos WHERE caminho=?", (velho,))
            con.execute("DELETE FROM arquivos WHERE caminho=?", (velho,))
        con.commit()
        con.close()
    except Exception as e:  # disco desmontado no meio, banco travado...
        _estado["erro"] = f"{type(e).__name__}: {e}"[:200]
    finally:
        _estado.update(rodando=False, fim=time.time())
        _trava.release()
    return dict(_estado)


def _palavras(consulta: str) -> list[str]:
    vistos = []
    for w in re.findall(r"[\w.+#]{3,}", consulta.lower()):
        w = w.strip(".+#")
        if len(w) >= 3 and w not in PARADAS and w not in vistos:
            vistos.append(w)
    return vistos[:8]


def busca(consulta: str, k: int = 3, fontes: tuple[str, ...] | None = None) -> list[dict]:
    """Trechos mais parecidos com a consulta. Só devolve os que casam com pelo menos 2 palavras (ou 1, se
    a consulta só tem uma), para não encher a conversa de ruído."""
    palavras = _palavras(consulta)
    if not palavras or not disponivel() or not (raiz() / "indice.db").exists():
        return []
    consulta_fts = " OR ".join('"' + w.replace('"', "") + '"' for w in palavras)
    try:
        con = _db()
        linhas = con.execute("SELECT texto, caminho, fonte FROM trechos WHERE trechos MATCH ? "
                             "ORDER BY bm25(trechos) LIMIT 40", (consulta_fts,)).fetchall()
        con.close()
    except sqlite3.Error:
        return []
    minimo = min(2, len(palavras))
    saida = []
    for texto, caminho, fonte in linhas:
        if fontes and not any(fonte.startswith(f) for f in fontes):
            continue
        baixo = texto.lower()
        # nome técnico (os.listdir, esp32, set_server...) é raro o bastante para valer por duas palavras
        pontos = sum(2 if re.search(r"[._\d]", w) else 1 for w in palavras if w in baixo)
        if pontos >= minimo:
            saida.append({"texto": texto, "caminho": caminho, "fonte": fonte})
        if len(saida) >= k:
            break
    return saida


# ------------------------------------------------------------------ Wikipedia offline (arquivos .zim)
def _zims() -> list[Path]:
    d = raiz() / "zim"
    return sorted(d.glob("*.zim")) if d.is_dir() else []


def wikipedia(consulta: str, k: int = 2, max_chars: int = 700) -> list[dict]:
    """Busca nos arquivos .zim (Wikipedia offline). Precisa do pacote libzim; sem ele, devolve vazio."""
    zims = _zims()
    if not zims or not _palavras(consulta):
        return []
    try:
        from libzim.reader import Archive
        from libzim.search import Query, Searcher
    except ImportError:
        return []
    saida = []
    for z in zims:
        try:
            arq = Archive(str(z))
            res = Searcher(arq).search(Query().set_query(consulta))
            for caminho in res.getResults(0, k):
                e = arq.get_entry_by_path(caminho)
                item = e.get_item()
                if "html" not in item.mimetype:
                    continue
                t = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ",
                                                                  bytes(item.content).decode("utf-8", "ignore"))))
                t = re.sub(r"\s+", " ", t).strip()
                saida.append({"titulo": e.title, "texto": t[:max_chars], "fonte": z.name})
        except Exception:
            continue
        if len(saida) >= k:
            break
    return saida[:k]


# ------------------------------------------------------------------ o que entra no contexto da IA
def contexto_chat(pergunta: str, max_chars: int = 1400) -> str:
    achados = busca(pergunta, k=3)
    if not achados:
        return ""
    texto, total = [], 0
    for a in achados:
        t = a["texto"][:520]
        total += len(t)
        if total > max_chars:
            break
        texto.append(f"[{a['fonte']}] {t}")
    return "Da biblioteca local (use se ajudar):\n" + "\n---\n".join(texto)


def referencias(descricao: str, max_chars: int = 1800) -> str:
    """Trechos de exemplos e documentação para ajudar a escrever o código pedido (apps, firmware, web, python)."""
    achados = busca(descricao, k=4, fontes=("codigo", "docs", "meus"))
    if not achados:
        return ""
    texto, total = [], 0
    for a in achados:
        t = a["texto"][:600]
        total += len(t)
        if total > max_chars:
            break
        texto.append(f"// {a['caminho']}\n{t}")
    return ("\n\nReferências da biblioteca local (exemplos reais; use os nomes de funções e classes se servirem, "
            "ignore se não tiverem a ver):\n" + "\n---\n".join(texto))


# ------------------------------------------------------------------ painel
def status() -> dict:
    r = raiz()
    info = {"pasta": str(r), "destino": "", "disponivel": disponivel(), "livre_gb": None, "total_gb": None,
            "fontes": [], "zims": [], "indexando": _estado["rodando"], "ultimo_erro": _estado["erro"],
            "wikipedia_pronta": False}
    try:
        info["destino"] = str(r.resolve())
        u = shutil.disk_usage(r if r.exists() else r.parent)
        info["livre_gb"], info["total_gb"] = round(u.free / 1e9), round(u.total / 1e9)
    except OSError:
        pass
    if info["disponivel"]:
        try:
            if (r / "indice.db").exists():
                con = _db()
                info["fontes"] = [{"nome": f, "trechos": n, "arquivos": a} for f, n, a in con.execute(
                    "SELECT t.fonte, COUNT(*), COUNT(DISTINCT t.caminho) FROM trechos t GROUP BY t.fonte ORDER BY 2 DESC")]
                con.close()
        except sqlite3.Error:
            pass
        info["zims"] = [{"nome": z.name, "gb": round(z.stat().st_size / 1e9, 1)} for z in _zims()]
        try:
            import libzim  # noqa: F401
            info["wikipedia_pronta"] = bool(info["zims"])
        except ImportError:
            pass
    return info
