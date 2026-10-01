"""Memória de longo prazo: guarda o que o usuário ensina e o que a IA aprende na web.

Usa SQLite com busca de texto (FTS5), sem modelos extras: leve para CPU e RAM.
O modelo em si não muda; ele recebe as memórias relevantes junto com cada pergunta.
"""
import re
import sqlite3
import threading
import time

import config

_lock = threading.Lock()
_db: sqlite3.Connection | None = None

STOP = set("""
a o as os um uma uns umas de do da dos das em no na nos nas por para com sem sob sobre e ou mas que se
como qual quais quem onde quando porque pra pro ao aos isso isto esse essa esses essas este esta estes
estas ele ela eles elas eu voce você nos nós meu minha meus minhas seu sua seus suas foi ser sao são
tem ter era tinha vai vou ja já mais muito muita tambem também so só me te lhe nao não sim favor
""".split())


def _conn() -> sqlite3.Connection:
    global _db
    if _db is None:
        config.MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(config.MEMORY_DB, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.execute(
            "CREATE TABLE IF NOT EXISTS memories("
            "id INTEGER PRIMARY KEY, text TEXT NOT NULL UNIQUE, source TEXT NOT NULL, created REAL NOT NULL)"
        )
        _db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
            "text, tokenize='unicode61 remove_diacritics 2')"
        )
        _db.commit()
    return _db


def add(text: str, source: str = "usuario") -> dict:
    text = text.strip()[:1500]
    if not text:
        raise ValueError("texto vazio")
    with _lock:
        db = _conn()
        row = db.execute("SELECT id FROM memories WHERE text = ?", (text,)).fetchone()
        if row:
            return {"id": row["id"], "duplicate": True}
        cur = db.execute(
            "INSERT INTO memories(text, source, created) VALUES (?, ?, ?)", (text, source, time.time())
        )
        db.execute("INSERT INTO memories_fts(rowid, text) VALUES (?, ?)", (cur.lastrowid, text))
        db.commit()
        return {"id": cur.lastrowid, "duplicate": False}


def delete(mem_id: int) -> None:
    with _lock:
        db = _conn()
        db.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        db.execute("DELETE FROM memories_fts WHERE rowid = ?", (mem_id,))
        db.commit()


def list_all(limit: int = 300) -> list[dict]:
    with _lock:
        rows = _conn().execute(
            "SELECT id, text, source, created FROM memories ORDER BY created DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def _fts_query(text: str) -> str:
    words = [w for w in re.findall(r"\w{3,}", text.lower()) if w not in STOP]
    terms = []
    for w in dict.fromkeys(words):  # sem repetir, mantendo a ordem
        # corta palavras longas e usa prefixo: "cachorros" encontra "cachorro"
        stem = w[:5] if len(w) > 6 else w
        terms.append(f'"{stem}"*' if len(w) > 6 else f'"{stem}"')
    return " OR ".join(terms[:10])


def search(text: str, limit: int = 4) -> list[dict]:
    q = _fts_query(text)
    if not q:
        return []
    with _lock:
        rows = _conn().execute(
            "SELECT m.id, m.text, m.source FROM memories_fts f JOIN memories m ON m.id = f.rowid "
            "WHERE memories_fts MATCH ? ORDER BY bm25(memories_fts) LIMIT ?",
            (q, limit),
        ).fetchall()
    return [dict(r) for r in rows]
