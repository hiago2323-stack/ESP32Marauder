"""Leituras do PC para a tela: temperaturas, ventoinhas, uso da GPU e da memória."""
import json
import os
import subprocess

import recursos
import time
from pathlib import Path

_cache: tuple[float, dict | None] = (0.0, None)


def _num(s: str):
    try:
        return float(s)
    except ValueError:
        return None


def _gpu() -> dict | None:
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,fan.speed,power.draw,name",
             "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        t, u, mu, mt, fan, pw, nome = [x.strip() for x in r.stdout.strip().splitlines()[0].split(",", 6)]
        return {"nome": nome, "temp": _num(t), "uso": _num(u), "mem_usada": _num(mu), "mem_total": _num(mt),
                "ventoinha": _num(fan), "watts": _num(pw)}
    except (OSError, subprocess.TimeoutExpired):
        return None


def _hwmon() -> tuple[float | None, dict]:
    """Temperatura da CPU (k10temp/coretemp) e rotação das ventoinhas (RPM) de todos os sensores."""
    temp = None
    fans = {}
    for d in sorted(Path("/sys/class/hwmon").glob("hwmon*")):
        try:
            nome = (d / "name").read_text().strip()
        except OSError:
            continue
        if nome in ("k10temp", "coretemp", "zenpower") and temp is None:
            for f in sorted(d.glob("temp*_input")):
                label = ""
                try:
                    label = (d / f.name.replace("_input", "_label")).read_text().strip()
                except OSError:
                    pass
                if label in ("Tctl", "Tdie", "Package id 0") or temp is None:
                    v = _num(f.read_text().strip())
                    if v is not None:
                        temp = v / 1000
        for f in sorted(d.glob("fan*_input")):
            v = _num(f.read_text().strip()) if f.exists() else None
            if v is not None:
                fans[f"{nome}/{f.name.split('_')[0]}"] = int(v)
    return temp, fans


_ult_cpu: tuple[int, int] | None = None


def _uso_cpu() -> float | None:
    """Uso total da CPU em % desde a leitura anterior (a primeira leitura compara com 0,3 s antes)."""
    global _ult_cpu

    def ler():
        v = [int(x) for x in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
        return sum(v), v[3] + (v[4] if len(v) > 4 else 0)  # total, parado (idle+iowait)

    try:
        t, ocioso = ler()
        if _ult_cpu is None:
            time.sleep(0.3)
            _ult_cpu = (t, ocioso)
            t, ocioso = ler()
        dt, di = t - _ult_cpu[0], ocioso - _ult_cpu[1]
        _ult_cpu = (t, ocioso)
        return round(max(0.0, min(100.0, 100.0 * (dt - di) / dt)), 1) if dt > 0 else None
    except (OSError, ValueError, IndexError):
        return None


def _ram() -> dict:
    m = {}
    try:
        for ln in Path("/proc/meminfo").read_text().splitlines():
            k, v = ln.split(":", 1)
            m[k] = int(v.split()[0]) / 1024
    except (OSError, ValueError):
        return {}
    total, livre = m.get("MemTotal", 0), m.get("MemAvailable", 0)
    return {"total": round(total), "usada": round(total - livre)}


def _controle_ventoinha() -> str:
    """'active' se o serviço de controle automático das ventoinhas (localai-fan) está rodando."""
    try:
        r = subprocess.run(["systemctl", "is-active", "localai-fan.service"], capture_output=True, text=True, timeout=2)
        return r.stdout.strip() or "inactive"
    except (OSError, subprocess.TimeoutExpired):
        return "desconhecido"


def _estado_ventoinhas() -> dict:
    """Estado gravado pelo serviço de ventoinhas (/run/localai-fan.json). Vazio/antigo = serviço parado."""
    arq = Path(os.environ.get("FANCTL_STATUS", "/run/localai-fan.json"))
    try:
        d = json.loads(arq.read_text())
        if time.time() - d.get("hora", 0) > 15 or d.get("parado"):
            return {"ativo": False}
        d["ativo"] = True
        return d
    except (OSError, ValueError):
        return {"ativo": False}


def ler() -> dict:
    global _cache
    agora = time.time()
    if _cache[1] is not None and agora - _cache[0] < 2:
        return _cache[1]
    temp, fans = _hwmon()
    dado = {"gpu": _gpu(), "cpu": {"temp": temp, "uso": _uso_cpu(), "carga": round(os.getloadavg()[0], 2), "nucleos": os.cpu_count()},
            "ventoinhas": fans, "ram": _ram(), "controle_ventoinha": _controle_ventoinha(), "ventoinha_ctl": _estado_ventoinhas(),
            "modelos_na_ram": sorted(recursos._modelos)}
    _cache = (agora, dado)
    return dado
