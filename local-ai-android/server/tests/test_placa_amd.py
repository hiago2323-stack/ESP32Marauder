import os
import sys
import tempfile
import unittest
from pathlib import Path

import ambiente  # noqa: F401
import fanctl
import placa
import recursos
import telemetria


def monta(raiz: Path, card="card1", vendor="0x1002", vram_total=8 * 1024**3, usada=1 * 1024**3, hwmon=True, pwm=True):
    dev = raiz / card / "device"
    dev.mkdir(parents=True)
    (dev / "vendor").write_text(vendor + "\n")
    (dev / "mem_info_vram_total").write_text(str(vram_total))
    (dev / "mem_info_vram_used").write_text(str(usada))
    (dev / "gpu_busy_percent").write_text("37\n")
    (dev / "product_name").write_text("Radeon RX 580 Series\n")
    if hwmon:
        hw = dev / "hwmon" / "hwmon7"
        hw.mkdir(parents=True)
        (hw / "name").write_text("amdgpu\n")
        (hw / "temp1_input").write_text("61000\n")
        (hw / "power1_average").write_text("85000000\n")
        (hw / "fan1_input").write_text("1450\n")
        if pwm:
            (hw / "pwm1").write_text("102\n")
            (hw / "pwm1_enable").write_text("2\n")
            (hw / "pwm1_max").write_text("255\n")
            (hw / "pwm1_min").write_text("0\n")
    return dev


class Amd(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.raiz = Path(self.t.name)

    def tearDown(self):
        self.t.cleanup()

    def test_le_a_rx_580(self):
        monta(self.raiz)
        p = placa.acha_amd(str(self.raiz))
        r = p.resumo()
        self.assertEqual((r["nome"], r["temp"], r["uso"], r["mem_usada"], r["mem_total"], r["watts"]),
                         ("Radeon RX 580 Series", 61.0, 37.0, 1024, 8192, 85.0))
        self.assertEqual(r["ventoinha"], 40.0)                    # 102/255
        self.assertEqual(p.vram_livre_mb(), 7168)

    def test_prefere_a_placa_dedicada_a_grafica_integrada_do_ryzen(self):
        monta(self.raiz, "card0", vram_total=512 * 1024**2, usada=100 * 1024**2)    # iGPU
        monta(self.raiz, "card1")                                                   # RX 580
        self.assertEqual(placa.acha_amd(str(self.raiz)).dev.parent.name, "card1")

    def test_ignora_nvidia_e_conectores(self):
        monta(self.raiz, "card0", vendor="0x10de")
        (self.raiz / "card0-HDMI-A-1").mkdir()
        self.assertIsNone(placa.acha_amd(str(self.raiz)))

    def test_ventoinha_manual_e_devolucao_ao_automatico(self):
        dev = monta(self.raiz)
        p = placa.acha_amd(str(self.raiz))
        hw = dev / "hwmon" / "hwmon7"
        p.definir(70)
        self.assertEqual((hw / "pwm1_enable").read_text().strip(), "1")
        self.assertEqual((hw / "pwm1").read_text().strip(), str(round(255 * 0.7)))
        self.assertEqual(p.metodo, "amdgpu")
        p.definir(150)                                                      # nunca passa de 100%
        self.assertEqual((hw / "pwm1").read_text().strip(), "255")
        p.restaurar()
        self.assertEqual((hw / "pwm1_enable").read_text().strip(), "2")

    def test_restaurar_sem_ter_mexido_nao_escreve_nada(self):
        dev = monta(self.raiz)
        placa.acha_amd(str(self.raiz)).restaurar()
        self.assertEqual((dev / "hwmon" / "hwmon7" / "pwm1_enable").read_text().strip(), "2")

    def test_placa_sem_controle_de_ventoinha_diz_o_motivo(self):
        monta(self.raiz, pwm=False)
        p = placa.acha_amd(str(self.raiz))
        with self.assertRaises(RuntimeError):
            p.definir(50)
        self.assertIn("pwm1", p.erro)

    def test_controlador_sobe_a_rotacao_da_amd_quando_a_gpu_esquenta(self):
        dev = monta(self.raiz)
        hw = dev / "hwmon" / "hwmon7"
        c = fanctl.Controlador(placa.acha_amd(str(self.raiz)), [], leitura_cpu=lambda: 40, uso_cpu=type("U", (), {"ler": lambda s: 5.0})())
        c.ciclo()
        frio = int((hw / "pwm1").read_text())
        (hw / "temp1_input").write_text("78000\n")
        c.ciclo()
        self.assertGreater(int((hw / "pwm1").read_text()), frio)
        self.assertEqual(int((hw / "pwm1").read_text()), 255)               # 78 °C: 100%

    def test_varredura_da_placa_mae_nao_pega_a_ventoinha_da_gpu(self):
        monta(self.raiz)
        mb = self.raiz / "hwmon" / "hwmon3"
        mb.mkdir(parents=True)
        (mb / "name").write_text("nct6795\n")
        for n, v in (("pwm2", "128"), ("pwm2_enable", "5"), ("fan2_input", "900")):
            (mb / n).write_text(v)
        # amdgpu também aparece em /sys/class/hwmon
        g = self.raiz / "hwmon" / "hwmon7"
        g.mkdir()
        for n, v in (("name", "amdgpu"), ("pwm1", "100"), ("pwm1_enable", "2"), ("fan1_input", "1450")):
            (g / n).write_text(v)
        antes = fanctl.HWMON
        fanctl.HWMON = str(self.raiz / "hwmon")
        try:
            achados = [os.path.basename(os.path.dirname(p)) for p, _ in fanctl.candidatos()]
        finally:
            fanctl.HWMON = antes
        self.assertEqual(achados, ["hwmon3"])

    def test_vram_livre_do_servidor_enxerga_a_amd(self):
        monta(self.raiz)
        orig, orig_smi = placa.RAIZ, recursos.subprocess.run
        placa.acha_amd.__defaults__ = (str(self.raiz),)
        recursos.subprocess.run = lambda *a, **k: (_ for _ in ()).throw(OSError("sem nvidia-smi"))
        try:
            self.assertEqual(recursos.vram_livre_mb(), 7168)
            self.assertEqual(recursos.modo_imagem(), "gpu")                # 7 GB livres: tudo na placa
            g = telemetria._gpu()
            self.assertEqual((g["nome"], g["mem_total"]), ("Radeon RX 580 Series", 8192))
        finally:
            placa.acha_amd.__defaults__ = (orig,)
            recursos.subprocess.run = orig_smi


if __name__ == "__main__":
    unittest.main()
