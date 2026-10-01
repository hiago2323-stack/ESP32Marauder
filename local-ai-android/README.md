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

## Imagens

Dois modelos: **Rápido** (SD-Turbo, arte/ilustração) e **Realista** (Realistic Vision V6.0, fotos de pessoas, lugares e objetos; Apache-2.0). Escolha na tela de criar imagem. O atualizador baixa os dois em segundo plano.

**Placa de vídeo**: o modelo de imagem precisa de ~1,8 GB na placa (1,4 GB de pesos + 0,4 GB de cálculo), mais do que sobra
numa GTX 960 de 2 GB com a IA de texto carregada. Por isso ele roda na placa **em partes** (`max_vram` do stable-diffusion.cpp):
os pesos ficam na RAM e vão para a placa conforme o uso, dentro do limite de memória que sobra; todos os núcleos da CPU ajudam.
Se a placa falhar 2 vezes seguidas, ele usa só a CPU por 24 h (não perde tempo tentando de novo). A IA de texto deixa
~1,1 GB livres quando o modelo de imagens existe (`RESERVA_VRAM=NNNN` muda isso).

## Vídeo curto

Modo 🎬 **Vídeo curto**: texto para vídeo com o Wan 2.1 (1,3 bilhão de parâmetros, oficial, Apache-2.0) pelo mesmo motor.
Clipes de 0,5 a 2 s (16 quadros por segundo) em 320×192 ou 480×272, salvos em `.mp4`. Na CPU de 4 núcleos um clipe de ~1 s
leva de 15 a 25 minutos e a qualidade é simples (borrada em tamanhos pequenos); com a placa (em partes) deve ser mais rápido.
O trabalho roda em segundo plano no PC: pode fechar o app; ele continua e o app volta a acompanhar sozinho. Modelos (~5 GB)
baixados em segundo plano pelo atualizador.

## Adicionar outras IAs (busca pelo nome)

⚙ Configurações › **Modelo de IA** › *Adicionar outra IA*: digite só o nome (ex.: "llama 3.2 3b") ou o endereço do
Hugging Face. A busca é online e neutra (não há lista fechada nem bloqueio); mostra os arquivos `.gguf`, marca o recomendado
para o seu PC (avisos de tamanho são só informação), baixa com progresso e passa a usar (o número de camadas é lido do
próprio arquivo). Modelos de imagem completos (Stable Diffusion em um arquivo) também entram na lista de modelos de imagem.
Registro em `~/localai/modelos_extra.json`.

## Inicia com o Linux

Os serviços `localai-llm` (modelo), `localai-server` (servidor) e `localai-fan` (ventoinhas) sobem em todo boot, mesmo sem
login. No boot o modelo espera o driver da placa e os discos (SSD) ficarem prontos antes de decidir quantas camadas vão para
a GPU (antes, se o driver demorasse, a IA podia ficar só na CPU a sessão inteira). O atualizador refaz os serviços.

## SSD como parte rápida

Se o sistema está num HD e há um SSD à parte (partição ext4/btrfs/xfs), o `atualizar.sh` move para ele os modelos de IA, Gradle, Android SDK e núcleo ESP32 (as pastas antigas viram links). `SEM_SSD=1` pula; `SSD_DESTINO=/caminho` escolhe a pasta.

## Biblioteca no disco grande

O `atualizar.sh` escolhe sozinho o disco grande (>= 100 GB, que não seja o do sistema; se estiver desmontado ele monta, só leitura/escrita, sem formatar, e deixa montado em todo boot com `nofail`), cria `~/biblioteca` apontando para ele e baixa em segundo plano (`tail -f ~/localai/biblioteca.log`): exemplos e bibliotecas ESP32/Arduino, exemplos de apps Android, documentação do Python, Arduino e MDN (web) e a Wikipedia em português offline. O servidor indexa tudo (SQLite FTS5, devagar e em segundo plano) e a IA recebe os trechos relevantes ao responder e ao criar apps/firmware. Joguei seus arquivos em `~/biblioteca/meus` e eles entram na busca também. Tela: engrenagem > Biblioteca. Para escolher outro lugar: `BIBLIOTECA_DESTINO=/caminho bash atualizar.sh`; para pular: `SEM_BIBLIOTECA=1`.

## App do celular (acesso de qualquer lugar)

O app **Betina & IA** (`dist/Betina-IA.apk`, v1.3, código em `android-client/`) é a "cara" do PC no celular: tela de abertura
com a marca, faixa de telemetria com logo (e o vídeo em andamento), navegação inferior (Conversa, Criar, Arquivos, Biblioteca,
Mais) e uma bandeja com todas as ferramentas (apps, firmware, web, Python, imagem e vídeo).
Ele tem uma **faixa de telemetria em tempo real** no topo (atualiza a cada ~1,5 s): uso e temperatura da
GPU e da CPU, VRAM, RAM e estado da IA. Tocando na faixa abre o painel com gráficos estilo osciloscópio.
O visual segue o app "Decker Cyber Segurança" (terminal escuro, verde neon e ciano), e o ícone é um
escudo duplo com a letra B. Mesma assinatura das versões antigas: instalar por cima atualiza sem perder nada.
Ele tem todas as funções da tela (conversa, voz, pesquisa, memória, criar apps/firmware, Parar) e o
processamento continua no PC. A conexão é pelo **Tailscale** (VPN privada).

1. No PC o instalador/atualizador já liga o Tailscale e dá ao PC o nome fixo **betina** (você só entra na sua conta uma vez, no link que ele mostra).
2. No celular: instale o app **Tailscale**, entre **na mesma conta** e deixe conectado.
3. Instale `Betina-IA.apk` e abra. Ele acha o PC sozinho em `http://betina:8080`, **sem endereço e sem token**
   (quem vem pela VPN já foi autenticado pela sua conta). Se não achar, a tela de erro tem o botão "Abrir o Tailscale".

Fora da VPN (rede de casa, por exemplo) o servidor aceita conexões **somente com o token** (para exigir o token também na VPN: `TAILNET_SEM_TOKEN=0`). Nunca abra a porta 8080 no roteador.
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

`fanctl.py` (serviço `localai-fan`) sobe a rotação **pelo uso e pela temperatura**: com a GPU ou a CPU ocupada ela já
acelera antes de esquentar (ex.: GPU a 85% de uso → ventoinha a 85%), e desce devagar. **GPU**: por NVML ou nvidia-settings.
**Processador e caixa**: na primeira vez o serviço testa sozinho quais saídas PWM da placa-mãe mexem numa ventoinha (elas
aceleram e desaceleram por alguns segundos) e passa a controlar todas; o resultado fica guardado. O atualizador instala o
`lm-sensors` e roda o `sensors-detect` se o Linux ainda não enxergar o chip da placa-mãe. Tudo volta ao automático ao parar.
O estado aparece em ⚙ › Sistema e ventoinhas e no painel do app. Ajustes em `server/fan.env` (`FANCTL_CPU_PWM`, `FANCTL_AUTO_PWM=0`).

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
