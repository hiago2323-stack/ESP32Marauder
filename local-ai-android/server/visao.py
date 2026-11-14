"""Visão: descreve uma imagem (a foto do usuário, imagens anexadas) com um modelo que enxerga.

O modelo (Qwen2.5-VL 3B) é baixado na primeira vez. A análise sobe uma segunda cópia do llama-server só com ele,
na CPU (a placa fica para o modelo de conversa), e a desliga em seguida: nada fica ocupando memória depois.
"""
import asyncio
import base64
import io
import os
import time

import httpx

import config
import modelos
import recursos

PROMPT_FOTO = (
    "Esta é uma foto do próprio usuário, que a enviou para a IA saber como ele é. Descreva em português do Brasil, "
    "em 3 a 5 frases, apenas o que dá para ver: cabelo (cor, comprimento, estilo), barba ou bigode, óculos, faixa etária "
    "aparente, expressão, roupa, acessórios e o fundo. Não adivinhe nome, etnia ou raça, saúde, religião, orientação ou "
    "qualquer coisa que não esteja visível. Se não houver uma pessoa na foto, diga só isso."
)


def arquivos() -> tuple[str, str] | None:
    pasta = config.MODELS_DIR / "visao"
    m, p = (pasta / n for n in config.VISAO_ARQUIVOS)
    return (str(m), str(p)) if m.exists() and p.exists() else None


def pronto() -> bool:
    return arquivos() is not None


def baixar() -> str | None:
    """Começa o download do modelo de visão (se ainda não existe). Devolve o id do download, ou None se já está pronto."""
    if pronto():
        return None
    return modelos.baixar(config.VISAO_REPO, config.VISAO_ARQUIVOS, "visao", config.VISAO_TAM)


def progresso() -> dict | None:
    for j in modelos.estado()["baixando"]:
        if j["tipo"] == "visao":
            return j
    return None


def _jpeg_pequeno(caminho, maximo: int = 896) -> str:
    """A imagem reduzida (lado maior 896 px) em base64: foto de celular em tamanho cheio deixaria a análise lentíssima."""
    from PIL import Image, ImageOps
    with Image.open(caminho) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im.thumbnail((maximo, maximo))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=88)
    return base64.b64encode(buf.getvalue()).decode()


async def descreve(caminho, prompt: str = PROMPT_FOTO, espera_max: int = 240) -> str:
    """Descreve a imagem. Levanta RuntimeError com uma mensagem em português se algo falhar."""
    arq = arquivos()
    if not arq:
        raise RuntimeError("O modelo de visão ainda não foi baixado.")
    if not config.LLAMA_SERVER_BIN.exists():
        raise RuntimeError("Não achei o llama-server. Rode no PC: bash atualizar.sh")
    b64 = await asyncio.to_thread(_jpeg_pequeno, caminho)
    async with recursos.pesado:
        recursos.garante_ram(4500, "analisar a imagem")
        porta = config.VISAO_PORTA
        proc = await asyncio.create_subprocess_exec(
            str(config.LLAMA_SERVER_BIN), "-m", arq[0], "--mmproj", arq[1], "--host", "127.0.0.1", "--port", str(porta),
            "-ngl", "0", "-c", "4096", "-t", str(recursos.nucleos_fisicos()),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
        try:
            url = f"http://127.0.0.1:{porta}"
            limite = time.time() + 180
            async with httpx.AsyncClient(timeout=10) as c:
                while True:
                    if proc.returncode is not None:
                        raise RuntimeError("O modelo de visão não iniciou (versão do llama.cpp sem suporte a imagens?). "
                                           "Rode no PC: bash atualizar.sh")
                    try:
                        if (await c.get(f"{url}/health")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.time() > limite:
                        raise RuntimeError("O modelo de visão demorou demais para carregar.")
                    await asyncio.sleep(1.5)
            corpo = {"messages": [{"role": "user", "content": [
                         {"type": "text", "text": prompt},
                         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}],
                     "temperature": 0.1, "max_tokens": 350}
            async with httpx.AsyncClient(timeout=espera_max) as c:
                r = await c.post(f"{url}/v1/chat/completions", json=corpo)
            if r.status_code != 200:
                raise RuntimeError(f"O modelo de visão respondeu com erro {r.status_code}.")
            return r.json()["choices"][0]["message"]["content"].strip()
        except httpx.TimeoutException:
            raise RuntimeError("A análise da imagem demorou demais.")
        finally:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
