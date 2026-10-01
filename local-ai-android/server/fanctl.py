#!/usr/bin/env python3
"""Controle automático das ventoinhas: aumenta a rotação conforme a temperatura (sob demanda).

GPU NVIDIA: lê a temperatura e ajusta a ventoinha por uma curva. Tenta primeiro pelo NVML
(sem precisar de tela) e, se a placa não aceitar, pelo nvidia-settings (precisa do Coolbits).
CPU (opcional): se a placa-mãe expõe um PWM no Linux, defina FANCTL_CPU_PWM com o caminho
(ex.: /sys/class/hwmon/hwmon3/pwm2). Veja o diagnostico_fans.sh para descobrir.

SEGURANÇA: ao parar (ou se perder a leitura da temperatura) devolve o controle ao automático
da placa. A curva nunca deixa a ventoinha abaixo do mínimo.
Roda como serviço (root): localai-fan.service. Para desligar: sudo systemctl disable --now localai-fan
"""
import glob
import os
import signal
import subprocess
import sys
import time

# (temperatura em °C, ventoinha em %). Entre os pontos, interpola.
CURVA_GPU = [(40, 30), (50, 38), (60, 50), (70, 70), (78, 90), (82, 100)]
CURVA_CPU = [(40, 30), (55, 45), (65, 65), (75, 85), (85, 100)]
INTERVALO = float(os.environ.get("FANCTL_INTERVALO", "2"))
DESCE_POR_CICLO = 2          # a rotação sobe na hora, mas desce devagar (evita "serrote")
FALHAS_PARA_RESTAURAR = 3


def interpola(curva, t):
    if t <= curva[0][0]:
        return curva[0][1]
    for (t0, v0), (t1, v1) in zip(curva, curva[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return curva[-1][1]


def alvo_gpu(temp, uso):
    """Rotação desejada. Se a GPU está muito ocupada, já sobe um pouco antes de esquentar."""
    alvo = interpola(CURVA_GPU, temp)
    if uso is not None and uso >= 50:
        alvo = max(alvo, 45)
    if uso is not None and uso >= 85:
        alvo = max(alvo, 60)
    return int(round(min(100, max(30, alvo))))


class Suavizador:
    def __init__(self):
        self.atual = None

    def passo(self, alvo):
        if self.atual is None or alvo >= self.atual:
            self.atual = alvo
        else:
            self.atual = max(alvo, self.atual - DESCE_POR_CICLO)
        return self.atual


# ----------------------------------------------------------------------------- GPU
def _smi(campo):
    r = subprocess.run(["nvidia-smi", f"--query-gpu={campo}", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, timeout=5)
    return float(r.stdout.strip().splitlines()[0])


class GpuNvidia:
    """Leitura por nvidia-smi; escrita por NVML (preferido) ou nvidia-settings (reserva)."""

    def __init__(self):
        self.metodo = None
        self.nvml = None
        self.h = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml, self.h = pynvml, pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:
            self.nvml = None

    def temp(self):
        return _smi("temperature.gpu")

    def uso(self):
        try:
            return _smi("utilization.gpu")
        except Exception:
            return None

    def _xauth(self):
        for c in glob.glob("/home/*/.Xauthority") + glob.glob("/var/run/lightdm/root/:0"):
            return c
        return None

    def _via_nvml(self, pct):
        self.nvml.nvmlDeviceSetFanSpeed_v2(self.h, 0, int(pct))

    def _via_settings(self, pct):
        env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
        xa = self._xauth()
        if xa:
            env["XAUTHORITY"] = xa
        r = subprocess.run(["nvidia-settings", "-a", "[gpu:0]/GPUFanControlState=1",
                            "-a", f"[fan:0]/GPUTargetFanSpeed={int(pct)}"],
                           capture_output=True, text=True, timeout=10, env=env)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip()[:200])

    def definir(self, pct):
        tentativas = [("nvml", self._via_nvml)] if self.nvml else []
        tentativas.append(("nvidia-settings", self._via_settings))
        if self.metodo:  # já sabemos qual funciona
            tentativas = [t for t in tentativas if t[0] == self.metodo]
        erro = None
        for nome, fn in tentativas:
            try:
                fn(pct)
                if self.metodo != nome:
                    print(f"[fanctl] GPU: controle da ventoinha via {nome}", flush=True)
                self.metodo = nome
                return
            except Exception as e:  # tenta o próximo método
                erro = e
        raise RuntimeError(f"Nenhum método controla a ventoinha da GPU: {erro}")

    def restaurar(self):
        """Devolve a ventoinha ao controle automático da placa."""
        try:
            if self.metodo == "nvml" and self.nvml:
                self.nvml.nvmlDeviceSetDefaultFanSpeed_v2(self.h, 0)
            elif self.metodo == "nvidia-settings":
                env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":0"))
                xa = self._xauth()
                if xa:
                    env["XAUTHORITY"] = xa
                subprocess.run(["nvidia-settings", "-a", "[gpu:0]/GPUFanControlState=0"],
                               capture_output=True, timeout=10, env=env)
        except Exception as e:
            print(f"[fanctl] aviso ao restaurar a GPU: {e}", flush=True)
        self.metodo = None


# ----------------------------------------------------------------------------- CPU (opcional)
def temp_cpu():
    for d in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            nome = open(f"{d}/name").read().strip()
            if nome in ("k10temp", "coretemp", "zenpower"):
                return int(open(f"{d}/temp1_input").read()) / 1000
        except (OSError, ValueError):
            continue
    return None


class CpuPwm:
    """Ventoinha ligada a um PWM da placa-mãe (ex.: /sys/class/hwmon/hwmon3/pwm2)."""

    def __init__(self, pwm):
        self.pwm = pwm
        self.enable = pwm + "_enable"
        self.original = None

    def definir(self, pct):
        if self.original is None:
            self.original = open(self.enable).read().strip()
            open(self.enable, "w").write("1")  # 1 = manual
        open(self.pwm, "w").write(str(int(round(255 * pct / 100))))

    def restaurar(self):
        if self.original is not None:
            try:
                open(self.enable, "w").write(self.original)
            except OSError as e:
                print(f"[fanctl] aviso ao restaurar a CPU: {e}", flush=True)
            self.original = None


# ----------------------------------------------------------------------------- laço principal
class Controlador:
    def __init__(self, gpu, cpu=None, leitura_cpu=temp_cpu):
        self.gpu, self.cpu, self.leitura_cpu = gpu, cpu, leitura_cpu
        self.sg, self.sc = Suavizador(), Suavizador()
        self.falhas = 0
        self.ativo = False

    def ciclo(self):
        """Um ciclo de leitura e ajuste. Devolve (pct_gpu, pct_cpu) aplicados."""
        try:
            t, u = self.gpu.temp(), self.gpu.uso()
            self.falhas = 0
        except Exception:
            self.falhas += 1
            if self.falhas >= FALHAS_PARA_RESTAURAR and self.ativo:
                print("[fanctl] sem leitura da temperatura: devolvendo ao automático", flush=True)
                self.parar()
            return None, None
        pg = self.sg.passo(alvo_gpu(t, u))
        self.gpu.definir(pg)
        self.ativo = True
        pc = None
        if self.cpu:
            tc = self.leitura_cpu()
            if tc is not None:
                pc = self.sc.passo(int(round(min(100, max(30, interpola(CURVA_CPU, tc))))))
                self.cpu.definir(pc)
        return pg, pc

    def parar(self):
        self.gpu.restaurar()
        if self.cpu:
            self.cpu.restaurar()
        self.sg, self.sc = Suavizador(), Suavizador()
        self.ativo = False


def main():
    gpu = GpuNvidia()
    cpu = CpuPwm(os.environ["FANCTL_CPU_PWM"]) if os.environ.get("FANCTL_CPU_PWM") else None
    ctl = Controlador(gpu, cpu)

    def sair(*_):
        ctl.parar()
        print("[fanctl] encerrado; ventoinhas devolvidas ao automático", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, sair)
    signal.signal(signal.SIGINT, sair)
    print(f"[fanctl] iniciado (CPU PWM: {'sim' if cpu else 'não'})", flush=True)
    erros = 0
    try:
        while True:
            try:
                ctl.ciclo()
                erros = 0
            except Exception as e:
                erros += 1
                print(f"[fanctl] erro: {e}", flush=True)
                if erros >= 5:  # algo está errado de forma persistente: não mexe mais
                    print("[fanctl] muitos erros seguidos; saindo e deixando em automático", flush=True)
                    break
            time.sleep(INTERVALO)
    finally:
        ctl.parar()


if __name__ == "__main__":
    main()
