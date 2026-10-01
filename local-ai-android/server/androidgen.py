"""Cria aplicativos Android a partir de uma descrição.

Fluxo: a IA escreve UM arquivo (MainActivity.java) -> o servidor monta o projeto Gradle
em volta dele -> compila -> se der erro, devolve o erro à IA para corrigir (algumas vezes).

Por que um arquivo só e sem bibliotecas: o modelo local escreve poucas palavras por
segundo, então a estrutura fixa (Gradle, manifesto) vem de um modelo pronto e a IA escreve
só a lógica. A interface é montada por código (sem XML) e usa só classes do Android.
"""
import asyncio
import json
import os
import re
import shutil
import signal
import time
import uuid
from pathlib import Path

import httpx

import config
import entregas

PACKAGE = "com.localai.app"

SYSTEM_PROMPT = """Você é um programador Android experiente. Escreva UM aplicativo Android completo em UM único arquivo Java.

REGRAS OBRIGATÓRIAS:
1. Primeira linha do arquivo: package com.localai.app;
2. Classe principal: public class MainActivity extends android.app.Activity (NÃO use AppCompatActivity, NÃO use AndroidX, NÃO use Kotlin).
3. Use SOMENTE classes do Android (android.*) e do Java (java.*). Nenhuma biblioteca externa.
4. NÃO use arquivos XML nem R.layout/R.id. Monte toda a interface por código Java (LinearLayout, TextView, Button, EditText, ScrollView, ListView com ArrayAdapter, etc.) e chame setContentView(view).
5. Inclua TODOS os imports necessários. O código precisa compilar na primeira tentativa.
6. Converta dp para pixels com um método auxiliar (ex.: (int) (valor * getResources().getDisplayMetrics().density)).
7. Rede (se precisar): HttpURLConnection dentro de uma Thread e atualize a tela com runOnUiThread.
8. Todos os textos da interface em português do Brasil.
9. Classes auxiliares podem existir no mesmo arquivo (sem "public").

FORMATO DA RESPOSTA (siga exatamente, sem explicações):
NOME: <nome curto do app>
```java
<código completo>
```"""

EXEMPLO_PEDIDO = "um contador com botões de mais e menos"
EXEMPLO_RESPOSTA = """NOME: Contador
```java
package com.localai.app;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {
    private int valor = 0;

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LinearLayout raiz = new LinearLayout(this);
        raiz.setOrientation(LinearLayout.VERTICAL);
        raiz.setGravity(Gravity.CENTER);
        raiz.setPadding(dp(24), dp(24), dp(24), dp(24));

        final TextView texto = new TextView(this);
        texto.setTextSize(48);
        texto.setGravity(Gravity.CENTER);
        texto.setText("0");

        Button mais = new Button(this);
        mais.setText("+1");
        mais.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                valor++;
                texto.setText(String.valueOf(valor));
            }
        });

        Button menos = new Button(this);
        menos.setText("-1");
        menos.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                valor--;
                texto.setText(String.valueOf(valor));
            }
        });

        raiz.addView(texto);
        raiz.addView(mais);
        raiz.addView(menos);
        setContentView(raiz);
    }
}
```"""

# ------------------------------------------------------------------ projeto Gradle
SETTINGS_GRADLE = """pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}
rootProject.name = "app"
include ':app'
"""

ROOT_BUILD_GRADLE = """plugins {
    id 'com.android.application' version '8.5.2' apply false
}
"""

GRADLE_PROPERTIES = """org.gradle.jvmargs=-Xmx2g -Dfile.encoding=UTF-8
android.useAndroidX=false
android.nonTransitiveRClass=true
"""

APP_BUILD_GRADLE = """plugins {
    id 'com.android.application'
}

android {
    namespace 'com.localai.app'
    compileSdk 34

    defaultConfig {
        applicationId "%(app_id)s"
        minSdk 24
        targetSdk 34
        versionCode 1
        versionName "1.0"
    }

    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }

    lint {
        abortOnError false
        checkReleaseBuilds false
    }
}
"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET" />
    <application
        android:label="%(label)s"
        android:allowBackup="true"
        android:usesCleartextTraffic="true"
        android:theme="@android:style/Theme.Material.Light.DarkActionBar">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""


def slugify(nome: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "", s)
    return (s or "app")[:16]


def xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;").replace("'", "&apos;"))


def escreve_projeto(pasta: Path, nome: str, app_id: str, codigo: str) -> None:
    (pasta / "app/src/main/java/com/localai/app").mkdir(parents=True, exist_ok=True)
    (pasta / "settings.gradle").write_text(SETTINGS_GRADLE)
    (pasta / "build.gradle").write_text(ROOT_BUILD_GRADLE)
    (pasta / "gradle.properties").write_text(GRADLE_PROPERTIES)
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    (pasta / "local.properties").write_text(f"sdk.dir={sdk}\n")
    (pasta / "app/build.gradle").write_text(APP_BUILD_GRADLE % {"app_id": app_id})
    (pasta / "app/src/main/AndroidManifest.xml").write_text(MANIFEST % {"label": xml_escape(nome)})
    (pasta / "app/src/main/java/com/localai/app/MainActivity.java").write_text(codigo)


# ------------------------------------------------------------------ resposta da IA
def extrai_resposta(texto: str) -> tuple[str, str]:
    """Devolve (nome, codigo_java). Levanta ValueError se não achar código utilizável."""
    m = re.search(r"```(?:java)?\s*\n(.*?)(?:```|$)", texto, re.S)
    if not m:
        raise ValueError("A IA não devolveu um bloco de código.")
    codigo = m.group(1).strip()
    if "class MainActivity" not in codigo:
        raise ValueError("O código não define a classe MainActivity.")
    if not re.match(r"\s*package\s+com\.localai\.app\s*;", codigo):
        codigo = re.sub(r"^\s*package\s+[\w.]+\s*;\s*", "", codigo)
        codigo = f"package {PACKAGE};\n\n" + codigo
    n = re.search(r"NOME:\s*(.+)", texto)
    nome = (n.group(1).strip() if n else "App IA")[:40] or "App IA"
    return nome, codigo + "\n"


def resumo_erros(log: str, limite: int = 1600) -> str:
    """Pega do log do Gradle só o que ajuda a corrigir: erros do javac (com contexto)."""
    linhas = log.splitlines()
    blocos = []
    for i, ln in enumerate(linhas):
        if ": error:" in ln or re.search(r"error: ", ln):
            blocos.append("\n".join(linhas[i:i + 4]))
    if blocos:
        saida = "\n".join(blocos)
    else:
        m = re.search(r"\* What went wrong:\n(.*?)(?:\n\* |\Z)", log, re.S)
        saida = m.group(1) if m else log[-limite:]
    return saida[:limite]


# ------------------------------------------------------------------ chamadas externas
async def pede_codigo(messages: list[dict], emit) -> str:
    """Chama o llama-server em streaming e junta o texto, avisando o progresso."""
    payload = {"messages": messages, "stream": True, "temperature": 0.2, "top_p": 0.9,
               "cache_prompt": True,  # o exemplo e as regras são sempre iguais: reaproveita o cálculo
               "max_tokens": config.GEN_MAX_TOKENS}
    texto, n, ultimo = "", 0, time.time()
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            async with client.stream("POST", f"{config.LLAMA_URL}/v1/chat/completions", json=payload) as r:
                if r.status_code == 503:
                    raise RuntimeError("O modelo ainda está carregando na memória. Espere um pouco e tente de novo.")
                if r.status_code != 200:
                    raise RuntimeError(f"O modelo respondeu com erro {r.status_code}.")
                async for linha in r.aiter_lines():
                    if not linha.startswith("data:"):
                        continue
                    dado = linha[5:].strip()
                    if not dado or dado == "[DONE]":
                        continue
                    pedaco = json.loads(dado)["choices"][0]["delta"].get("content") or ""
                    texto += pedaco
                    n += 1
                    if time.time() - ultimo > 2:
                        ultimo = time.time()
                        await emit({"type": "progress", "tokens": n})
        except httpx.ConnectError:
            raise RuntimeError("O modelo (llama-server) está desligado ou reiniciando.")
        except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ReadTimeout):
            raise RuntimeError("O modelo parou no meio (provável falta de memória da placa de vídeo).")
    return texto


async def compila(pasta: Path) -> tuple[bool, str, Path | None]:
    gradle = config.GRADLE_BIN
    env = dict(os.environ)
    sdk = env.get("ANDROID_HOME") or env.get("ANDROID_SDK_ROOT")
    if not sdk or not Path(sdk).exists():
        return False, "Android SDK não encontrado (ANDROID_HOME). Rode o instalador.", None
    env["ANDROID_HOME"] = sdk
    # start_new_session: o Gradle e os processos que ele cria ficam num grupo próprio,
    # para podermos matar tudo de uma vez ao cancelar (senão sobra Java rodando escondido)
    proc = await asyncio.create_subprocess_exec(
        gradle, "assembleDebug", "--no-daemon", "--console=plain", "-q",
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
        return False, "A compilação passou do tempo limite.", None
    except asyncio.CancelledError:  # o usuário clicou em Parar
        mata_tudo()
        raise
    log = saida.decode(errors="replace")
    if proc.returncode != 0:
        return False, log, None
    apks = sorted(pasta.rglob("*-debug.apk"))
    if not apks:
        return False, log + "\nCompilou, mas nenhum APK foi encontrado.", None
    return True, log, apks[0]


# ------------------------------------------------------------------ fluxo principal
_trava = asyncio.Lock()


async def gera_app(descricao: str, emit) -> None:
    """Executa o fluxo completo, mandando eventos por emit(dict)."""
    if _trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outro app. Espere ele terminar ou clique em Parar."})
        return
    async with _trava:
        mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Crie: {EXEMPLO_PEDIDO}"},
            {"role": "assistant", "content": EXEMPLO_RESPOSTA},
            {"role": "user", "content": f"Crie: {descricao}"},
        ]
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"app-{id_}"
        try:
            for tentativa in range(config.MAX_FIX_ATTEMPTS + 1):
                if tentativa == 0:
                    await emit({"type": "status", "msg": "A IA está escrevendo o código do app… (pode levar alguns minutos)"})
                else:
                    await emit({"type": "status",
                                "msg": f"Deu erro ao compilar. A IA está corrigindo (tentativa {tentativa} de {config.MAX_FIX_ATTEMPTS})…"})
                texto = await pede_codigo(mensagens, emit)
                try:
                    nome, codigo = extrai_resposta(texto)
                except ValueError as e:
                    await emit({"type": "error", "msg": str(e), "log": texto[-1500:]})
                    return
                await emit({"type": "code", "text": codigo})

                if pasta.exists():
                    shutil.rmtree(pasta)
                app_id = f"com.localai.{slugify(nome)}{id_[:4]}"
                escreve_projeto(pasta, nome, app_id, codigo)
                await emit({"type": "status", "msg": "Compilando o app… (a primeira vez baixa as ferramentas e demora mais)"})
                ok, log, apk = await compila(pasta)
                if ok:
                    meta = entregas.salva(
                        id_, nome, descricao, "apk",
                        [(apk, f"{slugify(nome)}.apk", "App Android (.apk)")],
                        {"code": codigo, "app_id": app_id})
                    shutil.rmtree(pasta, ignore_errors=True)
                    await emit({"type": "done", "id": id_, "name": nome, "kind": "apk", "files": meta["files"],
                                "note": "Passe o arquivo para o celular e abra-o para instalar. Se o Android pedir, permita instalar de fontes desconhecidas."})
                    return
                erros = resumo_erros(log)
                if tentativa >= config.MAX_FIX_ATTEMPTS:
                    await emit({"type": "error", "msg": "Não consegui compilar o app depois das correções.", "log": erros})
                    return
                mensagens += [
                    {"role": "assistant", "content": texto},
                    {"role": "user", "content": (
                        "O código NÃO compilou. ERROS DE COMPILAÇÃO:\n" + erros +
                        "\n\nCorrija e devolva o arquivo COMPLETO no mesmo formato (NOME: e bloco ```java).")},
                ]
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
