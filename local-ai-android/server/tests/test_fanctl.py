import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fanctl  # noqa: E402


class FakeGpu:
    def __init__(self, t, u):
        self.t, self.u, self.set, self.restaurada = t, u, [], False

    def temp(self): return self.t
    def uso(self): return self.u
    def definir(self, p): self.set.append(p)
    def restaurar(self): self.restaurada = True


class FakeCpu:
    def __init__(self): self.set = []
    def definir(self, p): self.set.append(p)
    def restaurar(self): pass


class Curvas(unittest.TestCase):
    def test_carga_sobe_a_rotacao_antes_de_esquentar(self):
        frio = fanctl.alvo_cpu(40, 5)
        self.assertEqual(frio, 30)
        self.assertGreaterEqual(fanctl.alvo_cpu(40, 70), 50)    # IA gerando: sobe com a CPU ainda fria
        self.assertGreaterEqual(fanctl.alvo_cpu(40, 95), 65)

    def test_calor_manda_mais_que_a_carga(self):
        self.assertEqual(fanctl.alvo_cpu(90, 10), 100)

    def test_nunca_abaixo_do_minimo_nem_acima_de_100(self):
        for t in (-10, 0, 40, 120):
            for c in (None, 0, 100):
                self.assertTrue(30 <= fanctl.alvo_cpu(t, c) <= 100)

    def test_sem_leitura_de_carga_usa_so_a_temperatura(self):
        self.assertEqual(fanctl.alvo_cpu(65, None), fanctl.alvo_cpu(65, 0))


class Laco(unittest.TestCase):
    def test_sobe_na_hora_e_desce_devagar(self):
        gpu, cpu = FakeGpu(40, 5), FakeCpu()
        cargas = iter([5, 95, 5, 5, 5])
        c = fanctl.Controlador(gpu, cpu, leitura_cpu=lambda: 40, leitura_carga=lambda: next(cargas))
        c.ciclo()
        base = cpu.set[-1]
        c.ciclo()
        pico = cpu.set[-1]
        self.assertGreater(pico, base)                           # subiu de uma vez
        c.ciclo()
        self.assertEqual(cpu.set[-1], pico - fanctl.DESCE_POR_CICLO)  # e desce aos poucos

    def test_sem_temperatura_devolve_ao_automatico(self):
        class Quebrada(FakeGpu):
            def temp(self): raise OSError("sem leitura")
        gpu = Quebrada(50, 50)
        c = fanctl.Controlador(gpu)
        c.ativo = True
        for _ in range(fanctl.FALHAS_PARA_RESTAURAR):
            c.ciclo()
        self.assertTrue(gpu.restaurada)


class Deteccao(unittest.TestCase):
    def _hw(self, raiz, nome, canais):
        d = os.path.join(raiz, nome)
        os.makedirs(d)
        for n, (rpm, rotulo) in canais.items():
            open(f"{d}/pwm{n}", "w").write("100")
            open(f"{d}/pwm{n}_enable", "w").write("5")
            open(f"{d}/fan{n}_input", "w").write(str(rpm))
            if rotulo:
                open(f"{d}/fan{n}_label", "w").write(rotulo + "\n")
        return d

    def test_prefere_o_canal_rotulado_cpu(self):
        with tempfile.TemporaryDirectory() as r:
            d = self._hw(r, "hwmon0", {1: (900, "chassis"), 2: (1200, "CPU Fan")})
            self.assertEqual(fanctl.acha_pwm_cpu(r), f"{d}/pwm2")

    def test_sem_rotulo_pega_o_primeiro_girando(self):
        with tempfile.TemporaryDirectory() as r:
            d = self._hw(r, "hwmon0", {1: (0, ""), 2: (800, "")})
            self.assertEqual(fanctl.acha_pwm_cpu(r), f"{d}/pwm2")

    def test_sem_pwm_nao_inventa(self):
        with tempfile.TemporaryDirectory() as r:
            os.makedirs(f"{r}/hwmon0")
            self.assertIsNone(fanctl.acha_pwm_cpu(r))


if __name__ == "__main__":
    unittest.main()
