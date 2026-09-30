# Local AI Android

IA local com app Android como controle remoto, servidor no PC (Ryzen + GTX 960)
para rodar o modelo e compilar apps.

```
Redmi A3 (app)  ⇄  Wi-Fi / Tailscale  ⇄  PC Linux Mint XFCE
                                           ├─ llama-server (modelo)
                                           └─ server/ (chat, busca, build)
```

## Etapas

- [x] 1. Servidor do PC (`server/`)
- [x] 1b. Telemetria, GPU e IA local no PC (scripts `install_*.sh`)
- [ ] 2. App Android (chat + conexão com o PC)
- [ ] 3. Pesquisa web (SearXNG no Positivo)
- [ ] 4. IA pequena local no celular
- [ ] 5. Memória / skills

## Etapa 1 — Instalar o servidor no PC

Depois do Linux Mint XFCE instalado e do driver NVIDIA (série 580) ativo:

1. Copie esta pasta para o PC (ou `git clone` do repositório).
2. Abra o terminal na pasta `local-ai-android/server` e rode:
   ```
   bash setup.sh
   ```
   Ele instala tudo e mostra o **token** (senha). Anote.
3. Inicie:
   ```
   bash run.sh
   ```
4. No navegador do PC, abra `http://localhost:8080/health`. Deve aparecer `{"ok":true}`.

O `/chat` só funciona depois de subir o `llama-server` (etapa seguinte, guia a caminho).
O `/build` precisa do Android SDK instalado (também no próximo guia).

## Segurança

- Nunca abra a porta 8080 no roteador. Acesse de fora só pelo **Tailscale**.
- O token fica em `server/.env`, que não vai para o Git.

## Etapa 1b — Ordem de instalação no PC (dentro de `server/`)

```
bash install_monitoring.sh     # telemetria e controle da GPU (reinicie depois)
bash setup.sh                  # servidor (token)
bash install_llm.sh            # llama.cpp com CUDA + modelo
bash install_android_sdk.sh    # para o /build compilar apps
```

Para usar: em um terminal `bash start_llm.sh`, em outro `bash run.sh`.
Ajuste `NGL` (camadas na GPU) com `NGL=10 bash start_llm.sh` olhando o `nvidia-smi`.
