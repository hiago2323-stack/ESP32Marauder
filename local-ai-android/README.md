# Local AI Android

IA 100% local no PC (Ryzen + GTX 960), com tela de conversa por **texto e voz**,
pesquisa na web e compilação de apps Android. O celular e o acesso remoto
(Tailscale) ficam para uma etapa futura.

## Instalação (um comando)

No Linux Mint XFCE, com o arquivo `instalar_tudo_v2.sh` salvo no PC, **sem sudo**:

```
bash instalar_tudo_v2.sh            # modelo 7B (padrão)
MODELO=3b bash instalar_tudo_v2.sh  # modelo 3B, mais leve
```

Se outro instalador ainda estiver rodando, use `bash esperar_e_instalar.sh`: ele espera o
anterior terminar e dispara este sozinho. Não salve um instalador novo com o nome de um que está rodando.

Instala: driver NVIDIA 580 (a GTX 960 não é suportada pelo 590+), CUDA 12.6,
telemetria/controle da GPU, llama.cpp + modelo Qwen2.5-Coder 7B (ou 3B), servidor, memória, voz
(Whisper para ouvir, Piper para falar), Android SDK, serviços no boot e atalho
"IA Local" na área de trabalho. Log em `~/localai-install.log`.

Depois de reiniciar, abra o atalho **IA Local** (ou `http://localhost:8080`).

## Estrutura

- `instalar_tudo_v2.sh`: instalador completo (contém os arquivos de `server/` embutidos).
- `server/`: código-fonte do servidor, usado para desenvolvimento.
  - `main.py`: rotas `/` (tela), `/chat`, `/stt`, `/tts`, `/memory`, `/search`, `/fetch`, `/build`.
  - `memory.py`: memória de longo prazo (SQLite + busca de texto) em `~/localai/memory.db`.
  - `static/index.html`: tela de conversa.

O instalador é gerado a partir de `server/`; mantenha os dois iguais.

## Próximas etapas

- [ ] Compilar `.bin` de ESP32 (`arduino-cli`)
- [ ] App Android + acesso remoto (Tailscale)

## Como a IA "aprende com o tempo"

O modelo não muda; a **memória** cresce. A cada pergunta, o servidor busca na memória o que é
relacionado e entrega ao modelo junto com a pergunta.

- Diga "lembre que meu cachorro se chama Thor" (ou use o botão 📌 numa resposta, ou o painel 🧠 Memória).
- Com "Pesquisar na web" e "Aprender com pesquisas" ligados, o que ele descobre fica guardado.
  Só guarda se a pesquisa realmente trouxe resultados.
- No painel 🧠 Memória você vê tudo e apaga o que estiver errado.
- Rodar o instalador de novo por cima é seguro: mantém token e memórias.
