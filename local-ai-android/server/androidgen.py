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
import gerador
import entregas
import recursos

PACKAGE = "com.localai.app"

SYSTEM_PROMPT = 'Você é um desenvolvedor Android sênior. Escreva UM aplicativo Android COMPLETO, funcional e bonito em UM único arquivo Java.\n\nO projeto Gradle já existe; você escreve só o MainActivity.java. REGRAS:\n1. Primeira linha: package com.localai.app;\n2. public class MainActivity extends Activity (android.app.Activity). NÃO use AppCompatActivity, AndroidX, Kotlin, Compose nem bibliotecas externas.\n3. Use só android.*, java.* e org.json. NÃO existem layouts XML nem R.layout/R.id/R.drawable: monte a interface toda por código (LinearLayout, ScrollView, TextView, EditText, Button, ListView + ArrayAdapter...) e chame setContentView(...).\n4. Coloque TODOS os imports (inclusive android.view.View e cada android.widget usado).\n5. Logo depois do package, escreva um comentário /* O QUE O APP FAZ: ... */ listando em tópicos as funções que você vai implementar. Depois implemente TODAS de verdade (nada de "TODO" ou funções vazias).\n6. O que o usuário cria (listas, notas, configurações, pontuações) deve ser SALVO com SharedPreferences (JSON com org.json) e carregado no onCreate.\n7. Variáveis usadas dentro de listeners/lambdas e que mudam: declare como CAMPOS da classe (private), não como variáveis locais.\n8. Rede: HttpURLConnection dentro de new Thread(...) e atualize a tela com runOnUiThread(...). Repetição/tempo: android.os.Handler + postDelayed (não use AsyncTask nem Timer para mexer na tela).\n9. Visual caprichado: margens com um método dp(int), fonte legível, cores com Color.parseColor("#..."), botões com GradientDrawable arredondado.\n10. Textos da interface em português do Brasil. Diálogos: android.app.AlertDialog.Builder. Avisos curtos: Toast.\n\nFORMATO DA RESPOSTA (exatamente assim, sem explicação fora do bloco):\nNOME: <nome curto do app>\n```java\n<código completo>\n```'

EXEMPLO_PEDIDO = 'um conversor de temperatura de Celsius para Fahrenheit que guarda o histórico'
EXEMPLO_RESPOSTA = 'NOME: Conversor de Temperatura\n```java\npackage com.localai.app;\n\n/* O QUE O APP FAZ:\n * - Campo para digitar a temperatura em Celsius e botão Converter\n * - Mostra o resultado em Fahrenheit\n * - Guarda o histórico das conversões (salvo no celular) e mostra numa lista\n * - Botão para limpar o histórico\n */\n\nimport android.app.Activity;\nimport android.app.AlertDialog;\nimport android.content.SharedPreferences;\nimport android.graphics.Color;\nimport android.graphics.drawable.GradientDrawable;\nimport android.os.Bundle;\nimport android.text.InputType;\nimport android.view.Gravity;\nimport android.view.View;\nimport android.widget.ArrayAdapter;\nimport android.widget.Button;\nimport android.widget.EditText;\nimport android.widget.LinearLayout;\nimport android.widget.ListView;\nimport android.widget.TextView;\nimport android.widget.Toast;\n\nimport org.json.JSONArray;\n\nimport java.util.ArrayList;\nimport java.util.Locale;\n\npublic class MainActivity extends Activity {\n    private final ArrayList<String> historico = new ArrayList<>();\n    private ArrayAdapter<String> adaptador;\n    private EditText campo;\n    private TextView resultado;\n    private SharedPreferences prefs;\n\n    private int dp(int v) {\n        return (int) (v * getResources().getDisplayMetrics().density);\n    }\n\n    private Button botao(String texto, String cor) {\n        Button b = new Button(this);\n        b.setText(texto);\n        b.setTextColor(Color.WHITE);\n        b.setAllCaps(false);\n        GradientDrawable fundo = new GradientDrawable();\n        fundo.setColor(Color.parseColor(cor));\n        fundo.setCornerRadius(dp(12));\n        b.setBackground(fundo);\n        return b;\n    }\n\n    @Override\n    protected void onCreate(Bundle savedInstanceState) {\n        super.onCreate(savedInstanceState);\n        prefs = getSharedPreferences("dados", MODE_PRIVATE);\n\n        LinearLayout raiz = new LinearLayout(this);\n        raiz.setOrientation(LinearLayout.VERTICAL);\n        raiz.setPadding(dp(20), dp(20), dp(20), dp(20));\n        raiz.setBackgroundColor(Color.parseColor("#F4F6FA"));\n\n        TextView titulo = new TextView(this);\n        titulo.setText("Celsius → Fahrenheit");\n        titulo.setTextSize(24);\n        titulo.setTextColor(Color.parseColor("#1F2A44"));\n        raiz.addView(titulo);\n\n        campo = new EditText(this);\n        campo.setHint("Temperatura em °C");\n        campo.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL | InputType.TYPE_NUMBER_FLAG_SIGNED);\n        raiz.addView(campo);\n\n        Button converter = botao("Converter", "#2E7D32");\n        converter.setOnClickListener(v -> converte());\n        raiz.addView(converter);\n\n        resultado = new TextView(this);\n        resultado.setTextSize(28);\n        resultado.setGravity(Gravity.CENTER);\n        resultado.setPadding(0, dp(16), 0, dp(16));\n        raiz.addView(resultado);\n\n        Button limpar = botao("Limpar histórico", "#C62828");\n        limpar.setOnClickListener(v -> new AlertDialog.Builder(this)\n                .setTitle("Limpar histórico?")\n                .setPositiveButton("Sim", (d, w) -> {\n                    historico.clear();\n                    adaptador.notifyDataSetChanged();\n                    salva();\n                })\n                .setNegativeButton("Não", null)\n                .show());\n        raiz.addView(limpar);\n\n        ListView lista = new ListView(this);\n        adaptador = new ArrayAdapter<>(this, android.R.layout.simple_list_item_1, historico);\n        lista.setAdapter(adaptador);\n        raiz.addView(lista, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));\n\n        carrega();\n        setContentView(raiz);\n    }\n\n    private void converte() {\n        String texto = campo.getText().toString().trim().replace(\',\', \'.\');\n        if (texto.isEmpty()) {\n            Toast.makeText(this, "Digite uma temperatura", Toast.LENGTH_SHORT).show();\n            return;\n        }\n        try {\n            double c = Double.parseDouble(texto);\n            double f = c * 9 / 5 + 32;\n            String linha = String.format(Locale.US, "%.1f °C = %.1f °F", c, f);\n            resultado.setText(linha);\n            historico.add(0, linha);\n            adaptador.notifyDataSetChanged();\n            salva();\n        } catch (NumberFormatException e) {\n            Toast.makeText(this, "Número inválido", Toast.LENGTH_SHORT).show();\n        }\n    }\n\n    private void salva() {\n        prefs.edit().putString("historico", new JSONArray(historico).toString()).apply();\n    }\n\n    private void carrega() {\n        try {\n            JSONArray a = new JSONArray(prefs.getString("historico", "[]"));\n            for (int i = 0; i < a.length(); i++) {\n                historico.add(a.getString(i));\n            }\n            adaptador.notifyDataSetChanged();\n        } catch (Exception e) {\n            historico.clear();\n        }\n    }\n}\n```'

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

GRADLE_PROPERTIES = """org.gradle.jvmargs=-Xmx1536m -Dfile.encoding=UTF-8
org.gradle.daemon.idletimeout=900000
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
    codigo = tira_numeros(m.group(1).strip())
    if "class MainActivity" not in codigo:
        raise ValueError("O código não define a classe MainActivity.")
    if not re.match(r"\s*package\s+com\.localai\.app\s*;", codigo):
        codigo = re.sub(r"^\s*package\s+[\w.]+\s*;\s*", "", codigo)
        codigo = f"package {PACKAGE};\n\n" + codigo
    n = re.search(r"NOME:\s*(.+)", texto)
    nome = (n.group(1).strip() if n else "App IA")[:40] or "App IA"
    return nome, codigo + "\n"


def tira_numeros(codigo: str) -> str:
    """Se a IA devolveu o código com os números de linha da correção ("  12| ..."), tira os números."""
    ls = codigo.split("\n")
    if ls and sum(1 for ln in ls if re.match(r"^\s*\d+\|", ln)) > len(ls) * 0.6:
        return "\n".join(re.sub(r"^\s*\d+\|\s?", "", ln) for ln in ls)
    return codigo


def cortado(texto: str) -> bool:
    """A resposta acabou no meio do código (o modelo atingiu o limite de palavras)?"""
    m = re.search(r"```\w*\s*\n(.*)", texto, re.S)
    if not m or "```" in m.group(1):
        return False
    corpo = gerador._sem_comentarios_e_textos(m.group(1))
    return corpo.count("{") > corpo.count("}")


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
async def _uma_chamada(messages: list[dict], emit, temperatura: float, max_tokens: int) -> str:
    payload = {"messages": messages, "stream": True, "temperature": temperatura, "top_p": 0.9,
               "cache_prompt": True,  # as regras e o exemplo são sempre iguais: reaproveita o cálculo
               "max_tokens": max_tokens}
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


async def pede_codigo(messages: list[dict], emit, temperatura: float = 0.2) -> str:
    """Chama o llama-server em streaming. Se a resposta for cortada no meio do código, pede para continuar."""
    tamanho = sum(len(m["content"]) for m in messages) // 3      # ~3 letras por "palavra" do modelo
    limite = max(1024, min(config.GEN_MAX_TOKENS, config.CTX - tamanho - 200))
    texto = await _uma_chamada(messages, emit, temperatura, limite)
    for _ in range(2):
        if not cortado(texto):
            break
        resto = config.CTX - (tamanho + len(texto) // 3) - 200
        if resto < 400:
            break
        mais = await _uma_chamada(messages + [{"role": "assistant", "content": texto},
                                              {"role": "user", "content": "A resposta foi cortada. Continue EXATAMENTE de onde parou, sem repetir nada e sem explicações."}],
                                  emit, temperatura, min(resto, config.GEN_MAX_TOKENS))
        mais = re.sub(r"^\s*```\w*\s*\n", "", mais)   # se ela reabrir o bloco, ignora a abertura
        texto += mais
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
        *recursos.baixa_prioridade([gradle, "assembleDebug", "--console=plain", "-q", "-Dorg.gradle.workers.max=2"]
                                   + ([] if config.GRADLE_DAEMON else ["--no-daemon"])),
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
_trava = recursos.pesado   # uma tarefa pesada por vez (RAM/CPU compartilhadas)


def _pedido_limpo(descricao: str) -> str:
    """O pedido sem as referências da biblioteca (nas correções elas só ocupariam espaço)."""
    return descricao.split("\n\nReferências da biblioteca local")[0].split("\n\n(Pedido curto:")[0].strip()


async def _pede_revisao(mensagens, temperatura, max_tokens):
    async def mudo(_ev):
        return None
    return await _uma_chamada(mensagens, mudo, temperatura, max_tokens)


async def _compila_e_corrige(codigo: str, nome: str, app_id: str, pedido: str, pasta: Path, emit,
                             rodadas: int) -> tuple[bool, str, Path | None, str]:
    """Compila; corrige sozinho o que é mecânico; chama a IA (conversa limpa) para o resto.
    Devolve (ok, código final, apk, relatório de erros)."""
    erros, log, anteriores, relatorio = [], "", set(), ""
    for rodada in range(rodadas + 1):
        for _auto in range(3):
            codigo, feito = gerador.normaliza_java(codigo, PACKAGE)
            if feito:
                await emit({"type": "status", "msg": "Ajuste automático: " + "; ".join(feito) + "."})
            await emit({"type": "code", "text": codigo})
            if pasta.exists():
                shutil.rmtree(pasta)
            escreve_projeto(pasta, nome, app_id, codigo)
            await emit({"type": "status", "msg": "Compilando o app… (a primeira vez baixa as ferramentas e demora mais)"})
            ok, log, apk = await compila(pasta)
            if ok:
                return True, codigo, apk, ""
            erros = gerador.erros_java(log)
            codigo2, feito2 = gerador.corrige_java_por_erros(codigo, erros)
            if not feito2:
                break
            codigo = codigo2
            await emit({"type": "status", "msg": "Corrigi sozinho: " + "; ".join(feito2) + ". Compilando de novo…"})
        relatorio = gerador.relatorio_erros(erros, codigo, gerador.dica_java, log=log)
        assinatura = gerador.assinatura_erros(erros, codigo)
        if assinatura and assinatura <= anteriores:
            relatorio += gerador.AVISO_REPETIDO
        anteriores = assinatura
        if rodada >= rodadas:
            break
        await emit({"type": "status", "msg": f"A IA está corrigindo {len(erros) or 1} erro(s) (rodada {rodada + 1} de {rodadas})…"})
        msgs = gerador.mensagens_correcao(SYSTEM_PROMPT, pedido, codigo, relatorio, "java", nome)
        texto = await pede_codigo(msgs, emit, temperatura=0.4 if gerador.AVISO_REPETIDO in relatorio else 0.1)
        try:
            _n, codigo = extrai_resposta(texto)
        except ValueError:
            continue   # resposta inútil: tenta outra rodada com o mesmo código
    return False, codigo, None, relatorio


async def gera_app(descricao: str, emit, base_codigo: str | None = None, base_nome: str | None = None) -> None:
    """Executa o fluxo completo, mandando eventos por emit(dict)."""
    if _trava.locked():
        await emit({"type": "error", "msg": "Ainda estou criando outro app. Espere ele terminar ou clique em Parar."})
        return
    async with _trava:
        await gerador.garante_modelo_codigo(emit)
        mensagens = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Crie: {EXEMPLO_PEDIDO}"},
            {"role": "assistant", "content": EXEMPLO_RESPOSTA},
            {"role": "user", "content": (
                f"Código ATUAL do app:\n```java\n{base_codigo[:14000]}\n```\n\nModifique o app conforme o pedido e "
                f"devolva o arquivo COMPLETO no mesmo formato. Pedido: {descricao}") if base_codigo
             else f"Crie: {descricao}"},
        ]
        pedido = _pedido_limpo(descricao)
        id_ = uuid.uuid4().hex[:10]
        pasta = config.WORK_DIR / f"app-{id_}"
        pasta2 = config.WORK_DIR / f"app-{id_}-rev"
        try:
            await emit({"type": "status", "msg": "A IA está escrevendo o código do app… (pode levar alguns minutos)"})
            texto = await pede_codigo(mensagens, emit)
            try:
                nome, codigo = extrai_resposta(texto)
            except ValueError:   # fora do formato: pede de novo, lembrando o formato
                texto = await pede_codigo(mensagens + [{"role": "assistant", "content": texto[:3000]}, {"role": "user", "content":
                        "Responda SÓ no formato pedido: a linha NOME: e o código completo dentro de ```java ... ```."}], emit)
                try:
                    nome, codigo = extrai_resposta(texto)
                except ValueError as e:
                    await emit({"type": "error", "msg": str(e), "log": texto[-1500:]})
                    return
            if base_nome and nome == "App IA":
                nome = base_nome
            app_id = f"com.localai.{slugify(nome)}{id_[:4]}"
            ok, codigo, apk, relatorio = await _compila_e_corrige(codigo, nome, app_id, pedido, pasta, emit, config.MAX_FIX_ATTEMPTS)
            if not ok:
                await emit({"type": "error", "msg": "Não consegui compilar o app depois das correções.", "log": relatorio})
                return
            nota_rev = ""
            if config.REVISAO:
                await emit({"type": "status", "msg": "Compilou! A IA está conferindo se o app faz tudo o que você pediu…"})
                faltando = await gerador.revisa(pedido, codigo, "java", _pede_revisao)
                if faltando:
                    await emit({"type": "status", "msg": "Faltou: " + "; ".join(faltando) + ". A IA está completando…"})
                    texto = await pede_codigo(gerador.mensagens_melhoria(SYSTEM_PROMPT, pedido, codigo, faltando, "java", nome), emit)
                    try:
                        _n, codigo2 = extrai_resposta(texto)
                        ok2, codigo2, apk2, _r = await _compila_e_corrige(codigo2, nome, app_id, pedido, pasta2, emit, 2)
                    except ValueError:
                        ok2 = False
                    if ok2:
                        codigo, apk = codigo2, apk2
                        nota_rev = " A revisão completou: " + "; ".join(faltando) + "."
                    else:
                        await emit({"type": "code", "text": codigo})
                        nota_rev = " (A revisão sugeriu completar: " + "; ".join(faltando) + ", mas essa versão não compilou; entreguei a que funciona.)"
            meta = entregas.salva(
                id_, nome, descricao, "apk",
                [(apk, f"{slugify(nome)}.apk", "App Android (.apk)")],
                {"code": codigo, "app_id": app_id})
            await emit({"type": "done", "id": id_, "name": nome, "kind": "apk", "files": meta["files"],
                        "note": "Toque para baixar no celular e instalar. Se o Android pedir, permita instalar de fontes desconhecidas." + nota_rev})
        except RuntimeError as e:
            await emit({"type": "error", "msg": str(e)})
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
            shutil.rmtree(pasta2, ignore_errors=True)
