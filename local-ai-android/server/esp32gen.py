"""Cria firmware (.bin) para ESP32 a partir de uma descrição.

Fluxo igual ao dos apps Android: a IA escreve UM sketch Arduino (.ino) -> o arduino-cli
compila -> se der erro, o erro volta à IA para corrigir -> o .bin é entregue para download.
Só usa as bibliotecas que já vêm no núcleo ESP32 do Arduino (WiFi, WebServer, Wire...).
"""
import asyncio
import os
import re
import shutil
import signal
import uuid
from pathlib import Path

import androidgen
import config
import entregas
import recursos

# id da tela -> (nome para o usuário, FQBN do arduino-cli)
PLACAS = {
    "esp32": ("ESP32 (DevKit comum)", "esp32:esp32:esp32"),
    "esp32s3": ("ESP32-S3", "esp32:esp32:esp32s3"),
    "esp32c3": ("ESP32-C3", "esp32:esp32:esp32c3"),
    "esp32s2": ("ESP32-S2", "esp32:esp32:esp32s2"),
    "esp32c6": ("ESP32-C6", "esp32:esp32:esp32c6"),
}

SYSTEM_PROMPT = """Você é um programador de firmware experiente em Arduino para ESP32. Escreva UM sketch Arduino completo em UM único arquivo .ino.

REGRAS OBRIGATÓRIAS:
1. Use SOMENTE bibliotecas que já vêm no núcleo ESP32 do Arduino (WiFi.h, WebServer.h, HTTPClient.h, Wire.h, SPI.h, Preferences.h, BluetoothSerial.h etc.). NENHUMA biblioteca externa (nada de Adafruit, FastLED, PubSubClient...).
2. Defina setup() e loop(). Inicie a serial com Serial.begin(115200).
3. Inclua TODOS os #include necessários. O código precisa compilar na primeira tentativa.
4. Para o LED da placa use o pino 2 (const int LED = 2;), a menos que o usuário peça outro.
5. Comentários e textos da serial em português do Brasil.
6. Não use delay() longos que travem o programa quando houver servidor web; prefira millis().

FORMATO DA RESPOSTA (siga exatamente, sem explicações):
NOME: <nome curto do firmware>
```cpp
<código completo>
```"""

EXEMPLO_PEDIDO = "piscar o LED a cada segundo e escrever na serial"
EXEMPLO_RESPOSTA = """NOME: Pisca LED
```cpp
// Pisca o LED a cada segundo e informa na serial
const int LED = 2;
bool ligado = false;

void setup() {
  Serial.begin(115200);
  pinMode(LED, OUTPUT);
  Serial.println("Pisca LED iniciado");
}

void loop() {
  ligado = !ligado;
  digitalWrite(LED, ligado ? HIGH : LOW);
  Serial.println(ligado ? "LED ligado" : "LED desligado");
  delay(1000);
}
```"""


def extrai_resposta(texto: str) -> tuple[str, str]:
    m = re.search(r"```(?:cpp|c\+\+|arduino|ino|c)?\s*\n(.*?)(?:```|$)", texto, re.S)
    if not m:
        raise ValueError("A IA não devolveu um bloco de código.")
    codigo = m.group(1).strip()
    if "void setup" not in codigo or "void loop" not in codigo:
        raise ValueError("O código não tem as funções setup() e loop().")
    n = re.search(r"NOME:\s*(.+)", texto)
    nome = (n.group(1).strip() if n else "Firmware IA")[:40] or "Firmware IA"
    return nome, codigo + "\n"


async def compila(pasta: Path, fqbn: str) -> tuple[bool, str, Path]:
    saida_dir = pasta / "out"
    cli = config.ARDUINO_CLI
    env = dict(os.environ)
    proc = await asyncio.create_subprocess_exec(
        *recursos.baixa_prioridade([cli, "compile", "--fqbn", fqbn, "--output-dir", str(saida_dir), str(pasta / "sketch")]),
        cwd=pasta, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )

    def mata_tudo():
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        saida, _ = await asyncio.wait_for(proc.communicate(), timeout=config.BUILD_TIMEOUT)
    except asyncio.TimeoutError:
        mata_tudo()
        return False, "A compilação passou do tempo limite.", saida_dir
    except asyncio.CancelledError:
        mata_tudo()
        raise
    return proc.returncode == 0, saida.decode(errors="replace"), saida_dir


async def gera_firmware(descricao: str, placa: str, emit, base_codigo: str | None = None,
                        base_nome: str | None = None) -> None:
    if placa not in PLACAS:
        await emit({"type": "error", "msg": "Placa desconhecida."})
        return
    nome_placa, fqbn = PLACAS[placa]
    if not Path(config.ARDUINO_CLI).exists():
        await emit({"type": "error", "msg": "O arduino-cli não está instalado. Rode: bash atualizar.sh"})
        return
    if androidgen._trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outra coisa. Espere terminar ou clique em Parar."})
        return
    async with androidgen._trava:
        try:
            recursos.garante_ram(1500, "compilar o firmware")
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
            return
        so_compilar = bool(base_codigo) and not descricao.strip()
        mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Crie: {EXEMPLO_PEDIDO}"},
            {"role": "assistant", "content": EXEMPLO_RESPOSTA},
            {"role": "user", "content": (
                f"Placa: {nome_placa}. Código ATUAL do sketch:\n```cpp\n{base_codigo[:14000]}\n```\n\n"
                f"Modifique conforme o pedido e devolva o sketch COMPLETO no mesmo formato. Pedido: {descricao or 'corrigir os erros'}")
             if base_codigo else f"Placa: {nome_placa}. Crie: {descricao}"},
        ]
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"esp-{id_}"
        try:
            for tentativa in range(config.MAX_FIX_ATTEMPTS + 1):
                if tentativa == 0:
                    await emit({"type": "status", "msg": "A IA está escrevendo o código do firmware… (pode levar alguns minutos)"})
                else:
                    await emit({"type": "status",
                                "msg": f"Deu erro ao compilar. A IA está corrigindo (tentativa {tentativa} de {config.MAX_FIX_ATTEMPTS})…"})
                if tentativa == 0 and so_compilar:   # sem pedido de mudança: compila o sketch como está
                    nome, codigo = base_nome or "Firmware", base_codigo
                    texto = f"NOME: {nome}\n```cpp\n{codigo}\n```"
                    await emit({"type": "status", "msg": "Compilando o seu sketch como ele está…"})
                else:
                    texto = await androidgen.pede_codigo(mensagens, emit)
                    try:
                        nome, codigo = extrai_resposta(texto)
                    except ValueError as e:
                        await emit({"type": "error", "msg": str(e), "log": texto[-1500:]})
                        return
                await emit({"type": "code", "text": codigo})

                if pasta.exists():
                    shutil.rmtree(pasta)
                (pasta / "sketch").mkdir(parents=True)
                (pasta / "sketch" / "sketch.ino").write_text(codigo)
                await emit({"type": "status", "msg": f"Compilando para {nome_placa}… (a primeira vez é mais lenta)"})
                ok, log, saida = await compila(pasta, fqbn)
                if ok:
                    slug = androidgen.slugify(nome)
                    arquivos = []
                    merged = next(iter(sorted(saida.glob("*.merged.bin"))), None)
                    app = next((p for p in sorted(saida.glob("*.ino.bin"))), None)
                    if merged:
                        arquivos.append((merged, f"{slug}-completo.bin", "Firmware completo (.bin): gravar no endereço 0x0"))
                    if app:
                        arquivos.append((app, f"{slug}-app.bin", "Só o aplicativo (.bin): para atualização OTA / endereço 0x10000"))
                    if not arquivos:
                        await emit({"type": "error", "msg": "Compilou, mas não encontrei o arquivo .bin.", "log": log[-1500:]})
                        return
                    meta = entregas.salva(id_, nome, descricao, "bin", arquivos, {"board": nome_placa, "code": codigo})
                    await emit({"type": "done", "id": id_, "name": nome, "kind": "bin", "files": meta["files"],
                                "note": f"Placa: {nome_placa}. Grave o '-completo.bin' no endereço 0x0 com o esptool ou com um gravador web (ex.: espressif.github.io/esptool-js)."})
                    return
                erros = androidgen.resumo_erros(log)
                if tentativa >= config.MAX_FIX_ATTEMPTS:
                    await emit({"type": "error", "msg": "Não consegui compilar o firmware depois das correções.", "log": erros})
                    return
                mensagens += [
                    {"role": "assistant", "content": texto},
                    {"role": "user", "content": (
                        "O código NÃO compilou. ERROS DE COMPILAÇÃO:\n" + erros +
                        "\n\nCorrija e devolva o arquivo COMPLETO no mesmo formato (NOME: e bloco ```cpp).")},
                ]
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
