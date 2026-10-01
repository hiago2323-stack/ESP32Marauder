#!/usr/bin/env python3
"""Processo isolado que gera UM vídeo curto com o stable-diffusion.cpp (modelo Wan 2.1, texto para vídeo).

Lê um JSON na entrada padrão e escreve eventos JSON (um por linha) na saída, como o imggen.py.
Roda separado do servidor para poder ser cancelado na hora (botão Parar) e devolver toda a memória.
Grava os quadros como PNG numa pasta; o servidor junta tudo num .mp4.
"""
import json
import os
import sys


def evento(**kw):
    print(json.dumps(kw, ensure_ascii=False), flush=True)


def main():
    cfg = json.loads(sys.stdin.read())
    modo = cfg.get("modo", "cpu")
    if modo == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"   # sem GPU: tudo na CPU
    try:
        from stable_diffusion_cpp import StableDiffusion
        kw = dict(diffusion_model_path=cfg["difusao"], vae_path=cfg["vae"], t5xxl_path=cfg["texto"],
                  n_threads=cfg["threads"], vae_decode_only=False, diffusion_flash_attn=True,   # (vídeo exige vae_decode_only=False)
                  
                  keep_clip_on_cpu=True,           # o codificador de texto (3,6 GB) nunca cabe na placa
                  enable_mmap=True)
        if modo == "segmentado":                   # a difusão (e o VAE) na placa, em partes, dentro do limite que sobra
            kw.update(offload_params_to_cpu=True, max_vram=cfg["orcamento"], keep_vae_on_cpu=False)
        elif modo == "gpu":
            kw.update(keep_vae_on_cpu=False)
        sd = StableDiffusion(**kw)
        evento(tipo="carregado", modo=modo)

        def passo(i, total, tempo):
            evento(tipo="passo", passo=int(i), total=int(total), seg=round(float(tempo), 1))

        quadros = sd.generate_video(
            prompt=cfg["prompt"], negative_prompt=cfg.get("negativo", ""), width=cfg["largura"], height=cfg["altura"],
            cfg_scale=cfg.get("cfg", 6.0), sample_method="euler", sample_steps=cfg["passos"], flow_shift=3.0,
            video_frames=cfg["quadros"], seed=cfg["seed"], vae_tiling=True, progress_callback=passo)
        os.makedirs(cfg["pasta"], exist_ok=True)
        for n, q in enumerate(quadros):
            q.save(os.path.join(cfg["pasta"], f"q{n:04d}.png"))
        evento(tipo="pronto", quadros=len(quadros))
    except Exception as e:  # o servidor decide o que fazer (por exemplo, repetir só pela CPU)
        evento(tipo="erro", msg=f"{type(e).__name__}: {e}"[:300])
        sys.exit(1)


if __name__ == "__main__":
    main()
