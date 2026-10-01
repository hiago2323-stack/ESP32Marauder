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
(Whisper para ouvir, Kokoro 'pf_dora' feminina para falar, Piper masculina como reserva), Android SDK, serviços no boot e atalho
"Betina & IA" na área de trabalho. Log em `~/localai-install.log`.

Depois de reiniciar, abra o atalho **Betina & IA** (ou `http://localhost:8080`).

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

## App do celular (acesso de qualquer lugar)

O app **Betina & IA** (`dist/Betina-IA.apk`, código em `android-client/`) é a "cara" do PC no celular.
Ele tem uma **faixa de telemetria em tempo real** no topo (atualiza a cada ~1,5 s): uso e temperatura da
GPU e da CPU, VRAM, RAM e estado da IA. Tocando na faixa abre o painel com gráficos estilo osciloscópio.
O visual segue o app "Decker Cyber Segurança" (terminal escuro, verde neon e ciano), e o ícone é um
escudo duplo com a letra B. Mesma assinatura das versões antigas: instalar por cima atualiza sem perder nada.
Ele tem todas as funções da tela (conversa, voz, pesquisa, memória, criar apps/firmware, Parar) e o
processamento continua no PC. A conexão é pelo **Tailscale** (VPN privada).

1. No PC: `sudo tailscale up` (entre na sua conta). No celular: instale o Tailscale e entre na mesma conta.
2. Instale `Betina-IA.apk` no celular (permita instalar de fontes desconhecidas).
3. No PC, abra o Betina & IA e clique em **📲 Conectar celular**: mostra o endereço e o token.
4. No app, digite o endereço e o token. Pronto.

O servidor aceita conexões de fora do PC **somente com o token**. Nunca abra a porta 8080 no roteador.
Para recompilar o app: `cd android-client && gradle assembleDebug` (a chave de assinatura fica no
repositório, para as atualizações instalarem por cima).

## Criar apps Android e firmware ESP32

Marque **📱 Criar app Android** na tela e descreva o app. A IA escreve um único arquivo Java
(sem bibliotecas externas), o servidor monta o projeto Gradle, compila e, se der erro, devolve o
erro à IA para corrigir (até 2 vezes). O APK fica em `~/localai/apps` e na janela **📱 Meus apps**.
Para **firmware ESP32**, escolha o modo 🔌 e a placa: a IA escreve um sketch Arduino, o `arduino-cli`
compila e a conversa mostra os `.bin` para baixar (o `-completo.bin` é gravado no endereço 0x0).
Todos os arquivos ficam também em **📁 Arquivos**.
O botão **⏹ Parar** (ou Esc) cancela a escrita e a compilação. A primeira compilação baixa as
ferramentas do Android (algumas centenas de MB) e demora mais.

## Anexar, modificar e criar

- **📎 Anexar** (ou arrastar/colar): imagens, `.ino`, `.bin`, `.apk` e arquivos de código. Cada um é analisado:
  APK (pacote, permissões, assinatura), `.bin` (chip ESP32, partições, endereço de gravação), `.ino` (setup/loop/bibliotecas).
- **Modos** (botão ＋): Conversa, App Android rápido (1 arquivo), App Android avançado (vários arquivos, layouts XML e AndroidX),
  Firmware ESP32, Página web, Programa Python e Imagem.
- **✏ Modificar**: em qualquer item criado (ou em **📁 Arquivos**) a IA recebe o código atual e devolve a versão nova.
  Um `.ino` anexado também pode ser **só compilado**. APK enviado de volta é reconhecido se foi criado aqui.
- **Imagens**: Stable Diffusion Turbo local (`stable-diffusion.cpp`), texto→imagem e imagem→imagem, sem filtro de conteúdo
  acrescentado por este projeto. O pedido é traduzido para inglês pela IA de texto (opcional).
- **Limite**: `.apk` de terceiros só são analisados (não reempacotados). `.bin` é analisado, não editado.

## Recursos (RAM, VRAM, CPU)

`recursos.py`: uma tarefa pesada por vez, modelos de voz descarregados da RAM após 10 min parados, programas pesados com
prioridade baixa (`nice`), geração de imagem em GPU/híbrido/CPU conforme a VRAM livre (e repete na CPU se a placa falhar),
e `NGL=auto` no `start_llm.sh` (camadas do modelo na placa calculadas pela VRAM livre).

## Desempenho e precisão

- **Perfis de modelo** (⚙ Configurações › Modelo de IA): *Rápido* (3B), *Preciso* (7B) e *Código* (Coder 7B).
  Dá para baixar e trocar pela tela. O 3B é bem mais rápido; o 7B é mais correto; o Código é o melhor para apps/firmware.
- **Velocidade**: o prompt é mantido estável e o contexto (memórias, web) vai na última mensagem, então o
  `llama-server` reaproveita o cache entre perguntas; threads de geração = núcleos físicos; `--mlock`, `-np 1`,
  `--cache-reuse` (só entram se a versão do llama.cpp conhecer a opção); contexto de 8192.
- **Precisão**: temperatura 0.4, pesquisa web com poucos resultados, e voz com modelo maior (large-v3-turbo) e
  dica de vocabulário técnico (ESP32, Tailscale, Gradle...).

## Ventoinhas

`fanctl.py` (serviço `localai-fan`) ajusta a ventoinha da GPU pela temperatura (curva de 30% a 100%) e a devolve
ao automático ao parar. Pela tela: ⚙ Configurações › Sistema e ventoinhas. Para a CPU, a placa-mãe costuma controlar
sozinha pela BIOS; `bash ~/localai/diagnostico_fans.sh` mostra se o Linux consegue controlá-la (então se define
`FANCTL_CPU_PWM` em `server/fan.env`).

## Tailscale

O `atualizar.sh` liga o `tailscaled` no boot e faz o login uma vez (`sudo tailscale up`, ou `TS_AUTHKEY=... bash atualizar.sh`
para entrar sem navegador). Depois disso o PC reconecta sozinho a cada ligada.

## Atualizar sem reinstalar

```
bash atualizar.sh          # troca o código e reinicia os serviços (mantém token, memória e modelo); NGL=4
NGL=2 bash atualizar.sh    # menos camadas na placa = menos memória de vídeo (se der "CUDA out of memory")
```

## Como a IA "aprende com o tempo"

O modelo não muda; a **memória** cresce. A cada pergunta, o servidor busca na memória o que é
relacionado e entrega ao modelo junto com a pergunta.

- Diga "lembre que meu cachorro se chama Thor" (ou use o botão 📌 numa resposta, ou o painel 🧠 Memória).
- Com "Pesquisar na web" e "Aprender com pesquisas" ligados, o que ele descobre fica guardado.
  Só guarda se a pesquisa realmente trouxe resultados.
- No painel 🧠 Memória você vê tudo e apaga o que estiver errado.
- Rodar o instalador de novo por cima é seguro: mantém token e memórias.
