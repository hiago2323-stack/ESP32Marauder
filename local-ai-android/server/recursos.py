"""Gerenciador de recursos: RAM, VRAM e CPU divididos entre as tarefas do servidor.

Ideias (o PC tem 16 GB de RAM, 2 GB de VRAM e 4 núcleos):
  * Só UMA tarefa pesada por vez (compilar, gerar imagem, escrever um app): elas competem
    pela mesma memória e pelos mesmos núcleos. As demais esperam na fila.
  * Modelos de voz grandes ficam na RAM só enquanto são usados: depois de alguns minutos sem
    uso eles são descarregados, e a RAM volta para o resto.
  * Programas pesados (Gradle, arduino-cli, gerador de imagens) rodam com prioridade baixa
    ("nice"), para a conversa continuar respondendo enquanto eles trabalham.
  * A escolha entre GPU, CPU ou os dois juntos depende da VRAM que está livre naquela hora.
"""
import asyncio
import gc
import os
import shutil
import subprocess
import time
from pathlib import Path

import config

# Trabalhos pesados (compilar, gerar imagem, criar app/firmware): um de cada vez.
pesado = asyncio.Lock()

_cache_nucleos: int | None = None


def nucleos_fisicos() -> int:
    """Núcleos físicos (os threads extras do SMT quase não ajudam em IA, que depende da memória)."""
    global _cache_nucleos
    if _cache_nucleos is None:
        n = 0
        try:
            r = subprocess.run(["lscpu", "-p=CORE,SOCKET"], capture_output=True, text=True, timeout=3)
            n = len({ln for ln in r.stdout.splitlines() if ln and not ln.startswith("#")})
        except (OSError, subprocess.TimeoutExpired):
            pass
        _cache_nucleos = n if n >= 1 else max(1, (os.cpu_count() or 2) // 2)
    return _cache_nucleos


def ram_livre_mb() -> int:
    """RAM que dá para usar agora (inclui o cache de arquivos, que o sistema devolve quando precisa)."""
    try:
        for ln in open("/proc/meminfo"):
            if ln.startswith("MemAvailable:"):
                return int(ln.split()[1]) // 1024
    except OSError:
        pass
    return 0


def vram_livre_mb() -> int | None:
    """VRAM livre da GPU NVIDIA em MiB, ou None se não houver GPU/nvidia-smi."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return int(float(r.stdout.strip().splitlines()[0]))
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return None


def baixa_prioridade(cmd: list[str]) -> list[str]:
    """Roda o comando com prioridade baixa, se o 'nice' existir."""
    return (["nice", "-n", "10"] + cmd) if shutil.which("nice") else cmd


# ------------------------------------------------------------ modelos ociosos
_modelos: dict[str, dict] = {}


def registra(nome: str, descarrega) -> None:
    """Registra um modelo carregado e a função que o descarrega da memória."""
    _modelos[nome] = {"descarrega": descarrega, "uso": time.time()}


def usou(nome: str) -> None:
    if nome in _modelos:
        _modelos[nome]["uso"] = time.time()


def libera_ociosos(forcar: bool = False) -> list[str]:
    """Descarrega modelos que ficaram parados. Com forcar=True, descarrega todos (falta de RAM)."""
    limite = config.OCIOSO_MIN * 60
    soltos = []
    for nome, m in list(_modelos.items()):
        if forcar or time.time() - m["uso"] > limite:
            try:
                m["descarrega"]()
            finally:
                _modelos.pop(nome, None)
                soltos.append(nome)
    if soltos:
        gc.collect()
    return soltos


async def vigia() -> None:
    """Tarefa de fundo: a cada minuto descarrega o que está parado há muito tempo."""
    while True:
        await asyncio.sleep(60)
        try:
            libera_ociosos()
        except Exception:
            pass


def garante_ram(minimo_mb: int, o_que: str) -> None:
    """Confere se há RAM para uma tarefa pesada; tenta liberar modelos de voz antes de desistir."""
    if ram_livre_mb() >= minimo_mb:
        return
    libera_ociosos(forcar=True)
    livre = ram_livre_mb()
    if livre < minimo_mb:
        raise RuntimeError(
            f"Pouca memória livre para {o_que}: {livre} MB livres e preciso de uns {minimo_mb} MB. "
            "Feche programas pesados (navegador com muitas abas) e tente de novo.")


# ------------------------------------------------------------ GPU, CPU ou os dois
def modo_imagem(v: int | None = None) -> str:
    """Onde rodar o gerador de imagens/vídeo, conforme a VRAM livre agora.

    gpu        : tudo na placa, de uma vez (precisa de ~2,4 GB livres; numa placa de 2 GB quase nunca)
    segmentado : o modelo roda na placa EM PARTES, dentro de um limite de memória (max_vram do stable-diffusion.cpp);
                 os pesos ficam na RAM e vão para a placa conforme o uso. Cabe nos ~0,7 GB ou mais que sobram.
    cpu        : tudo na CPU
    """
    v = vram_livre_mb() if v is None else v
    if v is None:
        return "cpu"
    if v >= 2400:
        return "gpu"
    if v >= 600:
        return "segmentado"
    return "cpu"


def orcamento_vram_gib(v: int) -> float:
    """Quanto da VRAM livre o gerador pode usar (em GiB), deixando folga para a tela e o modelo de texto."""
    return round(max(0.35, min((v - 250) / 1024, 1.8)), 2)


# ------------------------------------------------------------ a placa deu conta? (aprende com as tentativas)
def _arq_gpu() -> Path:
    return config.MODELO_ENV.parent / "gpu_imagem.json"


def _le_gpu() -> dict:
    try:
        import json
        return json.loads(_arq_gpu().read_text())
    except (OSError, ValueError):
        return {}


def gpu_liberada(tarefa: str) -> bool:
    """Falso se a placa falhou 2 vezes seguidas nas últimas 24 h nesta tarefa ('imagem' ou 'video')."""
    e = _le_gpu().get(tarefa, {})
    return not (e.get("falhas", 0) >= 2 and time.time() - e.get("quando", 0) < 24 * 3600)


def anota_gpu(tarefa: str, ok: bool) -> None:
    import json
    d = _le_gpu()
    d[tarefa] = {"falhas": 0 if ok else d.get(tarefa, {}).get("falhas", 0) + 1, "quando": time.time()}
    try:
        _arq_gpu().write_text(json.dumps(d))
    except OSError:
        pass
