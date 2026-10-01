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

# Texto -> voz, voz FEMININA (Kokoro, pt-BR). Se os arquivos não existirem, cai no Piper (masculina).
# Vozes pt-BR do Kokoro: pf_dora (feminina), pm_alex e pm_santa (masculinas)
KOKORO_MODEL = Path(os.environ.get("KOKORO_MODEL", str(HOME / "models" / "kokoro-v1.0.onnx")))
KOKORO_VOICES = Path(os.environ.get("KOKORO_VOICES", str(HOME / "models" / "voices-v1.0.bin")))
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "pf_dora")
TTS_SPEED = float(os.environ.get("TTS_SPEED", "1.0"))

# Texto -> voz reserva (Piper). Arquivo .onnx da voz (o .onnx.json fica ao lado)
PIPER_VOICE = Path(os.environ.get("PIPER_VOICE", str(HOME / "models" / "pt_BR-faber-medium.onnx")))

SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "Você é uma IA local que roda no computador do usuário. Responda sempre em português do Brasil, "
    "de forma clara e direta. Quando houver resultados de pesquisa na web no contexto, use-os e cite as "
    "fontes (endereços). Se não souber algo, diga que não sabe em vez de inventar.",
)

# Banco da memória de longo prazo (o que o usuário ensina e o que a IA aprende)
MEMORY_DB = Path(os.environ.get("MEMORY_DB", str(HOME / "localai" / "memory.db")))

# ---- Criação de apps Android pela IA ----
# Onde ficam os apps criados (APK + código) e o Gradle usado para compilar
APPS_DIR = Path(os.environ.get("APPS_DIR", str(HOME / "localai" / "apps")))
GRADLE_BIN = os.environ.get("GRADLE_BIN", str(HOME / "gradle" / "gradle-8.7" / "bin" / "gradle"))
# Quantas vezes a IA tenta corrigir o código quando a compilação falha
MAX_FIX_ATTEMPTS = int(os.environ.get("MAX_FIX_ATTEMPTS", "2"))
# Limite de palavras (tokens) que a IA pode escrever por tentativa
GEN_MAX_TOKENS = int(os.environ.get("GEN_MAX_TOKENS", "2500"))
