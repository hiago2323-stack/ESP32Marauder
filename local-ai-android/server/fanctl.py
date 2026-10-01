#!/usr/bin/env python3
"""Controle automático das ventoinhas: sobe a rotação sob demanda (pelo USO e pela temperatura).

GPU NVIDIA: lê temperatura e uso e ajusta a ventoinha por uma curva. Tenta primeiro o NVML (sem precisar de
tela) e, se a placa não aceitar, o nvidia-settings (precisa do Coolbits e de uma sessão gráfica aberta).
PLACA-MÃE (processador e caixa): descobre sozinho quais saídas PWM do chip da placa-mãe (nct6775, it87...) mexem
numa ventoinha de verdade, com um teste rápido na primeira vez, e passa a controlar todas pela temperatura/uso
da CPU e da GPU. O resultado do teste fica guardado. Para forçar uma só saída: FANCTL_CPU_PWM=/sys/.../pwm2.
Estado em tempo real (para a tela do app): /run/localai-fan.json

SEGURANÇA: ao parar (ou se perder a leitura da temperatura) devolve o controle ao automático. A curva nunca
deixa a ventoinha abaixo do mínimo; com a temperatura alta ela vai a 100%.
Roda como serviço (root): localai-fan.service. Para desligar: sudo systemctl disable --now localai-fan
"""
import glob
import json
import os
import re
import signal
import subprocess
import sys
import time

# (temperatura em °C, ventoinha em %). Entre os pontos, interpola.
CURVA_GPU = [(35, 30), (45, 42), (55, 58), (62, 72), (70, 88), (76, 100)]
CURVA_CPU = [(35, 30), (45, 42), (55, 58), (62, 72), (70, 88), (76, 100)]
INTERVALO = float(os.environ.get("FANCTL_INTERVALO", "1.5"))
DESCE_POR_CICLO = 2          # a rotação sobe na hora, mas desce devagar (evita "serrote")
FALHAS_PARA_RESTAURAR = 3
HWMON = os.environ.get("FANCTL_HWMON", "/sys/class/hwmon")
STATUS = os.environ.get("FANCTL_STATUS", "/run/localai-fan.json")
ESTADO = os.environ.get("FANCTL_ESTADO", "/var/lib/localai-fan/mapa.json")


def interpola(curva, t):
    if t <= curva[0][0]:
        return curva[0][1]
    for (t0, v0), (t1, v1) in zip(curva, curva[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return curva[-1][1]


def alvo_gpu(temp, uso):
    """Rotação desejada. Se a GPU está ocupada, sobe ANTES de esquentar (a geração de imagens esquenta rápido)."""
    alvo = interpola(CURVA_GPU, temp)
    if uso is not None:
        if uso >= 30:
            alvo = max(alvo, 50)
        if uso >= 60:
            alvo = max(alvo, 68)
        if uso >= 85:
            alvo = max(alvo, 85)
    return int(round(min(100, max(30, alvo))))


def alvo_cpu(temp, uso):
    """Idem para o processador: o uso alto sobe a rotação antes de a temperatura reagir."""
    alvo = interpola(CURVA_CPU, temp) if temp is not None else 45
    if uso is not None:
        if uso >= 40:
            alvo = max(alvo, 50)
        if uso >= 70:
            alvo = max(alvo, 68)
        if uso >= 90:
            alvo = max(alvo, 85)
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


# ----------------------------------------------------------------------------- leituras
def _ler(caminho):
    with open(caminho) as f:
        return f.read().strip()


def temp_cpu():
    for d in sorted(glob.glob(f"{HWMON}/hwmon*")):
        try:
            nome = _ler(f"{d}/name")
            if nome in ("k10temp", "coretemp", "zenpower"):
                melhor = None
                for t in sorted(glob.glob(f"{d}/temp*_input")):
                    rot = ""
                    try:
                        rot = _ler(t.replace("_input", "_label"))
                    except OSError:
                        pass
                    v = int(_ler(t)) / 1000
                    if rot in ("Tctl", "Tdie", "Package id 0"):
                        return v
                    melhor = v if melhor is None else melhor
                return melhor
        except (OSError, ValueError):
            continue
    return None


class UsoCpu:
    """Uso total da CPU em % entre uma leitura e a seguinte (/proc/stat)."""

    def __init__(self):
        self.ant = None

    def ler(self):
        try:
            v = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        except (OSError, ValueError):
            return None
        total, ocioso = sum(v), v[3] + (v[4] if len(v) > 4 else 0)
        ant, self.ant = self.ant, (total, ocioso)
        if not ant or total <= ant[0]:
            return None
        return max(0.0, min(100.0, 100.0 * (1 - (ocioso - ant[1]) / (total - ant[0]))))


# ----------------------------------------------------------------------------- GPU
def _smi(campo):
    r = subprocess.run(["nvidia-smi", f"--query-gpu={campo}", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, timeout=5)
    return float(r.stdout.strip().splitlines()[0])


class GpuNvidia:
    """Leitura por nvidia-smi; escrita por NVML (preferido) ou nvidia-settings (reserva)."""

    def __init__(self):
        self.metodo = None
        self.erro = ""
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
                self.metodo, self.erro = nome, ""
                return
            except Exception as e:  # tenta o próximo método
                erro = e
        self.metodo = None
        self.erro = str(erro)[:160]
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


# ----------------------------------------------------------------------------- placa-mãe (CPU e caixa)
class Pwm:
    """Uma saída PWM do chip da placa-mãe ligada a uma ventoinha (ex.: /sys/class/hwmon/hwmon3/pwm2)."""

    def __init__(self, pwm, fan=None):
        self.pwm = pwm
        self.fan = fan
        self.enable = pwm + "_enable"
        self.original = None
        self.pct = None

    def rpm(self):
        try:
            return int(_ler(self.fan)) if self.fan else None
        except (OSError, ValueError):
            return None

    def definir(self, pct):
        if self.original is None:
            self.original = _ler(self.enable)
            with open(self.enable, "w") as f:
                f.write("1")  # 1 = manual
        with open(self.pwm, "w") as f:
            f.write(str(int(round(255 * pct / 100))))
        self.pct = pct

    def restaurar(self):
        if self.original is not None:
            try:
                with open(self.enable, "w") as f:
                    f.write(self.original)
            except OSError as e:
                print(f"[fanctl] aviso ao restaurar {self.pwm}: {e}", flush=True)
            self.original = None
            self.pct = None


def candidatos():
    """Todas as saídas PWM (com ventoinha e modo manual disponíveis) dos chips da placa-mãe."""
    saida = []
    for d in sorted(glob.glob(f"{HWMON}/hwmon*")):
        for p in sorted(glob.glob(f"{d}/pwm[0-9]*")):
            m = re.search(r"/pwm(\d+)$", p)
            if not m:
                continue
            fan = f"{d}/fan{m.group(1)}_input"
            if os.path.exists(p + "_enable") and os.path.exists(fan):
                saida.append((p, fan))
    return saida


def testa_resposta(pwm, fan, espera=float(os.environ.get("FANCTL_ESPERA_TESTE", "5"))):
    """Como o pwmconfig: pula para 100% e depois ~40% e vê se a rotação acompanha. Sempre restaura o modo original."""
    p = Pwm(pwm, fan)
    try:
        p.definir(100)
        time.sleep(espera)
        alto = p.rpm() or 0
        p.definir(40)
        time.sleep(espera)
        baixo = p.rpm() or 0
        return alto > 0 and alto - baixo >= 150
    except (OSError, ValueError):
        return False
    finally:
        p.restaurar()


def descobre_pwms():
    """Devolve as saídas PWM que realmente mexem numa ventoinha (usa o resultado guardado se houver)."""
    forcado = os.environ.get("FANCTL_CPU_PWM")
    if forcado:
        return [Pwm(forcado, re.sub(r"pwm(\d+)$", r"fan\1_input", forcado))]
    cands = candidatos()
    if not cands or os.environ.get("FANCTL_AUTO_PWM", "1") == "0":
        return []
    try:
        guardado = json.load(open(ESTADO))
    except (OSError, ValueError):
        guardado = None
    chave = sorted(p for p, _ in cands)
    if guardado and guardado.get("candidatos") == chave:
        return [Pwm(p, f) for p, f in cands if p in guardado.get("ativos", [])]
    t = temp_cpu()
    if t is not None and t > 70:
        return []   # CPU quente: não arrisca deixar nenhuma ventoinha baixar num teste agora
    print(f"[fanctl] testando {len(cands)} saída(s) PWM da placa-mãe (as ventoinhas vão acelerar e desacelerar um pouco)…", flush=True)
    ativos = [(p, f) for p, f in cands if testa_resposta(p, f)]
    try:
        os.makedirs(os.path.dirname(ESTADO), exist_ok=True)
        json.dump({"candidatos": chave, "ativos": [p for p, _ in ativos]}, open(ESTADO, "w"))
    except OSError:
        pass
    print(f"[fanctl] ventoinhas da placa-mãe que respondem: {len(ativos)} de {len(cands)}", flush=True)
    return [Pwm(p, f) for p, f in ativos]


# ----------------------------------------------------------------------------- laço principal
class Controlador:
    def __init__(self, gpu, pwms=None, leitura_cpu=temp_cpu, uso_cpu=None):
        self.gpu, self.pwms, self.leitura_cpu = gpu, pwms or [], leitura_cpu
        self.uso = uso_cpu or UsoCpu()
        self.sg, self.sc = Suavizador(), Suavizador()
        self.falhas = 0
        self.ativo = False
        self.estado = {}

    def ciclo(self):
        """Um ciclo de leitura e ajuste. Devolve (pct_gpu, pct_placa_mae)."""
        t = u = None
        try:
            t, u = self.gpu.temp(), self.gpu.uso()
            self.falhas = 0
        except Exception:
            self.falhas += 1
            if self.falhas >= FALHAS_PARA_RESTAURAR and self.ativo:
                print("[fanctl] sem leitura da temperatura da GPU: devolvendo ao automático", flush=True)
                self.gpu.restaurar()
        pg = None
        if t is not None:
            pg = self.sg.passo(alvo_gpu(t, u))
            try:
                self.gpu.definir(pg)
                self.ativo = True
            except Exception as e:
                print(f"[fanctl] GPU: {e}", flush=True)
        tc, uc = self.leitura_cpu(), self.uso.ler()
        pc = None
        if self.pwms:
            alvo = alvo_cpu(tc, uc)
            if t is not None:  # a caixa também ajuda a GPU: acompanha a curva dela, um pouco mais calma
                alvo = max(alvo, int(alvo_gpu(t, u) * 0.85))
            pc = self.sc.passo(alvo)
            for p in self.pwms:
                try:
                    p.definir(pc)
                    self.ativo = True
                except OSError as e:
                    print(f"[fanctl] {p.pwm}: {e}", flush=True)
        self.estado = {
            "hora": int(time.time()),
            "gpu": {"temp": t, "uso": u, "pct": pg, "metodo": self.gpu.metodo, "erro": self.gpu.erro},
            "cpu": {"temp": tc, "uso": None if uc is None else round(uc, 1)},
            "placa_mae": [{"pwm": os.path.basename(p.pwm), "chip": os.path.basename(os.path.dirname(p.pwm)),
                           "pct": p.pct, "rpm": p.rpm()} for p in self.pwms],
        }
        return pg, pc

    def parar(self):
        self.gpu.restaurar()
        for p in self.pwms:
            p.restaurar()
        self.sg, self.sc = Suavizador(), Suavizador()
        self.ativo = False


def grava_status(estado):
    try:
        tmp = STATUS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(estado, f)
        os.chmod(tmp, 0o644)
        os.replace(tmp, STATUS)
    except OSError:
        pass


def main():
    gpu = GpuNvidia()
    pwms = descobre_pwms()
    ctl = Controlador(gpu, pwms)

    def sair(*_):
        ctl.parar()
        grava_status({"hora": int(time.time()), "parado": True})
        print("[fanctl] encerrado; ventoinhas devolvidas ao automático", flush=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, sair)
    signal.signal(signal.SIGINT, sair)
    print(f"[fanctl] iniciado (ventoinhas da placa-mãe controladas: {len(pwms)})", flush=True)
    erros = 0
    try:
        while True:
            try:
                ctl.ciclo()
                grava_status(ctl.estado)
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
