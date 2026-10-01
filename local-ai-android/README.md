# Local AI Android

IA 100% local no PC (Ryzen + GTX 960), com tela de conversa por **texto e voz**,
pesquisa na web e compilação de apps Android. O celular e o acesso remoto
(Tailscale) ficam para uma etapa futura.

## Instalação (um comando)

No Linux Mint XFCE, com o arquivo `instalar_tudo.sh` salvo no PC, **sem sudo**:

```
bash instalar_tudo.sh
```

Instala: driver NVIDIA 580 (a GTX 960 não é suportada pelo 590+), CUDA 12.6,
telemetria/controle da GPU, llama.cpp + modelo Qwen2.5-Coder 7B, servidor, voz
(Whisper para ouvir, Piper para falar), Android SDK, serviços no boot e atalho
"IA Local" na área de trabalho. Log em `~/localai-install.log`.

Depois de reiniciar, abra o atalho **IA Local** (ou `http://localhost:8080`).

## Estrutura

- `instalar_tudo.sh`: instalador completo (contém os arquivos de `server/` embutidos).
- `server/`: código-fonte do servidor, usado para desenvolvimento.
  - `main.py`: rotas `/` (tela), `/chat`, `/stt`, `/tts`, `/search`, `/fetch`, `/build`.
  - `static/index.html`: tela de conversa.

O instalador é gerado a partir de `server/`; mantenha os dois iguais.

## Próximas etapas

- [ ] Compilar `.bin` de ESP32 (`arduino-cli`)
- [ ] Memória: guardar o que a IA aprende na web
- [ ] App Android + acesso remoto (Tailscale)
