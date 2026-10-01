"""Programar projetos com VÁRIOS arquivos: app Android avançado, página Web e script Python.

Diferente do modo simples (um arquivo só), aqui a IA devolve vários arquivos no formato

    NOME: <nome>
    ### caminho/do/arquivo.ext
    ```linguagem
    conteúdo
    ```

O servidor valida os caminhos, monta o projeto, confere/compila e devolve os arquivos prontos.
Para MODIFICAR algo que já existe, a IA recebe os arquivos atuais e devolve só o que mudou.
"""
import asyncio
import re
import shutil
import sys
import uuid
import zipfile
from pathlib import Path

import androidgen
import config
import entregas
import recursos

ARQ_RE = re.compile(r"^###\s*(?:ARQUIVO:\s*)?`?([^\s`]+)`?\s*\n```[^\n]*\n(.*?)\n```", re.S | re.M)
LIMITE_ARQ = 60_000
MAX_ARQUIVOS = 20


def extrai_arquivos(texto: str) -> dict[str, str]:
    arqs: dict[str, str] = {}
    for caminho, conteudo in ARQ_RE.findall(texto):
        arqs[caminho.strip().lstrip("./")] = conteudo.rstrip() + "\n"
    return arqs


def extrai_nome(texto: str, padrao: str) -> str:
    m = re.search(r"NOME:\s*(.+)", texto)
    return ((m.group(1).strip() if m else padrao)[:40]) or padrao


def caminho_seguro(c: str) -> bool:
    p = Path(c)
    return not p.is_absolute() and ".." not in p.parts and len(c) < 160 and bool(re.fullmatch(r"[\w./\- ]+", c))


def formata_base(arqs: dict[str, str], limite: int = 12_000) -> str:
    """Mostra os arquivos atuais à IA (cortando se for muito grande)."""
    out, usado = [], 0
    for c, t in arqs.items():
        trecho = t if usado + len(t) <= limite else t[: max(0, limite - usado)] + "\n…(cortado)"
        out.append(f"### {c}\n```\n{trecho.rstrip()}\n```")
        usado += len(t)
        if usado >= limite:
            break
    return "\n".join(out)


async def _compila_comando(cmd: list[str], pasta: Path) -> tuple[bool, str]:
    proc = await asyncio.create_subprocess_exec(
        *recursos.baixa_prioridade(cmd), cwd=pasta, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    try:
        saida, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        import os
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise
    return proc.returncode == 0, saida.decode(errors="replace")


def zipa(pasta: Path, arquivos: dict[str, str], destino: Path) -> Path:
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for c, t in arquivos.items():
            z.writestr(c, t)
    return destino


# =============================================================================== Android avançado
SYS_ANDROID = """Você é um programador Android sênior. Crie um aplicativo Android completo com VÁRIOS arquivos.

REGRAS OBRIGATÓRIAS:
1. Linguagem: Java (NÃO use Kotlin). Pacote: com.localai.app.
2. Já estão no projeto: AppCompat, Material, ConstraintLayout, RecyclerView, CardView, ViewPager2 e SwipeRefreshLayout. NENHUMA outra biblioteca.
3. A tela principal é com.localai.app.MainActivity (extends AppCompatActivity), com layout em app/src/main/res/layout/activity_main.xml. Pode criar outras Activities, Fragments e classes (arquivos .java), layouts, e res/values (strings.xml, colors.xml, themes.xml) e res/drawable (somente XML).
4. Se criar OUTRAS Activities, envie TAMBÉM o app/src/main/AndroidManifest.xml completo declarando todas (MainActivity com o filtro MAIN/LAUNCHER). Se não precisar, NÃO envie o manifesto.
5. Tema do app: @style/Theme.AppCompat.Light.DarkActionBar (ou um tema Material, se usar Material).
6. Todos os IDs usados no código (R.id.x) devem existir nos layouts. Inclua TODOS os imports. O projeto precisa compilar na primeira tentativa.
7. Sem imagens binárias (use cores e drawables XML). Textos em português do Brasil. Permissão de internet já existe.
8. Todo caminho começa com app/src/main/.

FORMATO (siga exatamente, sem explicações):
NOME: <nome curto do app>
### app/src/main/java/com/localai/app/MainActivity.java
```java
<código>
```
### app/src/main/res/layout/activity_main.xml
```xml
<layout>
```
(um bloco ### + código para cada arquivo)"""

EX_PEDIDO_AND = "um contador com botões de mais e menos"
EX_RESP_AND = """NOME: Contador
### app/src/main/java/com/localai/app/MainActivity.java
```java
package com.localai.app;

import android.os.Bundle;
import android.widget.Button;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

public class MainActivity extends AppCompatActivity {
    private int valor = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_main);
        final TextView texto = findViewById(R.id.texto);
        Button mais = findViewById(R.id.mais);
        Button menos = findViewById(R.id.menos);
        mais.setOnClickListener(v -> texto.setText(String.valueOf(++valor)));
        menos.setOnClickListener(v -> texto.setText(String.valueOf(--valor)));
    }
}
```
### app/src/main/res/layout/activity_main.xml
```xml
<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent"
    android:gravity="center"
    android:orientation="vertical"
    android:padding="24dp">

    <TextView
        android:id="@+id/texto"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="0"
        android:textSize="48sp" />

    <Button
        android:id="@+id/mais"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="+1" />

    <Button
        android:id="@+id/menos"
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="-1" />
</LinearLayout>
```"""

APP_BUILD_AVANCADO = """plugins {
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

dependencies {
    implementation 'androidx.appcompat:appcompat:1.6.1'
    implementation 'com.google.android.material:material:1.11.0'
    implementation 'androidx.constraintlayout:constraintlayout:2.1.4'
    implementation 'androidx.recyclerview:recyclerview:1.3.2'
    implementation 'androidx.cardview:cardview:1.0.0'
    implementation 'androidx.viewpager2:viewpager2:1.0.0'
    implementation 'androidx.swiperefreshlayout:swiperefreshlayout:1.1.0'
}
"""

GRADLE_PROPS_AVANCADO = """org.gradle.jvmargs=-Xmx1536m -Dfile.encoding=UTF-8
org.gradle.workers.max=2
android.useAndroidX=true
android.nonTransitiveRClass=true
"""

MANIFEST_AVANCADO = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <uses-permission android:name="android.permission.INTERNET" />
    <application
        android:label="%(label)s"
        android:allowBackup="true"
        android:usesCleartextTraffic="true"
        android:theme="@style/Theme.AppCompat.Light.DarkActionBar">
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


def valida_android(arqs: dict[str, str]) -> str | None:
    """Devolve uma mensagem de erro se os arquivos do app não forem aceitáveis."""
    if not arqs:
        return "A IA não devolveu nenhum arquivo no formato pedido."
    if len(arqs) > MAX_ARQUIVOS:
        return f"Arquivos demais ({len(arqs)}). O máximo é {MAX_ARQUIVOS}."
    for c, t in arqs.items():
        if not caminho_seguro(c) or not c.startswith("app/src/main/"):
            return f"Caminho não permitido: {c}"
        if not (c.endswith(".java") or c.endswith(".xml")):
            return f"Tipo de arquivo não permitido: {c} (só .java e .xml)"
        if len(t) > LIMITE_ARQ:
            return f"Arquivo grande demais: {c}"
    return None


def escreve_projeto_avancado(pasta: Path, nome: str, app_id: str, arqs: dict[str, str]) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "settings.gradle").write_text(androidgen.SETTINGS_GRADLE)
    (pasta / "build.gradle").write_text(androidgen.ROOT_BUILD_GRADLE)
    (pasta / "gradle.properties").write_text(GRADLE_PROPS_AVANCADO)
    import os
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""
    (pasta / "local.properties").write_text(f"sdk.dir={sdk}\n")
    (pasta / "app").mkdir(exist_ok=True)
    (pasta / "app/build.gradle").write_text(APP_BUILD_AVANCADO % {"app_id": app_id})
    todos = dict(arqs)
    todos.setdefault("app/src/main/AndroidManifest.xml", MANIFEST_AVANCADO % {"label": androidgen.xml_escape(nome)})
    for c, t in todos.items():
        f = pasta / c
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(t)


async def gera_app_avancado(descricao: str, base: dict[str, str] | None, base_nome: str | None, emit) -> None:
    if androidgen._trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outra coisa. Espere terminar ou clique em Parar."})
        return
    async with androidgen._trava:
        try:
            recursos.garante_ram(2500, "compilar o app")
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
            return
        if base:
            pedido = (f"Estes são os arquivos ATUAIS do app:\n{formata_base(base)}\n\n"
                      f"Modifique o app conforme o pedido e devolva SOMENTE os arquivos que mudaram ou são novos, "
                      f"cada um COMPLETO (não use reticências). Pedido: {descricao}")
        else:
            pedido = f"Crie: {descricao}"
        mensagens = [{"role": "system", "content": SYS_ANDROID},
                     {"role": "user", "content": f"Crie: {EX_PEDIDO_AND}"},
                     {"role": "assistant", "content": EX_RESP_AND},
                     {"role": "user", "content": pedido}]
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"appx-{id_}"
        try:
            for tentativa in range(config.MAX_FIX_ATTEMPTS + 1):
                if tentativa == 0:
                    await emit({"type": "status", "msg": "A IA está escrevendo os arquivos do app… (pode levar vários minutos)"})
                else:
                    await emit({"type": "status", "msg": f"Deu erro ao compilar. A IA está corrigindo (tentativa {tentativa} de {config.MAX_FIX_ATTEMPTS})…"})
                texto = await androidgen.pede_codigo(mensagens, emit)
                novos = extrai_arquivos(texto)
                erro = valida_android(novos)
                if erro:
                    await emit({"type": "error", "msg": erro, "log": texto[-1500:]})
                    return
                arqs = {**(base or {}), **novos}
                if not any(c.endswith("MainActivity.java") for c in arqs):
                    await emit({"type": "error", "msg": "Faltou a MainActivity.java.", "log": texto[-800:]})
                    return
                nome = extrai_nome(texto, base_nome or "App IA")
                await emit({"type": "code", "text": "\n\n".join(f"// {c}\n{t}" for c, t in novos.items())})
                if pasta.exists():
                    shutil.rmtree(pasta)
                app_id = f"com.localai.{androidgen.slugify(nome)}{id_[:4]}"
                escreve_projeto_avancado(pasta, nome, app_id, arqs)
                await emit({"type": "status", "msg": "Compilando o app… (a primeira vez baixa as bibliotecas e demora mais)"})
                ok, log, apk = await androidgen.compila(pasta)
                if ok:
                    meta = entregas.salva(id_, nome, descricao, "apk",
                                          [(apk, f"{androidgen.slugify(nome)}.apk", "App Android (.apk)")],
                                          {"app_id": app_id, "modo": "avancado", "arquivos_fonte": arqs})
                    await emit({"type": "done", "id": id_, "name": nome, "kind": "apk", "files": meta["files"],
                                "note": "Passe o arquivo para o celular e abra-o para instalar. Para mudar o app, use ✏ Modificar."})
                    return
                erros = androidgen.resumo_erros(log)
                if tentativa >= config.MAX_FIX_ATTEMPTS:
                    await emit({"type": "error", "msg": "Não consegui compilar o app depois das correções.", "log": erros})
                    return
                mensagens += [{"role": "assistant", "content": texto},
                              {"role": "user", "content": (
                                  "O código NÃO compilou. ERROS DE COMPILAÇÃO:\n" + erros +
                                  "\n\nCorrija e devolva SOMENTE os arquivos que precisam mudar, completos, no mesmo formato.")}]
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)


# =============================================================================== Web e Python
SYS_WEB = """Você é um desenvolvedor web experiente. Crie uma página/aplicação web completa.

REGRAS:
1. O arquivo principal é index.html, com HTML, CSS e JavaScript. Pode enviar também outros arquivos (.css, .js) se ajudar.
2. NÃO use bibliotecas externas, CDNs nem imagens da internet: tudo precisa funcionar offline, abrindo o index.html.
3. Layout responsivo (funciona no celular), visual limpo e moderno. Textos em português do Brasil.
4. O código precisa funcionar na primeira tentativa.

FORMATO (siga exatamente, sem explicações):
NOME: <nome curto>
### index.html
```html
<código>
```
(um bloco ### + código para cada arquivo)"""

SYS_PY = """Você é um programador Python experiente. Crie o programa pedido.

REGRAS:
1. Python 3. Use apenas a biblioteca padrão, a menos que o usuário peça outra biblioteca.
2. O arquivo principal é main.py. Pode enviar outros arquivos .py se ajudar.
3. O código precisa rodar na primeira tentativa. Comentários e mensagens em português do Brasil.

FORMATO (siga exatamente, sem explicações):
NOME: <nome curto>
### main.py
```python
<código>
```
(um bloco ### + código para cada arquivo)"""

EX_WEB = ("um contador de cliques",
          "NOME: Contador\n### index.html\n```html\n<!doctype html>\n<html lang=\"pt-BR\">\n<head>\n<meta charset=\"utf-8\">\n"
          "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>Contador</title>\n"
          "<style>body{font-family:sans-serif;text-align:center;padding:40px}button{font-size:24px;padding:12px 24px}</style>\n"
          "</head>\n<body>\n<h1 id=\"n\">0</h1>\n<button onclick=\"n.textContent=++c\">+1</button>\n"
          "<script>let c=0;</script>\n</body>\n</html>\n```")
EX_PY = ("somar dois números digitados",
         "NOME: Somador\n### main.py\n```python\ndef main():\n    a = float(input(\"Primeiro número: \"))\n"
         "    b = float(input(\"Segundo número: \"))\n    print(f\"Soma: {a + b}\")\n\n\nif __name__ == \"__main__\":\n    main()\n```")

ALVOS = {
    "web": dict(sys=SYS_WEB, ex=EX_WEB, rotulo="página web", principal="index.html", icone="web"),
    "python": dict(sys=SYS_PY, ex=EX_PY, rotulo="programa Python", principal="main.py", icone="python"),
}


async def confere_python(pasta: Path, arqs: dict[str, str]) -> str:
    erros = []
    for c in arqs:
        if c.endswith(".py"):
            ok, saida = await _compila_comando([sys.executable, "-m", "py_compile", c], pasta)
            if not ok:
                erros.append(saida.strip()[-600:])
    return "\n".join(erros)


async def gera_codigo(alvo: str, descricao: str, base: dict[str, str] | None, base_nome: str | None, emit) -> None:
    a = ALVOS.get(alvo)
    if not a:
        await emit({"type": "error", "msg": "Tipo de projeto desconhecido."})
        return
    if androidgen._trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outra coisa. Espere terminar ou clique em Parar."})
        return
    async with androidgen._trava:
        if base:
            pedido = (f"Estes são os arquivos ATUAIS:\n{formata_base(base)}\n\nModifique conforme o pedido e devolva "
                      f"SOMENTE os arquivos que mudaram ou são novos, cada um COMPLETO. Pedido: {descricao}")
        else:
            pedido = f"Crie: {descricao}"
        mensagens = [{"role": "system", "content": a["sys"]}, {"role": "user", "content": f"Crie: {a['ex'][0]}"},
                     {"role": "assistant", "content": a["ex"][1]}, {"role": "user", "content": pedido}]
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"cod-{id_}"
        try:
            for tentativa in range(config.MAX_FIX_ATTEMPTS + 1):
                await emit({"type": "status", "msg": (f"A IA está escrevendo o {a['rotulo']}…" if tentativa == 0 else
                            f"Achei um erro. A IA está corrigindo (tentativa {tentativa} de {config.MAX_FIX_ATTEMPTS})…")})
                texto = await androidgen.pede_codigo(mensagens, emit)
                novos = extrai_arquivos(texto)
                if not novos or not all(caminho_seguro(c) for c in novos) or len(novos) > MAX_ARQUIVOS:
                    await emit({"type": "error", "msg": "A IA não devolveu os arquivos no formato pedido.", "log": texto[-1200:]})
                    return
                arqs = {**(base or {}), **novos}
                if a["principal"] not in arqs:
                    await emit({"type": "error", "msg": f"Faltou o arquivo {a['principal']}.", "log": texto[-800:]})
                    return
                nome = extrai_nome(texto, base_nome or a["rotulo"].capitalize())
                await emit({"type": "code", "text": "\n\n".join(f"// {c}\n{t}" for c, t in novos.items())})
                erros = ""
                if alvo == "python":
                    await emit({"type": "status", "msg": "Conferindo se o código Python é válido…"})
                    if pasta.exists():
                        shutil.rmtree(pasta)
                    for c, t in arqs.items():
                        (pasta / c).parent.mkdir(parents=True, exist_ok=True)
                        (pasta / c).write_text(t)
                    erros = await confere_python(pasta, arqs)
                if erros:
                    if tentativa >= config.MAX_FIX_ATTEMPTS:
                        await emit({"type": "error", "msg": "O código continua com erros de sintaxe.", "log": erros})
                        return
                    mensagens += [{"role": "assistant", "content": texto},
                                  {"role": "user", "content": "O código tem erros:\n" + erros +
                                   "\n\nCorrija e devolva SOMENTE os arquivos que precisam mudar, completos, no mesmo formato."}]
                    continue
                slug = androidgen.slugify(nome)
                pasta.mkdir(parents=True, exist_ok=True)
                saidas = []
                for c, t in arqs.items():
                    dest = pasta / "saida" / c.replace("/", "_")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(t)
                    saidas.append((dest, c.replace("/", "_"), f"Arquivo {c}"))
                zip_dest = zipa(pasta, arqs, pasta / f"{slug}.zip")
                saidas.append((zip_dest, f"{slug}.zip", "Projeto completo (.zip)"))
                meta = entregas.salva(id_, nome, descricao, alvo, saidas, {"arquivos_fonte": arqs})
                nota = ("Abra o index.html no navegador (funciona offline)." if alvo == "web"
                        else "Rode com: python3 main.py")
                await emit({"type": "done", "id": id_, "name": nome, "kind": alvo, "files": meta["files"],
                            "note": nota + " Para mudar, use ✏ Modificar."})
                return
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
