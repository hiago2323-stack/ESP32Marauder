"""Isola os testes: HOME temporário (modelos, perfil, memória) e servidor sem rede de verdade. Importe antes do resto."""
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="localai-teste-")
os.environ["HOME"] = TMP
os.environ["LLAMA_URL"] = "http://127.0.0.1:1"        # porta fechada: simula o llama-server desligado
os.environ["VISAO_PORTA"] = "18082"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
