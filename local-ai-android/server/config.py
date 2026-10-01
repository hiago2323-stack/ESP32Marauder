"""Configuração do servidor, lida de variáveis de ambiente (arquivo .env via instalador)."""
import os
from pathlib import Path

HOME = Path.home()

# Token exigido de quem NÃO está no próprio PC (o PC local dispensa o token)
API_TOKEN = os.environ.get("LOCALAI_TOKEN", "")

# Endereço do llama-server (llama.cpp), que expõe uma API compatível com a da OpenAI
LLAMA_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8081")

# Endereço opcional de uma instância SearXNG; vazio = usa DuckDuckGo (biblioteca ddgs)
SEARXNG_URL = os.environ.get("SEARXNG_URL", "")

# Onde ficam os projetos recebidos para compilar
WORK_DIR = Path(os.environ.get("LOCALAI_WORK", str(HOME / "localai-work")))

# Tempo máximo de uma compilação, em segundos
BUILD_TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", "1200"))

# Tamanho máximo do projeto enviado, em MB
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))

# Voz -> texto (faster-whisper, roda na CPU). Opções: tiny, base, small, medium
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")

# Texto -> voz (Piper). Arquivo .onnx da voz (o .onnx.json fica ao lado)
PIPER_VOICE = Path(os.environ.get("PIPER_VOICE", str(HOME / "models" / "pt_BR-faber-medium.onnx")))

SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "Você é uma IA local que roda no computador do usuário. Responda sempre em português do Brasil, "
    "de forma clara e direta. Quando houver resultados de pesquisa na web no contexto, use-os e cite as "
    "fontes (endereços). Se não souber algo, diga que não sabe em vez de inventar.",
)
