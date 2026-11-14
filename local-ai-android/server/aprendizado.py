"""Aprender sozinha, de forma limitada e visível: depois de uma conversa, guarda na memória fatos DURÁVEIS que o usuário
afirmou sobre si (nome, projeto, equipamento, preferências). Nada de código: ela não altera a si mesma.

Cuidados: (1) nunca guarda segredos (senhas, chaves, cartão, documentos); (2) só guarda o que se apoia no que o usuário
escreveu (descarta invenção do modelo); (3) tudo aparece em 🧠 Memória com a etiqueta "aprendi sozinha" e pode ser apagado;
(4) não atrapalha a conversa: se um chat está em andamento ou outro aprendizado já roda, simplesmente pula.
"""
import asyncio
import json
import re

import httpx

import config
import memory

FONTE = "aprendido"
LIMITE_GUARDADOS = 200          # passando disso, os mais antigos "aprendidos" saem
ativos = 0                      # conversas em andamento (main.py soma e subtrai)
_rodando = False

SEGREDO = re.compile(
    r"(?i)(senha|password|passwd|\btoken\b|api[_ -]?key|chave (?:de )?(?:api|privada|pix)|secret|segredo|cart[aã]o|\bcvv\b|"
    r"\bcpf\b|\bcnpj\b|\bpix\b|\bcnh\b|\d{3}\.?\d{3}\.?\d{3}-?\d{2}|(?:\d[ -]?){13,19}|sk-[A-Za-z0-9_-]{10,}|"
    r"gh[pousr]_[A-Za-z0-9]{10,}|bearer\s+\S{10,}|-----BEGIN)")
SENSIVEL = re.compile(r"(?i)(diagn[oó]stic|doen[cç]a|rem[eé]dio|depress|sal[aá]rio|dívida|divida|endere[cç]o|mora (?:na|no|em))")

SISTEMA = (
    "Você extrai o que vale lembrar sobre o USUÁRIO, a partir da mensagem dele. Responda SOMENTE um JSON: "
    "{\"fatos\": [\"...\"]}, com no máximo 2 itens curtos em português, no formato \"O usuário ...\".\n"
    "Vale: nome, profissão, projeto em andamento, equipamento que usa, gostos e como prefere as respostas "
    "(só o que ELE afirmou sobre si ou pediu para lembrar).\n"
    "Não vale: perguntas de curiosidade, tarefas pontuais, o que a IA respondeu, opiniões sobre terceiros, e dados sensíveis "
    "(senhas, documentos, saúde, finanças, endereço). Se nada vale, devolva {\"fatos\": []}.\n"
    "Exemplos:\n"
    "\"meu PC tem uma RX 580 de 8 GB e eu uso Linux Mint\" -> {\"fatos\": [\"O usuário tem um PC com RX 580 de 8 GB e usa Linux Mint.\"]}\n"
    "\"me explica o que é recursão\" -> {\"fatos\": []}")


def _palavras(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-zà-ú0-9]{4,}", s.lower())}


def _apoiado(fato: str, fala: str) -> bool:
    """O fato precisa repetir pelo menos duas palavras relevantes (4+ letras) da fala do usuário."""
    return len(_palavras(fato) & _palavras(fala)) >= 2


def _poda() -> None:
    antigos = [m for m in memory.list_all(1000) if m["source"] == FONTE]
    for m in antigos[LIMITE_GUARDADOS:]:          # list_all vem do mais novo para o mais antigo
        memory.delete(m["id"])


async def _pergunta_ao_modelo(fala: str) -> list[str]:
    corpo = {"messages": [{"role": "system", "content": SISTEMA}, {"role": "user", "content": fala[:1500]}],
             "temperature": 0, "max_tokens": 160, "cache_prompt": True, "response_format": {"type": "json_object"}}
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{config.LLAMA_URL}/v1/chat/completions", json=corpo)
    r.raise_for_status()
    txt = r.json()["choices"][0]["message"]["content"]
    j = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    fatos = j.get("fatos")
    return [f.strip() for f in fatos if isinstance(f, str)] if isinstance(fatos, list) else []


async def aprende(pergunta: str, resposta: str = "") -> list[str]:
    """Devolve os fatos que guardou (vazio se não havia nada, se pulou ou se algo falhou)."""
    global _rodando
    fala = pergunta.strip()
    if _rodando or ativos > 0 or len(fala) < 15:
        return []
    if SEGREDO.search(fala) or SEGREDO.search(resposta or ""):
        return []
    _rodando = True
    try:
        candidatos = await _pergunta_ao_modelo(fala)
    except Exception:
        return []
    finally:
        _rodando = False
    guardados = []
    for f in candidatos[:2]:
        f = f[:240]
        if not f or SEGREDO.search(f) or SENSIVEL.search(f) or not _apoiado(f, fala):
            continue
        r = await asyncio.to_thread(memory.add, f, FONTE)
        if not r.get("duplicate"):
            guardados.append(f)
    if guardados:
        await asyncio.to_thread(_poda)
    return guardados
