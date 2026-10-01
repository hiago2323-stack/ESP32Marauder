#!/usr/bin/env python3
"""Processo isolado que gera UMA imagem com o stable-diffusion.cpp (Stable Diffusion Turbo).

Lê um JSON na entrada padrão e escreve eventos JSON (um por linha) na saída. Roda separado do
servidor para: (1) poder ser cancelado na hora (botão Parar), (2) devolver toda a memória ao
terminar, (3) se a GPU não der conta, o servidor repete só pela CPU.
Não há filtro de conteúdo: o modelo gera o que foi pedido.
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
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"   # sem GPU: tudo na CPU, mesmo que o binding tenha CUDA
    try:
        from stable_diffusion_cpp import StableDiffusion
        kw = dict(model_path=cfg["modelo"], n_threads=cfg["threads"], vae_decode_only=not cfg.get("init"))
        if modo == "segmentado":                      # na placa EM PARTES, dentro do limite de memória que sobra
            kw.update(offload_params_to_cpu=True, max_vram=cfg["orcamento"], keep_clip_on_cpu=False, keep_vae_on_cpu=False)
        elif modo == "gpu":
            kw.update(keep_clip_on_cpu=False, keep_vae_on_cpu=False)
        sd = StableDiffusion(**kw)
        evento(tipo="carregado", modo=modo)

        def passo(i, total, tempo):
            evento(tipo="passo", passo=int(i), total=int(total), seg=round(float(tempo), 1))

        args = dict(prompt=cfg["prompt"], negative_prompt=cfg.get("negativo", ""), width=cfg["largura"],
                    height=cfg["altura"], cfg_scale=cfg.get("cfg", 1.0), sample_steps=cfg["passos"],
                    seed=cfg["seed"], sample_method="euler_a", progress_callback=passo,
                    vae_tiling=(modo != "cpu"))  # em GPU o decodificador precisa ser fatiado para caber na VRAM
        if cfg.get("init"):
            args.update(init_image=cfg["init"], strength=cfg.get("forca", 0.6))
        imagens = sd.generate_image(**args)
        imagens[0].save(cfg["saida"])
        evento(tipo="pronto", arquivo=cfg["saida"])
    except Exception as e:  # o servidor decide o que fazer (por exemplo, repetir só pela CPU)
        evento(tipo="erro", msg=f"{type(e).__name__}: {e}"[:300])
        sys.exit(1)


if __name__ == "__main__":
    main()
