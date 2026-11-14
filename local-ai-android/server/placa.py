"""Placa de vídeo AMD (driver amdgpu): leitura e controle da ventoinha pelo /sys, sem programas extras.

Para NVIDIA o servidor usa nvidia-smi/NVML; aqui fica o equivalente para AMD (ex.: RX 580). Se houver mais de uma
AMD (a gráfica integrada do Ryzen e a placa dedicada), vale a de maior VRAM, que é a placa dedicada.
Controle da ventoinha: pwm1_enable = 1 (manual) / 2 (automático da placa) e pwm1 de 0 a 255; precisa de root.
"""
import glob
import os
from pathlib import Path

RAIZ = "/sys/class/drm"
AMD = "0x1002"


def _ler(p: Path) -> str | None:
    try:
        return p.read_text().strip()
    except OSError:
        return None


def _int(p: Path) -> int | None:
    v = _ler(p)
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None


class PlacaAmd:
    def __init__(self, dev: Path):
        self.dev = Path(dev)
        hw = sorted(glob.glob(str(self.dev / "hwmon" / "hwmon*")))
        self.hw = Path(hw[0]) if hw else None
        self.metodo = None
        self.erro = ""
        self._manual = False

    # ---- leitura
    def nome(self) -> str:
        return _ler(self.dev / "product_name") or "AMD Radeon"

    def temp(self) -> float:
        if self.hw:
            for f in ("temp1_input", "temp2_input"):
                v = _int(self.hw / f)
                if v is not None:
                    return v / 1000
        raise OSError("sem sensor de temperatura da placa AMD")

    def uso(self) -> float | None:
        v = _int(self.dev / "gpu_busy_percent")
        return None if v is None else float(v)

    def vram_mb(self) -> tuple[int, int] | None:
        """(usada, total) em MiB."""
        t, u = _int(self.dev / "mem_info_vram_total"), _int(self.dev / "mem_info_vram_used")
        return None if t is None else ((u or 0) // 1048576, t // 1048576)

    def vram_livre_mb(self) -> int | None:
        v = self.vram_mb()
        return None if v is None else max(0, v[1] - v[0])

    def ventoinha_pct(self) -> float | None:
        if not self.hw:
            return None
        pwm = _int(self.hw / "pwm1")
        if pwm is not None:
            return round(100 * pwm / 255, 1)
        rpm, mx = _int(self.hw / "fan1_input"), _int(self.hw / "fan1_max")
        return round(100 * rpm / mx, 1) if rpm is not None and mx else None

    def watts(self) -> float | None:
        if self.hw:
            for f in ("power1_average", "power1_input"):
                v = _int(self.hw / f)
                if v is not None:
                    return round(v / 1e6, 1)
        return None

    def resumo(self) -> dict:
        v = self.vram_mb()
        try:
            t = round(self.temp(), 1)
        except OSError:
            t = None
        return {"nome": self.nome(), "temp": t, "uso": self.uso(), "mem_usada": v[0] if v else None,
                "mem_total": v[1] if v else None, "ventoinha": self.ventoinha_pct(), "watts": self.watts()}

    # ---- ventoinha (mesma interface do GpuNvidia do fanctl)
    def definir(self, pct) -> None:
        if not self.hw or not (self.hw / "pwm1_enable").exists():
            self.metodo, self.erro = None, "esta placa AMD não expõe controle de ventoinha (pwm1)"
            raise RuntimeError(self.erro)
        try:
            if not self._manual:
                (self.hw / "pwm1_enable").write_text("1")   # 1 = manual
                self._manual = True
            lo, hi = _int(self.hw / "pwm1_min"), _int(self.hw / "pwm1_max")
            valor = round(255 * max(0, min(100, pct)) / 100)
            valor = max(lo or 0, min(hi if hi else 255, valor))
            (self.hw / "pwm1").write_text(str(valor))
            if self.metodo != "amdgpu":
                print("[fanctl] GPU: controle da ventoinha via amdgpu (sysfs)", flush=True)
            self.metodo, self.erro = "amdgpu", ""
        except OSError as e:
            self.metodo, self.erro = None, str(e)[:160]
            raise RuntimeError(f"Não consegui controlar a ventoinha da placa AMD: {e}")

    def restaurar(self) -> None:
        """Devolve a ventoinha ao controle automático da placa."""
        if self.hw and self._manual:
            try:
                (self.hw / "pwm1_enable").write_text("2")   # 2 = automático
            except OSError as e:
                print(f"[fanctl] aviso ao restaurar a GPU: {e}", flush=True)
        self._manual = False
        self.metodo = None


def acha_amd(raiz: str = RAIZ) -> PlacaAmd | None:
    melhor, tam = None, -1
    for c in sorted(glob.glob(f"{raiz}/card[0-9]*")):
        dev = Path(c) / "device"
        if _ler(dev / "vendor") != AMD:
            continue
        t = _int(dev / "mem_info_vram_total")
        if t is not None and t > tam:
            melhor, tam = dev, t
    return PlacaAmd(melhor) if melhor else None


def fabricante(raiz: str = RAIZ) -> str | None:
    import shutil
    if acha_amd(raiz):
        return "amd"
    return "nvidia" if shutil.which("nvidia-smi") else None
