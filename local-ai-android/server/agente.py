"""Entende pedidos em linguagem livre e executa (sem frases fixas): trocar, achar e instalar modelos de IA.

Fluxo: um filtro barato decide se a mensagem PODE ser um comando sobre modelos de IA; só então a própria IA local
interpreta o pedido (JSON curto). Se ela estiver desligada ou reiniciando, regras simples fazem o mesmo.
Mensagens que não são comandos seguem a conversa normal (devolve None).
"""
import json
import re

import httpx

import config
import modelos
import perfis

# a mensagem fala de modelos de IA (palavra genérica ou nome de família conhecida)...
ASSUNTO = re.compile(r"(?i)\b(modelos?|llms?|gguf|hugging ?face|ias?|llama|qwen|mistral|mixtral|gemma|phi|deepseek|"
                     r"granite|smollm|olmo|falcon|starcoder|codestral|nemotron|hermes|dolphin|tinyllama|yi|solar|"
                     r"stable ?diffusion|sdxl|flux)\b")
# ...e pede uma ação
ACAO = re.compile(r"(?i)\b(instal\w*|baix\w*|coloc\w*|bot[ae]\w*|troc\w*|mud\w*|us(?:e|ar|a|ando)|ativ\w*|selecion\w*|"
                  r"test\w*|carreg\w*|quero|queria|procur\w*|ach\w*|busc\w*|traz\w*|pega\w*|trabalh\w*|lista\w*|mostr\w*|"
                  r"quais|qual)\b")
FILLER = {"instala", "instale", "instalar", "baixa", "baixe", "baixar", "coloca", "coloque", "colocar", "bota", "bote",
          "botar", "troca", "troque", "trocar", "muda", "mude", "mudar", "usa", "use", "usar", "ativa", "ative", "ativar",
          "seleciona", "selecione", "selecionar", "testa", "teste", "testar", "carrega", "carregue", "carregar", "quero",
          "queria", "procura", "procure", "procurar", "acha", "ache", "achar", "busca", "busque", "buscar", "traz", "traga",
          "pega", "pegue", "pegar", "agora", "aí", "ai", "por", "favor", "pf", "pfv", "eu", "pra", "para", "que", "esse",
          "este", "essa", "esta", "aquele", "aquela", "meu", "minha", "no", "na", "de", "da", "do", "um", "uma", "o", "a",
          "os", "as", "e", "ou", "modelo", "modelos", "llm", "llms", "ia", "ias", "online", "internet", "hugging", "face",
          "huggingface", "gguf", "já", "ja", "deixa", "deixe", "deixar", "selecionado", "selecionada", "testando", "teste"}

SISTEMA = (
    "Você decide se a mensagem do usuário é um PEDIDO para gerenciar modelos de IA neste computador. "
    "Responda SOMENTE um JSON: {\"acao\": ..., \"consulta\": ..., \"tipo\": ...}.\n"
    "acao: \"instalar_modelo\" (achar na internet/baixar/instalar/testar um modelo que talvez não esteja no PC), "
    "\"usar_modelo\" (trocar para um modelo já instalado), \"listar_modelos\" (ver o que está instalado ou em uso), "
    "\"nenhuma\" (qualquer outra coisa: perguntas sobre IA, conversa, explicações).\n"
    "consulta: o nome do modelo como se digitaria numa busca (ex.: \"qwen2.5 coder 14b\"), sem verbos nem enfeites; "
    "vazio se não houver.\n"
    "tipo: \"texto\" (conversa/código) ou \"imagem\" (gera imagens, ex.: stable diffusion).\n"
    "Exemplos:\n"
    "\"bota o qwen coder de 14b pra eu testar\" -> {\"acao\":\"instalar_modelo\",\"consulta\":\"qwen2.5 coder 14b\",\"tipo\":\"texto\"}\n"
    "\"quero testar o deepseek r1 pequeno\" -> {\"acao\":\"instalar_modelo\",\"consulta\":\"deepseek r1 distill 7b\",\"tipo\":\"texto\"}\n"
    "\"volta pro modelo rápido\" -> {\"acao\":\"usar_modelo\",\"consulta\":\"rápido\",\"tipo\":\"texto\"}\n"
    "\"que modelos eu tenho?\" -> {\"acao\":\"listar_modelos\",\"consulta\":\"\",\"tipo\":\"texto\"}\n"
    "\"o que é um LLM?\" -> {\"acao\":\"nenhuma\",\"consulta\":\"\",\"tipo\":\"texto\"}"
)
ACOES = {"instalar_modelo", "usar_modelo", "listar_modelos", "nenhuma"}


def parece_comando(msg: str) -> bool:
    return 3 <= len(msg) <= 400 and bool(ASSUNTO.search(msg)) and bool(ACAO.search(msg))


def heuristica(msg: str) -> dict:
    """Plano B sem IA: tira os verbos e enfeites e fica com o nome do modelo."""
    if re.search(r"(?i)\b(que|quais|qual)\b.*\bmodelos?\b.*\b(tenho|tem|instalad\w+|uso|usando)\b|"
                 r"\b(lista\w*|mostr\w*)\b.*\bmodelos?\b", msg):
        return {"acao": "listar_modelos", "consulta": "", "tipo": "texto"}
    if msg.strip().endswith("?"):   # pergunta, não pedido
        return {"acao": "nenhuma", "consulta": "", "tipo": "texto"}
    palavras = [p for p in re.split(r"[^\w.\-/:+]+", msg) if p]
    resto = [p for p in palavras if p.lower() not in FILLER]
    tipo = "imagem" if re.search(r"(?i)stable ?diffusion|sdxl|flux|imagens?", msg) else "texto"
    if not resto or not ACAO.search(msg):
        return {"acao": "nenhuma", "consulta": "", "tipo": "texto"}
    return {"acao": "instalar_modelo", "consulta": " ".join(resto), "tipo": tipo}


async def interpreta(msg: str) -> dict:
    payload = {"messages": [{"role": "system", "content": SISTEMA}, {"role": "user", "content": msg}],
               "temperature": 0, "max_tokens": 120, "cache_prompt": True,
               "response_format": {"type": "json_object"}}
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.post(f"{config.LLAMA_URL}/v1/chat/completions", json=payload)
        r.raise_for_status()
        txt = r.json()["choices"][0]["message"]["content"]
        j = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
        if j.get("acao") in ACOES:
            return {"acao": j["acao"], "consulta": str(j.get("consulta") or "")[:120].strip(),
                    "tipo": "imagem" if j.get("tipo") == "imagem" else "texto"}
    except Exception:
        pass
    return heuristica(msg)


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB".replace(".", ",")


async def _instalar(consulta: str, tipo: str) -> str:
    try:
        r = await modelos.instalar_e_usar(consulta, tipo)
    except ValueError as e:
        return f"Não consegui: {e}"
    except httpx.HTTPError:
        return "Não consegui falar com o Hugging Face agora. O PC está sem internet?"
    linhas = []
    nome = f"**{r['repo']}**" + (f" ({r['quant']}, {_gb(r['tam'])})" if r["quant"] else f" ({_gb(r['tam'])})")
    if r["aproximado"]:
        linhas.append(f"Não achei um modelo com exatamente esse nome; o mais parecido foi {nome}.")
    else:
        linhas.append(f"Achei {nome}.")
    if not r["quant_pedida_ok"]:
        linhas.append("Essa versão (quantização) não existe nesse repositório; escolhi a que cabe melhor no seu PC.")
    if r["aviso"]:
        linhas.append(f"⚠ {r['aviso']}")
    if r["estado"] == "pronto":
        linhas.append("Já estava baixado: passei a usar agora. O modelo recarrega em alguns minutos; é só testar depois.")
    elif tipo == "imagem":
        linhas.append("Estou baixando; quando terminar ele aparece na lista de modelos de imagem. "
                      "Acompanhe em ⚙ Configurações › Modelo de IA.")
    else:
        linhas.append("Estou baixando agora. Quando terminar, **deixo selecionado e reinicio o modelo** para você testar "
                      "(a conversa fica indisponível por alguns minutos nessa troca). Acompanhe em ⚙ Configurações › Modelo de IA.")
    if r["alternativas"]:
        linhas.append("Se não era esse, diga o nome exato ou cole o link do Hugging Face. Outros que apareceram: "
                      + ", ".join(r["alternativas"]) + ".")
    return "\n\n".join(linhas)


async def _usar(consulta: str, tipo: str) -> str:
    import asyncio
    achado = modelos.acha_instalado(consulta)
    if not achado:
        return "Esse eu não tenho instalado. " + await _instalar(consulta, tipo)
    try:
        if achado["perfil"]:
            await asyncio.to_thread(perfis.seleciona, achado["id"])
        else:
            await asyncio.to_thread(modelos.usar, achado["id"])
    except (ValueError, RuntimeError) as e:
        return f"Não consegui trocar: {e}"
    return f"Troquei para **{achado['nome']}**. O modelo recarrega em alguns minutos; é só testar depois."


def _lista() -> str:
    atual = perfis._le_env().get("MODEL", "")
    from pathlib import Path
    em_uso = Path(atual).name if atual else ""
    linhas = []
    for p in perfis.lista():
        if p["presente"]:
            linhas.append(f"- {p['modelo']} ({p['nome']})" + (" ← em uso" if p["id"] == perfis.atual() else ""))
    for m in modelos.lista():
        if m["tipo"] == "texto":
            linhas.append(f"- {m['nome']} ({m.get('quant') or '?'}, {_gb(m['tam'])})" + (" ← em uso" if m["arquivo"] == em_uso else ""))
    return "Modelos de texto instalados:\n" + ("\n".join(linhas) if linhas else "- nenhum") + \
        "\n\nPara trazer outro, é só pedir: por exemplo, \"instala o gemma 3 4b\"."


async def tenta(msg: str) -> str | None:
    """Se a mensagem é um comando sobre modelos, executa e devolve a resposta; senão, None (conversa normal)."""
    if not parece_comando(msg):
        return None
    it = await interpreta(msg)
    if it["acao"] == "instalar_modelo" and it["consulta"]:
        return await _instalar(it["consulta"], it["tipo"])
    if it["acao"] == "usar_modelo" and it["consulta"]:
        return await _usar(it["consulta"], it["tipo"])
    if it["acao"] == "listar_modelos":
        return _lista()
    return None
