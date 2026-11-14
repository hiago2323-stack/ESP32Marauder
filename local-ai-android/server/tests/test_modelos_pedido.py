import asyncio
import os
import stat
import unittest
from pathlib import Path

import ambiente  # noqa: F401  (precisa vir antes dos módulos do servidor)
import config
import modelos


def roda(c):
    return asyncio.run(c)


REPOS = [
    {"repo": "someone/Qwen2.5-Coder-7B-Instruct-GGUF-weird", "downloads": 5, "likes": 0, "tipo": "", "tags": []},
    {"repo": "bartowski/Qwen2.5-Coder-14B-Instruct-GGUF", "downloads": 90000, "likes": 1, "tipo": "", "tags": []},
    {"repo": "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF", "downloads": 150000, "likes": 1, "tipo": "", "tags": []},
    {"repo": "x/qwen2.5-coder-3b-gguf", "downloads": 999999, "likes": 1, "tipo": "", "tags": []},
]


class Pedido(unittest.TestCase):
    def test_separa_quantizacao(self):
        self.assertEqual(modelos.separa_pedido("qwen2.5 coder 14b q5_k_m"), ("qwen2.5 coder 14b", "Q5_K_M"))
        self.assertEqual(modelos.separa_pedido("gemma 3 4b"), ("gemma 3 4b", ""))
        self.assertEqual(modelos.separa_pedido("Qwen/Qwen2.5-7B-Instruct-GGUF:Q8_0"), ("Qwen/Qwen2.5-7B-Instruct-GGUF", "Q8_0"))

    def test_tamanho_do_modelo_nao_vira_quantizacao(self):
        self.assertEqual(modelos.separa_pedido("llama 3.1 8b")[1], "")
        self.assertEqual(modelos.separa_pedido("phi 4 mini")[1], "")

    def test_escolhe_o_que_tem_todas_as_palavras_e_mais_downloads(self):
        r, aprox = modelos.escolhe_repo(REPOS, "qwen2.5 coder 14b")
        self.assertEqual(r["repo"], "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF")   # não o de 3b, mesmo com mais downloads
        self.assertFalse(aprox)

    def test_sem_correspondencia_exata_avisa_que_e_aproximado(self):
        r, aprox = modelos.escolhe_repo(REPOS, "qwen2.5 coder 32b")
        self.assertTrue(aprox)
        self.assertIn("qwen2.5-coder", r["repo"].lower())


class _Resp:
    status_code = 200

    def __init__(self, siblings): self._s = siblings
    def json(self): return {"siblings": self._s}


class _Cliente:
    def __init__(self, siblings): self._s = siblings
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, *a, **k): return _Resp(self._s)


class ArquivosDuplicados(unittest.TestCase):
    def test_inteiro_e_dividido_na_mesma_quantizacao_nao_soma_dobrado(self):
        """Repositórios como o da Qwen publicam q4_k_m inteiro E em partes: vale só um (senão baixaria o dobro)."""
        sib = [{"rfilename": "m-q4_k_m.gguf", "size": 4_680_000_000},
               {"rfilename": "m-q4_k_m-00001-of-00002.gguf", "size": 3_900_000_000},
               {"rfilename": "m-q4_k_m-00002-of-00002.gguf", "size": 780_000_000},
               {"rfilename": "m-q8_0-00001-of-00002.gguf", "size": 4_000_000_000},
               {"rfilename": "m-q8_0-00002-of-00002.gguf", "size": 4_100_000_000}]
        orig = modelos.httpx.AsyncClient
        modelos.httpx.AsyncClient = lambda **k: _Cliente(sib)
        try:
            itens = {g["quant"]: g for g in roda(modelos.arquivos("a/b"))}
        finally:
            modelos.httpx.AsyncClient = orig
        self.assertEqual(itens["Q4_K_M"]["arquivos"], ["m-q4_k_m.gguf"])
        self.assertEqual(itens["Q4_K_M"]["tam"], 4_680_000_000)
        self.assertEqual(len(itens["Q8_0"]["arquivos"]), 2)               # só dividido: soma as partes
        self.assertEqual(itens["Q8_0"]["tam"], 8_100_000_000)


class FluxoCompleto(unittest.TestCase):
    """achar -> escolher arquivo -> baixar (curl falso) -> registrar -> selecionar, tudo em um pedido."""

    def setUp(self):
        self.bin = Path(ambiente.TMP) / "bin"
        self.bin.mkdir(exist_ok=True)
        curl = self.bin / "curl"
        curl.write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\nhead -c 4096 /dev/zero > "$out"\n')
        curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
        self.path = os.environ["PATH"]
        os.environ["PATH"] = f"{self.bin}:{self.path}"
        self.orig = (modelos.buscar, modelos.arquivos, modelos.usar)
        self.usados = []

        async def buscar(q, tipo="texto", limite=20):
            return REPOS

        async def arquivos(repo, tipo="texto"):
            return [{"nome": "m-Q4_K_M.gguf", "arquivos": ["m-Q4_K_M.gguf"], "tam": 4096, "quant": "Q4_K_M", "aviso": "", "recomendado": True},
                    {"nome": "m-Q8_0.gguf", "arquivos": ["m-Q8_0.gguf"], "tam": 8192, "quant": "Q8_0", "aviso": "Pesado", "recomendado": False}]

        modelos.buscar, modelos.arquivos = buscar, arquivos
        modelos.usar = lambda id_: self.usados.append(id_)

    def tearDown(self):
        os.environ["PATH"] = self.path
        modelos.buscar, modelos.arquivos, modelos.usar = self.orig
        modelos._baixando.clear()
        for f in config.MODELS_DIR.glob("*.gguf*"):
            f.unlink()
        modelos.REGISTRO.unlink(missing_ok=True)

    async def _fluxo(self, pedido):
        r = await modelos.instalar_e_usar(pedido)
        job = modelos._baixando[r["id"]]
        await asyncio.wait_for(job["task"], 10)
        return r, job

    def test_baixa_registra_e_seleciona(self):
        r, job = roda(self._fluxo("qwen2.5 coder 14b"))
        self.assertEqual(r["estado"], "baixando")
        self.assertEqual(r["repo"], "Qwen/Qwen2.5-Coder-14B-Instruct-GGUF")
        self.assertEqual(r["quant"], "Q4_K_M")                       # a recomendada
        self.assertEqual(job["erro"], "")
        self.assertTrue(job["selecionado"])
        self.assertEqual(self.usados, [r["id"]])                     # ficou selecionado ao terminar
        self.assertTrue((config.MODELS_DIR / "m-Q4_K_M.gguf").exists())
        self.assertEqual([m["id"] for m in modelos.lista()], [r["id"]])

    def test_respeita_a_quantizacao_pedida(self):
        r, _ = roda(self._fluxo("qwen2.5 coder 14b q8_0"))
        self.assertEqual(r["quant"], "Q8_0")
        self.assertTrue(r["quant_pedida_ok"])

    def test_segundo_pedido_do_mesmo_modelo_so_seleciona(self):
        roda(self._fluxo("qwen2.5 coder 14b"))
        self.usados.clear()
        r = roda(modelos.instalar_e_usar("qwen2.5 coder 14b"))
        self.assertEqual(r["estado"], "pronto")
        self.assertEqual(len(self.usados), 1)

    def test_acha_instalado_pelo_nome(self):
        roda(self._fluxo("qwen2.5 coder 14b"))
        self.assertIsNotNone(modelos.acha_instalado("qwen coder 14b"))
        self.assertIsNone(modelos.acha_instalado("gemma 3 4b"))

    def test_download_que_falha_nao_registra_nem_seleciona(self):
        (self.bin / "curl").write_text("#!/bin/sh\nexit 22\n")
        r, job = roda(self._fluxo("qwen2.5 coder 14b"))
        self.assertTrue(job["erro"])
        self.assertEqual(self.usados, [])
        self.assertEqual(modelos.lista(), [])


if __name__ == "__main__":
    unittest.main()
