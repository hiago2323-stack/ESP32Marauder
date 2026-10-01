"""Configuração do servidor, lida de variáveis de ambiente (arquivo .env via instalador)."""
import os
from pathlib import Path

HOME = Path.home()

# Token exigido de quem NÃO está no próprio PC (o PC local dispensa o token)
API_TOKEN = os.environ.get("LOCALAI_TOKEN", "")
# Quem chega pela VPN Tailscale (100.64.0.0/10) já foi autenticado pela sua conta: entra sem token.
# Para exigir o token também na VPN: TAILNET_SEM_TOKEN=0
TAILNET_SEM_TOKEN = os.environ.get("TAILNET_SEM_TOKEN", "1") != "0"

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
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "large-v3-turbo")  # se não carregar, usa o "small"
WHISPER_FALLBACK = os.environ.get("WHISPER_FALLBACK", "small")
# Dica de vocabulário: faz o reconhecimento acertar termos técnicos (medido: 96% -> 99% de acerto)
STT_DICA = os.environ.get(
    "STT_DICA",
    "Conversa em português do Brasil sobre tecnologia. Termos: ESP32, Android, APK, Tailscale, Gradle, Arduino, "
    "Wi-Fi, Bluetooth, firmware, GPU, CPU, Python, Linux, Kotlin, Java.",
)

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
    "de forma clara, direta e correta. Quando a mensagem trouxer um bloco [CONTEXTO], use-o: ele tem "
    "memórias do usuário e resultados de pesquisa na web; cite os endereços das fontes usadas. "
    "Se não tiver certeza ou não souber, diga isso em vez de inventar. Responda direto ao ponto, sem sermões, "
    "sem avisos desnecessários e sem rodeios: trate o usuário como um adulto capaz. "
    "Entenda os pedidos de forma sumária: capte a essência de pedidos curtos ou vagos (por exemplo, "
    "\"app de lista\" ou \"gato astronauta\"), assuma padrões sensatos e entregue em vez de devolver perguntas; "
    "só pergunte se faltar algo sem o qual não dá para fazer. Respostas curtas e objetivas, com detalhes só se pedirem.",
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

# arduino-cli (compila firmware ESP32)
ARDUINO_CLI = os.environ.get("ARDUINO_CLI", str(HOME / "bin" / "arduino-cli"))

# ---- Desempenho e precisão ----
MODELS_DIR = Path(os.environ.get("MODELS_DIR", str(HOME / "models")))
MODELO_ENV = Path(os.environ.get("MODELO_ENV", str(HOME / "localai" / "modelo.env")))   # perfil escolhido na tela (o start_llm.sh lê este arquivo)

# ---- Anexos, imagens e recursos ----
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", str(HOME / "localai" / "uploads")))
OCIOSO_MIN = int(os.environ.get("OCIOSO_MIN", "10"))   # minutos parado até descarregar um modelo de voz da RAM

# Gerador de imagens (stable-diffusion.cpp): SD-Turbo quantizado (~2 GB), 1 a 4 passos
IMG_MODEL = Path(os.environ.get("IMG_MODEL", str(HOME / "models" / "imagens" / "sd_turbo-f16-q8_0.gguf")))
IMG_URL = "https://huggingface.co/Green-Sky/SD-Turbo-GGUF/resolve/main/sd_turbo-f16-q8_0.gguf"
IMG_TAM = 2023745376
# Modelos de imagem disponíveis (o rápido e um de fotorrealismo). Todos rodam 100% local, sem filtro extra nosso.
_NEG_FOTO = ("(worst quality, low quality:1.4), blurry, deformed, bad anatomy, extra fingers, extra limbs, "
             "cartoon, illustration, painting, 3d render, watermark, text, signature")
IMG_MODELOS = {
    "rapido": {"nome": "Rápido (SD-Turbo)", "arquivo": IMG_MODEL, "url": IMG_URL, "tam": IMG_TAM,
               "cfg": 1.0, "passos": 4, "negativo": ""},
    "realista": {"nome": "Realista (fotos)", "tam": 1765950304, "cfg": 2.0, "passos": 6, "negativo": _NEG_FOTO,
                 "arquivo": IMG_MODEL.parent / "realisticVisionV60B1_v51HyperVAE-Q8_0.gguf",
                 "url": "https://huggingface.co/second-state/Realistic_Vision_V6.0_B1-GGUF/resolve/main/"
                        "realisticVisionV60B1_v51HyperVAE-Q8_0.gguf"},
}

# ---- Biblioteca local (documentação e código de referência; fica no disco grande via link ~/biblioteca) ----
BIBLIOTECA_DIR = Path(os.environ.get("BIBLIOTECA_DIR", str(HOME / "biblioteca")))

# ---- Vídeo curto a partir de texto (Wan 2.1, 1,3 bilhão de parâmetros, pelo stable-diffusion.cpp) ----
# Roda na CPU (a placa de 2 GB não comporta); um clipe de ~1 s leva de 15 a 25 minutos. Modelo oficial, sem alterações.
_HF = "https://huggingface.co"
VIDEO_ARQUIVOS = {
    "difusao": {"arquivo": IMG_MODEL.parent / "Wan2.1-T2V-1.3B-Q4_K_M.gguf", "tam": 982716640,
                "url": f"{_HF}/samuelchristlie/Wan2.1-T2V-1.3B-GGUF/resolve/main/Wan2.1-T2V-1.3B-Q4_K_M.gguf"},
    "texto": {"arquivo": IMG_MODEL.parent / "umt5-xxl-encoder-Q4_K_M.gguf", "tam": 3655145312,
              "url": f"{_HF}/city96/umt5-xxl-encoder-gguf/resolve/main/umt5-xxl-encoder-Q4_K_M.gguf"},
    "vae": {"arquivo": IMG_MODEL.parent / "wan_2.1_vae.safetensors", "tam": 253815318,
            "url": f"{_HF}/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors"},
}
VIDEO_FPS = 16
