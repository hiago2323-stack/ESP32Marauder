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
import gerador
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

SYSTEM_PROMPT = 'Você é um engenheiro de firmware sênior em Arduino para ESP32 (núcleo Arduino-ESP32 versão 3.x). Escreva UM sketch .ino COMPLETO e funcional.\n\nREGRAS:\n1. Defina setup() e loop(). Serial.begin(115200) no setup e mensagens úteis na serial, em português.\n2. No topo, um comentário /* O QUE FAZ: ... LIGAÇÕES: ... */ dizendo o que o firmware faz e quais pinos/sensores usa.\n3. Bibliotecas permitidas: as do núcleo (WiFi.h, WebServer.h, HTTPClient.h, WiFiClientSecure.h, Wire.h, SPI.h, Preferences.h, ESPmDNS.h, BLEDevice.h, esp_now.h...) e estas, que o servidor instala sozinho: DHT.h (DHT sensor library), PubSubClient.h, ArduinoJson.h (versão 7: JsonDocument doc;), Adafruit_SSD1306.h + Adafruit_GFX.h, Adafruit_NeoPixel.h, FastLED.h, OneWire.h + DallasTemperature.h, LiquidCrystal_I2C.h, ESP32Servo.h, Adafruit_BME280.h, WiFiManager.h, NTPClient.h.\n4. API do núcleo 3.x (a antiga NÃO compila):\n   - PWM: ledcAttach(PINO, FREQUENCIA, BITS); depois ledcWrite(PINO, duty);   (NUNCA ledcSetup nem ledcAttachPin)\n   - Timer: hw_timer_t *t = timerBegin(1000000); timerAttachInterrupt(t, &funcao); timerAlarm(t, MICROSSEGUNDOS, true, 0);\n   - Temperatura interna do chip: temperatureRead() (em °C)\n   - Servo: #include <ESP32Servo.h> (Servo.h não existe no ESP32)\n5. Declare variáveis globais, objetos e funções ANTES de usar. Com WiFi/servidor web/MQTT não use delay() longo: use millis().\n6. WiFi: credenciais em constantes no topo (const char* WIFI_SSID = "SUA_REDE"; const char* WIFI_SENHA = "SUA_SENHA";) e reconexão automática no loop.\n7. LED da placa: pino 2 (salvo outro pedido). Leitura analógica com WiFi ligado: use pinos do ADC1 (32 a 39).\n\nFORMATO DA RESPOSTA (exatamente assim, sem explicação fora do bloco):\nNOME: <nome curto do firmware>\n```cpp\n<código completo>\n```'

EXEMPLO_PEDIDO = 'uma página web para ligar e desligar o LED da placa e mostrar há quanto tempo ela está ligada'
EXEMPLO_RESPOSTA = 'NOME: LED pela Web\n```cpp\n/* O QUE FAZ: cria uma página web (http://IP-da-placa/) com botões para ligar e desligar o LED\n * e mostra há quantos segundos a placa está ligada. Reconecta o WiFi sozinho.\n * LIGAÇÕES: LED da placa no pino 2 (nada externo).\n */\n#include <WiFi.h>\n#include <WebServer.h>\n\nconst char* WIFI_SSID = "SUA_REDE";\nconst char* WIFI_SENHA = "SUA_SENHA";\nconst int LED = 2;\n\nWebServer servidor(80);\nbool ledLigado = false;\nunsigned long ultimaTentativaWifi = 0;\n\nString pagina() {\n  String h = "<!DOCTYPE html><html><head><meta charset=\'utf-8\'><meta name=\'viewport\' content=\'width=device-width\'>";\n  h += "<title>LED</title></head><body style=\'font-family:sans-serif;text-align:center\'>";\n  h += "<h1>LED " + String(ledLigado ? "LIGADO" : "DESLIGADO") + "</h1>";\n  h += "<p><a href=\'/ligar\'><button>Ligar</button></a> <a href=\'/desligar\'><button>Desligar</button></a></p>";\n  h += "<p>Ligada há " + String(millis() / 1000) + " s</p></body></html>";\n  return h;\n}\n\nvoid mostraPagina() {\n  servidor.send(200, "text/html", pagina());\n}\n\nvoid ligar() {\n  ledLigado = true;\n  digitalWrite(LED, HIGH);\n  servidor.sendHeader("Location", "/");\n  servidor.send(303);\n}\n\nvoid desligar() {\n  ledLigado = false;\n  digitalWrite(LED, LOW);\n  servidor.sendHeader("Location", "/");\n  servidor.send(303);\n}\n\nvoid conectaWifi() {\n  Serial.print("Conectando ao WiFi ");\n  Serial.println(WIFI_SSID);\n  WiFi.mode(WIFI_STA);\n  WiFi.begin(WIFI_SSID, WIFI_SENHA);\n  unsigned long inicio = millis();\n  while (WiFi.status() != WL_CONNECTED && millis() - inicio < 15000) {\n    delay(250);\n    Serial.print(".");\n  }\n  Serial.println();\n  if (WiFi.status() == WL_CONNECTED) {\n    Serial.print("Conectado! Abra http://");\n    Serial.println(WiFi.localIP());\n  } else {\n    Serial.println("Não conectou; tento de novo depois.");\n  }\n}\n\nvoid setup() {\n  Serial.begin(115200);\n  pinMode(LED, OUTPUT);\n  digitalWrite(LED, LOW);\n  conectaWifi();\n  servidor.on("/", mostraPagina);\n  servidor.on("/ligar", ligar);\n  servidor.on("/desligar", desligar);\n  servidor.begin();\n  Serial.println("Servidor web iniciado");\n}\n\nvoid loop() {\n  servidor.handleClient();\n  if (WiFi.status() != WL_CONNECTED && millis() - ultimaTentativaWifi > 10000) {\n    ultimaTentativaWifi = millis();\n    conectaWifi();\n  }\n}\n```'


def extrai_resposta(texto: str) -> tuple[str, str]:
    m = re.search(r"```(?:cpp|c\+\+|arduino|ino|c)?\s*\n(.*?)(?:```|$)", texto, re.S)
    if not m:
        raise ValueError("A IA não devolveu um bloco de código.")
    codigo = androidgen.tira_numeros(m.group(1).strip())
    if "void setup" not in codigo or "void loop" not in codigo:
        raise ValueError("O código não tem as funções setup() e loop().")
    n = re.search(r"NOME:\s*(.+)", texto)
    nome = (n.group(1).strip() if n else "Firmware IA")[:40] or "Firmware IA"
    return nome, codigo + "\n"


def _build_path(fqbn: str) -> Path:
    """Pasta de build fixa por placa: o núcleo do ESP32 compilado é reaproveitado (as correções ficam rápidas)."""
    p = config.WORK_DIR / "esp-build" / fqbn.replace(":", "_")
    p.mkdir(parents=True, exist_ok=True)
    return p


async def compila(pasta: Path, fqbn: str) -> tuple[bool, str, Path]:
    saida_dir = pasta / "out"
    cli = config.ARDUINO_CLI
    env = dict(os.environ)
    proc = await asyncio.create_subprocess_exec(
        *recursos.baixa_prioridade([cli, "compile", "--fqbn", fqbn, "--build-path", str(_build_path(fqbn)),
                                    "--output-dir", str(saida_dir), str(pasta / "sketch")]),
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


async def _compila_e_corrige(codigo: str, nome: str, pedido: str, pasta: Path, fqbn: str, nome_placa: str, placa: str,
                             emit, rodadas: int) -> tuple[bool, str, Path | None, str]:
    """Compila; instala bibliotecas e corrige o que é mecânico; chama a IA (conversa limpa) para o resto.
    Devolve (ok, código final, pasta com os .bin, relatório de erros)."""
    erros, log, anteriores, relatorio = [], "", set(), ""
    for rodada in range(rodadas + 1):
        for _auto in range(4):
            codigo, feito = gerador.normaliza_ino(codigo)
            if feito:
                await emit({"type": "status", "msg": "Ajuste automático: " + "; ".join(feito) + "."})
            await emit({"type": "code", "text": codigo})
            if pasta.exists():
                shutil.rmtree(pasta)
            (pasta / "sketch").mkdir(parents=True)
            (pasta / "sketch" / "sketch.ino").write_text(codigo)
            await emit({"type": "status", "msg": f"Compilando para {nome_placa}… (a primeira vez é mais lenta)"})
            ok, log, saida = await compila(pasta, fqbn)
            if ok:
                return True, codigo, saida, ""
            erros = gerador.erros_gcc(log)
            faltam = gerador.headers_faltando(erros)
            if not faltam:
                break
            await emit({"type": "status", "msg": "Instalando no PC as bibliotecas que o código usa: " + ", ".join(faltam) + "…"})
            if not await asyncio.to_thread(gerador.instala_bibliotecas, faltam):
                break
        relatorio = gerador.relatorio_erros(erros, codigo, lambda e, c: gerador.dica_esp32(e, c, placa), log=log)
        assinatura = gerador.assinatura_erros(erros, codigo)
        if assinatura and assinatura <= anteriores:
            relatorio += gerador.AVISO_REPETIDO
        anteriores = assinatura
        if rodada >= rodadas:
            break
        await emit({"type": "status", "msg": f"A IA está corrigindo {len(erros) or 1} erro(s) (rodada {rodada + 1} de {rodadas})…"})
        msgs = gerador.mensagens_correcao(SYSTEM_PROMPT, pedido, codigo, relatorio, "cpp", nome)
        texto = await androidgen.pede_codigo(msgs, emit, temperatura=0.4 if gerador.AVISO_REPETIDO in relatorio else 0.1)
        try:
            _n, codigo = extrai_resposta(texto)
        except ValueError:
            continue
    return False, codigo, None, relatorio


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
        if not so_compilar:
            await gerador.garante_modelo_codigo(emit)
        mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Crie: {EXEMPLO_PEDIDO}"},
            {"role": "assistant", "content": EXEMPLO_RESPOSTA},
            {"role": "user", "content": (
                f"Placa: {nome_placa}. Código ATUAL do sketch:\n```cpp\n{base_codigo[:14000]}\n```\n\n"
                f"Modifique conforme o pedido e devolva o sketch COMPLETO no mesmo formato. Pedido: {descricao or 'corrigir os erros'}")
             if base_codigo else f"Placa: {nome_placa}. Crie: {descricao}"},
        ]
        pedido = f"Placa: {nome_placa}. " + androidgen._pedido_limpo(descricao or "corrigir os erros e compilar")
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"esp-{id_}"
        pasta2 = config.WORK_DIR / f"esp-{id_}-rev"
        try:
            if so_compilar:   # sem pedido de mudança: compila o sketch como está
                nome, codigo = base_nome or "Firmware", base_codigo
                await emit({"type": "status", "msg": "Compilando o seu sketch como ele está…"})
            else:
                await emit({"type": "status", "msg": "A IA está escrevendo o código do firmware… (pode levar alguns minutos)"})
                texto = await androidgen.pede_codigo(mensagens, emit)
                try:
                    nome, codigo = extrai_resposta(texto)
                except ValueError:
                    texto = await androidgen.pede_codigo(mensagens + [{"role": "assistant", "content": texto[:3000]}, {"role": "user", "content":
                            "Responda SÓ no formato pedido: a linha NOME: e o código completo dentro de ```cpp ... ```."}], emit)
                    try:
                        nome, codigo = extrai_resposta(texto)
                    except ValueError as e:
                        await emit({"type": "error", "msg": str(e), "log": texto[-1500:]})
                        return
            ok, codigo, saida, relatorio = await _compila_e_corrige(codigo, nome, pedido, pasta, fqbn, nome_placa, placa, emit,
                                                                     config.MAX_FIX_ATTEMPTS)
            if not ok:
                await emit({"type": "error", "msg": "Não consegui compilar o firmware depois das correções.", "log": relatorio})
                return
            nota_rev = ""
            if config.REVISAO and not so_compilar:
                await emit({"type": "status", "msg": "Compilou! A IA está conferindo se o firmware faz tudo o que você pediu…"})
                faltando = gerador.confere_pedido(pedido, codigo, "cpp")
                faltando += [f for f in await gerador.revisa(pedido, codigo, "cpp", androidgen._pede_revisao) if f not in faltando]
                faltando = faltando[:4]
                if faltando:
                    await emit({"type": "status", "msg": "Faltou: " + "; ".join(faltando) + ". A IA está completando…"})
                    texto = await androidgen.pede_codigo(gerador.mensagens_melhoria(SYSTEM_PROMPT, pedido, codigo, faltando, "cpp", nome), emit)
                    try:
                        _n, codigo2 = extrai_resposta(texto)
                        ok2, codigo2, saida2, _r = await _compila_e_corrige(codigo2, nome, pedido, pasta2, fqbn, nome_placa,
                                                                            placa, emit, 2)
                    except ValueError:
                        ok2 = False
                    if ok2:
                        codigo, saida = codigo2, saida2
                        nota_rev = " A revisão completou: " + "; ".join(faltando) + "."
                    else:
                        await emit({"type": "code", "text": codigo})
                        nota_rev = " (A revisão sugeriu completar: " + "; ".join(faltando) + ", mas essa versão não compilou; entreguei a que funciona.)"
            slug = androidgen.slugify(nome)
            arquivos = []
            merged = next(iter(sorted(saida.glob("*.merged.bin"))), None)
            app = next((p for p in sorted(saida.glob("*.ino.bin"))), None)
            if merged:
                arquivos.append((merged, f"{slug}-completo.bin", "Firmware completo (.bin): gravar no endereço 0x0"))
            if app:
                arquivos.append((app, f"{slug}-app.bin", "Só o aplicativo (.bin): para atualização OTA / endereço 0x10000"))
            if not arquivos:
                await emit({"type": "error", "msg": "Compilou, mas não encontrei o arquivo .bin."})
                return
            meta = entregas.salva(id_, nome, descricao, "bin", arquivos, {"board": nome_placa, "code": codigo})
            await emit({"type": "done", "id": id_, "name": nome, "kind": "bin", "files": meta["files"],
                        "note": f"Placa: {nome_placa}. Grave o '-completo.bin' no endereço 0x0 com o esptool ou com um gravador web "
                                f"(ex.: espressif.github.io/esptool-js). Troque SUA_REDE/SUA_SENHA no código se ele usar WiFi." + nota_rev})
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
            shutil.rmtree(pasta2, ignore_errors=True)
