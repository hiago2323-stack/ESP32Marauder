import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

import ambiente  # noqa: F401
import aprendizado
import config
import memory


def roda(c):
    return asyncio.run(c)


class _Llama(BaseHTTPRequestHandler):
    fatos = []
    chamadas = 0

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        type(self).chamadas += 1
        corpo = json.dumps({"choices": [{"message": {"content": json.dumps({"fatos": type(self).fatos})}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(corpo)

    def log_message(self, *a):
        pass


class Aprende(unittest.TestCase):
    def setUp(self):
        self.srv = HTTPServer(("127.0.0.1", 0), _Llama)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.antes = config.LLAMA_URL
        config.LLAMA_URL = f"http://127.0.0.1:{self.srv.server_port}"
        _Llama.chamadas = 0
        aprendizado.ativos = 0
        for m in memory.list_all(1000):
            memory.delete(m["id"])

    def tearDown(self):
        self.srv.shutdown()
        config.LLAMA_URL = self.antes

    def guardados(self):
        return [m for m in memory.list_all(1000) if m["source"] == aprendizado.FONTE]

    def test_guarda_fato_duravel_dito_pelo_usuario(self):
        _Llama.fatos = ["O usuário tem um PC com RX 580 de 8 GB e usa Linux Mint."]
        r = roda(aprendizado.aprende("meu PC tem uma RX 580 de 8 GB e eu uso Linux Mint"))
        self.assertEqual(len(r), 1)
        self.assertEqual(len(self.guardados()), 1)

    def test_descarta_invencao_do_modelo(self):
        _Llama.fatos = ["O usuário é médico cardiologista em São Paulo."]
        self.assertEqual(roda(aprendizado.aprende("me explica como funciona uma lista ligada em Python")), [])
        self.assertEqual(self.guardados(), [])

    def test_nunca_guarda_segredos_e_nem_chama_o_modelo(self):
        _Llama.fatos = ["O usuário usa a senha 123456 no roteador."]
        for fala in ["minha senha do roteador é 123456 e meu PC é um Ryzen", "meu cartão é 4111 1111 1111 1111 no Ryzen 5",
                     "meu token sk-abcdefghijklmnop2345 do projeto Betina", "meu cpf 123.456.789-09 e uso Linux Mint no PC"]:
            self.assertEqual(roda(aprendizado.aprende(fala)), [], fala)
        self.assertEqual(_Llama.chamadas, 0)                      # nem chegou a perguntar ao modelo
        self.assertEqual(self.guardados(), [])

    def test_fato_sensivel_devolvido_pelo_modelo_e_barrado(self):
        _Llama.fatos = ["O usuário toma remédio para ansiedade e usa Linux Mint no PC."]
        self.assertEqual(roda(aprendizado.aprende("eu uso Linux Mint no PC e tomo remédio para ansiedade")), [])

    def test_nao_disputa_o_modelo_com_uma_conversa_em_andamento(self):
        _Llama.fatos = ["O usuário tem um PC com RX 580 de 8 GB e usa Linux Mint."]
        aprendizado.ativos = 1
        self.assertEqual(roda(aprendizado.aprende("meu PC tem uma RX 580 de 8 GB e eu uso Linux Mint")), [])
        self.assertEqual(_Llama.chamadas, 0)

    def test_nao_repete_o_que_ja_sabe(self):
        _Llama.fatos = ["O usuário tem um PC com RX 580 de 8 GB e usa Linux Mint."]
        fala = "meu PC tem uma RX 580 de 8 GB e eu uso Linux Mint"
        roda(aprendizado.aprende(fala))
        self.assertEqual(roda(aprendizado.aprende(fala)), [])
        self.assertEqual(len(self.guardados()), 1)

    def test_mensagem_curta_e_modelo_desligado_nao_quebram(self):
        self.assertEqual(roda(aprendizado.aprende("oi")), [])
        config.LLAMA_URL = "http://127.0.0.1:1"
        self.assertEqual(roda(aprendizado.aprende("meu PC tem uma RX 580 de 8 GB e eu uso Linux Mint")), [])

    def test_limite_de_guardados_apaga_os_mais_antigos(self):
        antes = aprendizado.LIMITE_GUARDADOS
        aprendizado.LIMITE_GUARDADOS = 3
        try:
            for i in range(5):
                memory.add(f"O usuário gosta do número {i} e de café forte", aprendizado.FONTE)
            memory.add("Isto foi ensinado à mão e não pode sumir", "usuario")
            aprendizado._poda()
        finally:
            aprendizado.LIMITE_GUARDADOS = antes
        self.assertEqual(len(self.guardados()), 3)
        self.assertEqual(len([m for m in memory.list_all(1000) if m["source"] == "usuario"]), 1)


if __name__ == "__main__":
    unittest.main()
