import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import ambiente  # noqa: F401
import agente
import config
import modelos


def roda(c):
    return asyncio.run(c)


class Comando(unittest.TestCase):
    def test_conversa_normal_nao_vira_comando(self):
        for m in ["oi, tudo bem?", "me explica o que é recursão", "escreve um app de lista de compras",
                  "qual a capital da França?"]:
            self.assertFalse(agente.parece_comando(m), m)

    def test_pedidos_em_varios_jeitos_passam_pelo_filtro(self):
        for m in ["instala o qwen coder 14b", "bota o gemma 3 pra eu testar", "quero testar o deepseek r1",
                  "baixa um modelo llama 3.1 8b da internet", "traz o mistral nemo pra mim", "que modelos eu tenho instalados?"]:
            self.assertTrue(agente.parece_comando(m), m)

    def test_plano_b_sem_ia_acha_o_nome_do_modelo(self):
        h = agente.heuristica("bota o qwen2.5 coder 14b pra eu testar")
        self.assertEqual((h["acao"], h["consulta"]), ("instalar_modelo", "qwen2.5 coder 14b"))
        h = agente.heuristica("por favor instale o llama 3.1 8b da internet")
        self.assertEqual((h["acao"], h["consulta"]), ("instalar_modelo", "llama 3.1 8b"))
        self.assertEqual(agente.heuristica("que modelos eu tenho instalados?")["acao"], "listar_modelos")
        self.assertEqual(agente.heuristica("o que é um modelo llm?")["acao"], "nenhuma")

    def test_sem_llama_server_usa_o_plano_b(self):   # LLAMA_URL aponta para porta fechada
        it = roda(agente.interpreta("instala o gemma 3 4b"))
        self.assertEqual((it["acao"], it["consulta"]), ("instalar_modelo", "gemma 3 4b"))


class _FakeLlama(BaseHTTPRequestHandler):
    resposta = {}

    def do_POST(self):
        n = int(self.headers["Content-Length"])
        self.__class__.recebido = json.loads(self.rfile.read(n))
        corpo = json.dumps({"choices": [{"message": {"content": json.dumps(self.resposta)}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


class ComIA(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), _FakeLlama)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.antes = config.LLAMA_URL
        config.LLAMA_URL = f"http://127.0.0.1:{self.srv.server_port}"
        self.orig = modelos.instalar_e_usar
        self.chamadas = []

        async def falso(pedido, tipo="texto"):
            self.chamadas.append((pedido, tipo))
            return {"estado": "baixando", "repo": "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF", "arquivo": "x.gguf", "quant": "Q4_K_M",
                    "tam": 9000000000, "aviso": "Pesado (9.0 GB): cabe, mas fica lento.", "alternativas": ["a/b"],
                    "aproximado": False, "quant_pedida_ok": True, "id": "abc"}
        modelos.instalar_e_usar = falso

    def tearDown(self):
        self.srv.shutdown()
        config.LLAMA_URL = self.antes
        modelos.instalar_e_usar = self.orig

    def test_a_ia_entende_um_pedido_em_giria(self):
        _FakeLlama.resposta = {"acao": "instalar_modelo", "consulta": "qwen2.5 coder 14b", "tipo": "texto"}
        r = roda(agente.tenta("manda ver no qwen coder de 14 bilhões aí pra eu testar, esse modelo"))
        self.assertEqual(self.chamadas, [("qwen2.5 coder 14b", "texto")])
        self.assertIn("deixo selecionado", r)
        self.assertIn("Pesado", r)                       # o aviso de tamanho chega ao usuário
        self.assertEqual(_FakeLlama.recebido["temperature"], 0)

    def test_a_ia_diz_que_nao_e_comando(self):
        _FakeLlama.resposta = {"acao": "nenhuma", "consulta": "", "tipo": "texto"}
        self.assertIsNone(roda(agente.tenta("você sabe instalar um modelo llm? me explica como funciona")))
        self.assertEqual(self.chamadas, [])

    def test_resposta_torta_da_ia_cai_no_plano_b(self):
        _FakeLlama.resposta = {"acao": "apagar_tudo", "consulta": "x"}   # ação desconhecida nunca é executada
        roda(agente.tenta("instala o gemma 3 4b"))
        self.assertEqual(self.chamadas, [("gemma 3 4b", "texto")])

    def test_mensagem_comum_nem_chama_a_ia(self):
        self.assertIsNone(roda(agente.tenta("bom dia, me ajuda com uma receita")))
        self.assertFalse(hasattr(_FakeLlama, "recebido") and _FakeLlama.recebido.get("max_tokens") == 120 and self.chamadas)


if __name__ == "__main__":
    unittest.main()
