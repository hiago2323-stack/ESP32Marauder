import asyncio
import io
import os
import stat
import sys
import unittest
from pathlib import Path

import ambiente  # noqa: F401
import config
import perfil
import visao

FAKE_LLAMA = """#!{py}
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
a = sys.argv
porta = int(a[a.index("--port") + 1])
assert "--mmproj" in a and "-ngl" in a and a[a.index("-ngl") + 1] == "0", a    # visão na CPU, com o projetor
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"{{}}")
    def do_POST(self):
        d = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        partes = d["messages"][0]["content"]
        img = [p for p in partes if p["type"] == "image_url"][0]["image_url"]["url"]
        ok = img.startswith("data:image/jpeg;base64,")
        txt = "Homem de cabelo curto e barba, de óculos." if ok else "SEM IMAGEM"
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({{"choices": [{{"message": {{"content": txt}}}}]}}).encode())
    def log_message(self, *a): pass
HTTPServer(("127.0.0.1", porta), H).serve_forever()
"""


def foto_png(w=2000, h=1500) -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (w, h), (120, 90, 60)).save(b, "PNG")
    return b.getvalue()


class Perfil(unittest.TestCase):
    def setUp(self):
        perfil.apaga()
        pasta = config.MODELS_DIR / "visao"
        pasta.mkdir(parents=True, exist_ok=True)
        for n in config.VISAO_ARQUIVOS:
            (pasta / n).write_bytes(b"x")
        fake = Path(ambiente.TMP) / "llama-server"
        fake.write_text(FAKE_LLAMA.format(py=sys.executable))
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        config.LLAMA_SERVER_BIN = fake

    def test_foto_vira_descricao_e_entra_no_prompt(self):
        async def fluxo():
            perfil.define_nome("Hiago")
            perfil.salva_foto(foto_png(), ".png")
            perfil.analisar()
            await asyncio.wait_for(perfil._tarefa, 90)
        asyncio.run(fluxo())
        e = perfil.estado()
        self.assertEqual(e["estado"], "pronto", e)
        self.assertIn("barba", e["descricao"])
        ctx = perfil.contexto()
        self.assertIn("Hiago", ctx)
        self.assertIn("barba", ctx)

    def test_modelo_de_visao_e_desligado_depois(self):
        async def fluxo():
            await visao.descreve(Path(ambiente.TMP) / "f.png") if False else None
            p = Path(ambiente.TMP) / "f.png"
            p.write_bytes(foto_png(300, 200))
            return await visao.descreve(p)
        self.assertIn("barba", asyncio.run(fluxo()))
        import socket
        with self.assertRaises(OSError):                  # a porta voltou a ficar livre: processo encerrado
            socket.create_connection(("127.0.0.1", config.VISAO_PORTA), timeout=1)

    def test_formato_invalido_e_foto_gigante_sao_recusados(self):
        with self.assertRaises(ValueError):
            perfil.salva_foto(b"x", ".exe")
        with self.assertRaises(ValueError):
            perfil.salva_foto(b"x" * (26 * 1024 * 1024), ".jpg")

    def test_apagar_remove_foto_nome_e_descricao(self):
        perfil.define_nome("Hiago")
        perfil.salva_foto(foto_png(50, 50), ".png")
        f = perfil.caminho_foto()
        perfil.apaga()
        self.assertFalse(f.exists())
        self.assertEqual(perfil.contexto(), "")

    def test_sem_foto_e_sem_nome_nao_polui_o_prompt(self):
        self.assertEqual(perfil.contexto(), "")

    def test_modelo_de_visao_ausente_da_erro_claro(self):
        for n in config.VISAO_ARQUIVOS:
            (config.MODELS_DIR / "visao" / n).unlink()
        with self.assertRaises(RuntimeError) as c:
            asyncio.run(visao.descreve(Path("x.png")))
        self.assertIn("não foi baixado", str(c.exception))


if __name__ == "__main__":
    unittest.main()
