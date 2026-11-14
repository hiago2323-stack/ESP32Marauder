"""Núcleo comum dos geradores de código (apps Android e firmware ESP32): deixa a IA local mais esperta.

O modelo local (7 bilhões de parâmetros) erra coisas bobas e repetitivas. Em vez de gastar uma rodada inteira da IA
(minutos) com cada uma, o servidor:
  1. corrige SOZINHO o que é mecânico: imports que faltam no Java, @Override no lugar errado, AndroidX,
     bibliotecas do Arduino que faltam (instala pelo arduino-cli) e a API antiga de PWM do ESP32;
  2. só chama a IA para o que exige raciocínio, mostrando o código NUMERADO, cada erro com a linha exata e uma
     dica específica para aquele tipo de erro;
  3. começa cada correção com a conversa limpa (antes as versões antigas iam se empilhando e estouravam o
     contexto de 8192 palavras do modelo; a partir da 2ª correção a IA corrigia praticamente às cegas);
  4. usa o modelo de código (Qwen2.5-Coder) automaticamente quando ele está no PC.
"""
import asyncio
import json
import re
import subprocess
from pathlib import Path

import config

# ======================================================================= Java (apps Android)
IMPORTS_JAVA = {}
for _pkg, _nomes in {
    "android.app": "Activity AlertDialog Dialog ProgressDialog Notification NotificationManager NotificationChannel "
                   "PendingIntent DatePickerDialog TimePickerDialog AlarmManager Service Application ActionBar",
    "android.content": "Context Intent SharedPreferences ContentValues DialogInterface IntentFilter BroadcastReceiver "
                       "ClipboardManager ClipData ComponentName ActivityNotFoundException ContentResolver",
    "android.content.res": "Configuration Resources ColorStateList",
    "android.content.pm": "PackageManager",
    "android.database": "Cursor",
    "android.database.sqlite": "SQLiteDatabase SQLiteOpenHelper",
    "android.graphics": "Color Canvas Paint Path Bitmap BitmapFactory Typeface Rect RectF Point PointF PorterDuff "
                        "LinearGradient RadialGradient Shader Matrix",
    "android.graphics.drawable": "GradientDrawable Drawable ColorDrawable ShapeDrawable StateListDrawable",
    "android.hardware": "Sensor SensorEvent SensorEventListener SensorManager",
    "android.location": "Location LocationManager LocationListener",
    "android.media": "MediaPlayer AudioManager ToneGenerator SoundPool MediaRecorder RingtoneManager",
    "android.net": "Uri ConnectivityManager NetworkInfo",
    "android.os": "Bundle Handler Looper Build Environment CountDownTimer SystemClock Vibrator VibrationEffect "
                  "AsyncTask Message PowerManager",
    "android.provider": "Settings MediaStore",
    "android.speech.tts": "TextToSpeech",
    "android.speech": "RecognizerIntent",
    "android.text": "TextWatcher Editable InputType TextUtils Html SpannableString InputFilter",
    "android.text.method": "ScrollingMovementMethod LinkMovementMethod PasswordTransformationMethod",
    "android.util": "Log TypedValue DisplayMetrics AttributeSet SparseArray Base64",
    "android.view": "View ViewGroup Gravity MotionEvent KeyEvent Menu MenuItem LayoutInflater Window WindowManager",
    "android.view.inputmethod": "InputMethodManager EditorInfo",
    "android.view.animation": "AlphaAnimation Animation RotateAnimation ScaleAnimation TranslateAnimation "
                              "AnimationUtils LinearInterpolator AccelerateDecelerateInterpolator",
    "android.animation": "ValueAnimator ObjectAnimator AnimatorSet",
    "android.widget": "Button TextView EditText LinearLayout RelativeLayout FrameLayout ScrollView HorizontalScrollView "
                      "ListView GridView ArrayAdapter BaseAdapter AdapterView ImageView ImageButton CheckBox RadioButton "
                      "RadioGroup Switch ToggleButton SeekBar ProgressBar Spinner Toast TableLayout TableRow "
                      "CompoundButton NumberPicker DatePicker TimePicker CalendarView Chronometer RatingBar PopupMenu "
                      "AutoCompleteTextView SimpleAdapter Space GridLayout VideoView MediaController",
    "android.webkit": "WebView WebViewClient WebSettings WebChromeClient",
    "java.util": "ArrayList List HashMap Map HashSet Set Collections Arrays Random Date Calendar Locale Timer TimerTask "
                 "Iterator LinkedList Comparator UUID Objects Stack Queue Deque ArrayDeque TreeMap LinkedHashMap "
                 "Collection",
    "java.util.concurrent": "Executors ExecutorService TimeUnit",
    "java.text": "SimpleDateFormat DecimalFormat NumberFormat",
    "java.io": "File FileInputStream FileOutputStream InputStream OutputStream BufferedReader InputStreamReader "
               "IOException BufferedWriter OutputStreamWriter FileReader FileWriter PrintWriter ByteArrayOutputStream",
    "java.net": "URL HttpURLConnection URLEncoder MalformedURLException Socket InetAddress",
    "java.nio.charset": "StandardCharsets",
    "java.math": "BigDecimal BigInteger RoundingMode",
    "org.json": "JSONObject JSONArray JSONException",
}.items():
    for _n in _nomes.split():
        IMPORTS_JAVA[_n] = f"{_pkg}.{_n}"


def _sem_comentarios_e_textos(codigo: str) -> str:
    codigo = re.sub(r"/\*.*?\*/", " ", codigo, flags=re.S)
    codigo = re.sub(r"//[^\n]*", " ", codigo)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', codigo)


def _imports(codigo: str) -> list[str]:
    return re.findall(r"^\s*import\s+([\w.]+(?:\.\*)?)\s*;", codigo, re.M)


def _adiciona_imports(codigo: str, fqns: list[str]) -> str:
    if not fqns:
        return codigo
    linhas = "".join(f"import {f};\n" for f in sorted(set(fqns)))
    primeiro = re.search(r"^import\s", codigo, re.M)       # junto dos imports que já existem
    if primeiro:
        return codigo[:primeiro.start()] + linhas + codigo[primeiro.start():]
    m = re.search(r"^\s*package\s+[\w.]+\s*;\s*\n", codigo, re.M)
    pos = m.end() if m else 0
    return codigo[:pos] + ("\n" if m else "") + linhas + codigo[pos:]


IMPORTS_ANDROIDX = dict(IMPORTS_JAVA)
for _pkg, _nomes in {
    "androidx.appcompat.app": "AppCompatActivity AlertDialog",
    "androidx.appcompat.widget": "Toolbar SearchView",
    "androidx.recyclerview.widget": "RecyclerView LinearLayoutManager GridLayoutManager DividerItemDecoration ItemTouchHelper",
    "androidx.cardview.widget": "CardView",
    "androidx.constraintlayout.widget": "ConstraintLayout",
    "androidx.fragment.app": "Fragment FragmentManager FragmentTransaction",
    "androidx.viewpager2.widget": "ViewPager2",
    "androidx.swiperefreshlayout.widget": "SwipeRefreshLayout",
    "androidx.annotation": "NonNull Nullable",
    "androidx.core.content": "ContextCompat",
    "com.google.android.material.floatingactionbutton": "FloatingActionButton",
    "com.google.android.material.button": "MaterialButton",
    "com.google.android.material.textfield": "TextInputEditText TextInputLayout",
    "com.google.android.material.snackbar": "Snackbar",
    "com.google.android.material.bottomnavigation": "BottomNavigationView",
    "com.google.android.material.tabs": "TabLayout TabLayoutMediator",
    "com.google.android.material.card": "MaterialCardView",
    "com.google.android.material.switchmaterial": "SwitchMaterial",
}.items():
    for _n in _nomes.split():
        IMPORTS_ANDROIDX[_n] = f"{_pkg}.{_n}"


def corrige_imports(codigo: str, mapa: dict | None = None, pacote_r: str = "") -> tuple[str, list[str]]:
    """Adiciona os imports de classes do Android/Java usadas no código e esquecidas pela IA."""
    mapa = mapa or IMPORTS_JAVA
    limpo = _sem_comentarios_e_textos(codigo)
    existentes = _imports(codigo)
    simples = {i.rsplit(".", 1)[1] for i in existentes if not i.endswith("*")}
    curingas = {i[:-2] for i in existentes if i.endswith("*")}
    declaradas = set(re.findall(r"\b(?:class|interface|enum)\s+(\w+)", limpo))
    faltam = []
    for nome in set(re.findall(r"\b([A-Z][A-Za-z0-9_]*)\b", limpo)):
        fq = mapa.get(nome)
        if not fq or nome in simples or nome in declaradas or fq.rsplit(".", 1)[0] in curingas:
            continue
        if re.search(r"[\w.]\." + nome + r"\b", limpo) and not re.search(r"(?<![\w.])" + nome + r"\b", limpo):
            continue  # só aparece qualificado (android.widget.Button): não precisa de import
        faltam.append(fq)
    # classe num subpacote (com.localai.app.ui...) usando R precisa importar o R do app
    pk = re.search(r"^\s*package\s+([\w.]+)\s*;", codigo, re.M)
    if pacote_r and pk and pk.group(1) != pacote_r and re.search(r"\bR\.", limpo) and "R" not in simples:
        faltam.append(f"{pacote_r}.R")
    return _adiciona_imports(codigo, faltam), faltam


def normaliza_java(codigo: str, pacote: str) -> tuple[str, list[str]]:
    """Ajustes mecânicos antes de compilar. Devolve (código, o_que_foi_feito)."""
    feito = []
    if not re.match(r"\s*package\s+" + re.escape(pacote) + r"\s*;", codigo):
        codigo = re.sub(r"^\s*package\s+[\w.]+\s*;\s*", "", codigo)
        codigo = f"package {pacote};\n\n" + codigo
    if "AppCompatActivity" in codigo or "androidx." in codigo:
        antes = codigo
        codigo = re.sub(r"^\s*import\s+androidx\.appcompat\.app\.AppCompatActivity\s*;\s*\n", "", codigo, flags=re.M)
        codigo = codigo.replace("extends AppCompatActivity", "extends Activity")
        codigo = codigo.replace("getSupportActionBar()", "getActionBar()")
        codigo = re.sub(r"^\s*import\s+androidx\.appcompat\.app\.AlertDialog\s*;\s*\n", "", codigo, flags=re.M)
        codigo = re.sub(r"^\s*import\s+androidx\.annotation\.\w+\s*;\s*\n", "", codigo, flags=re.M)
        codigo = re.sub(r"@(NonNull|Nullable)\s+", "", codigo)
        if codigo != antes:
            feito.append("troquei AndroidX por classes do Android puro")
    codigo, imps = corrige_imports(codigo)
    if imps:
        feito.append(f"adicionei {len(imps)} import(s) que faltavam")
    return codigo, feito


def erros_java(log: str) -> list[dict]:
    """Erros do javac (Gradle) com a linha, a mensagem e o símbolo (e erros de recursos XML do AAPT)."""
    linhas = log.splitlines()
    saida, vistos = [], set()
    for ln in linhas:
        a = re.match(r"^(?:ERROR:\s*)?(?P<arq>\S+\.xml):(?P<l>\d+):(?:\d+:)?\s*(?:AAPT:\s*)?error:\s*(?P<msg>.+)$", ln.strip())
        if a and (a["arq"], a["l"]) not in vistos:
            vistos.add((a["arq"], a["l"]))
            saida.append({"arquivo": Path(a["arq"]).name, "caminho": a["arq"], "linha": int(a["l"]), "msg": a["msg"].strip(), "simbolo": ""})
    for i, ln in enumerate(linhas):
        m = re.match(r"^(?P<arq>\S+\.java):(?P<l>\d+): error: (?P<msg>.+)$", ln.strip())
        if not m:
            continue
        simbolo, local = "", ""
        for extra in linhas[i + 1:i + 7]:
            s = re.match(r"\s*symbol:\s+(\w+)\s+(.+)$", extra)
            if s and not simbolo:
                simbolo = f"{s.group(1)} {s.group(2).strip()}"
            lo = re.match(r"\s*location:\s+(.+)$", extra)
            if lo:
                local = lo.group(1).strip()
                break
        chave = (m["l"], m["msg"], simbolo)
        if chave in vistos:
            continue
        vistos.add(chave)
        saida.append({"arquivo": Path(m["arq"]).name, "caminho": m["arq"], "linha": int(m["l"]), "msg": m["msg"].strip(),
                      "simbolo": simbolo, "local": local})
    return saida


def dica_java(e: dict, codigo: str) -> str:
    msg, sim = e["msg"], e.get("simbolo", "")
    nome = sim.split()[-1].split("(")[0] if sim else ""
    if "cannot find symbol" in msg:
        if nome == "R" or "R." in _linha(codigo, e["linha"]):
            return "Este projeto NÃO tem layouts XML nem R.id/R.layout: monte a interface por código Java."
        if sim.startswith("class") and nome not in IMPORTS_JAVA:
            return (f"A classe {nome} não existe no Android padrão (sem bibliotecas). Use uma classe do Android "
                    "(android.*) ou do Java (java.*), ou crie a classe no próprio arquivo.")
        if sim.startswith("method"):
            lo = re.match(r"variable (\w+) of type ([\w.<>\[\]]+)", e.get("local", ""))
            if lo:
                var, tipo = lo.group(1), lo.group(2)
                campo = _tipo_do_campo(codigo, var, tipo)
                if campo:
                    return (f"Aqui '{var}' é uma {tipo} (variável local) com o MESMO nome do campo {campo} '{var}'. "
                            f"Renomeie a variável local (ex.: texto{var[:1].upper()}{var[1:]}) ou use this.{var}.{nome}(...).")
                return f"'{var}' é do tipo {tipo}, e {tipo} não tem o método {nome}(...). Use o objeto certo ou outro método."
            return f"O método {nome} não existe nesse objeto/classe. Confira o nome e os parâmetros, ou crie o método."
        if sim.startswith("variable"):
            return f"A variável {nome} não foi declarada neste ponto. Declare-a como campo da classe (antes de usar)."
    if "resource" in msg and "not found" in msg:
        return ("Esse recurso não existe: crie-o (em res/values/colors.xml, strings.xml ou res/drawable) ou use um que "
                "exista, como @android:color/white ou um valor direto (#FF5722, 16sp).")
    if "attribute" in msg and "not found" in msg:
        return "Esse atributo XML não existe nesse componente/tema: remova-o ou use o nome certo (android:… ou app:…)."
    if "effectively final" in msg or "must be final" in msg:
        return ("Variáveis locais usadas dentro de lambdas/classes anônimas não podem mudar. "
                "Transforme essa variável em um CAMPO da classe (private) ou crie uma cópia final.")
    if "unreported exception" in msg:
        return "Envolva essa chamada em try { ... } catch (Exception e) { ... }."
    if "incompatible types" in msg:
        return "Os tipos não batem: converta (ex.: Integer.parseInt, String.valueOf, (int)) ou ajuste a declaração."
    if "missing return statement" in msg:
        return "Todo caminho do método precisa de return."
    if "already defined" in msg:
        return "Esse nome já foi declarado: remova a duplicata ou renomeie."
    if "non-static" in msg and "static context" in msg:
        return "Você está usando algo da instância dentro de um método static: tire o static ou use a instância."
    if "does not override" in msg:
        return "Remova o @Override desse método (ou corrija a assinatura para a do método original)."
    if "class, interface, enum, or record expected" in msg or "reached end of file" in msg:
        return "As chaves { } estão desbalanceadas ou há código fora da classe. Confira a estrutura do arquivo inteiro."
    if "is not abstract and does not override abstract method" in msg:
        return "A classe precisa implementar todos os métodos da interface (ex.: onClick, onItemClick, getView...)."
    return ""


def _tipo_do_campo(codigo: str, var: str, tipo_local: str) -> str:
    """Tipo de um CAMPO da classe com esse nome, se for diferente do tipo da variável local (nome repetido)."""
    padrao = (r"^\s{2,8}(?:(?:private|public|protected|static|final)\s+)*([A-Z][\w.]*(?:<[^;=()]*?>)?(?:\[\])?)\s+"
              r"([A-Za-z_][^;()]*?);")
    for m in re.finditer(padrao, codigo, re.M):
        nomes = [d.split("=")[0].strip() for d in m.group(2).split(",")]
        if var in nomes and m.group(1) != tipo_local:
            return m.group(1)
    return ""


def corrige_java_por_erros(codigo: str, erros: list[dict], mapa: dict | None = None) -> tuple[str, list[str]]:
    """Correções automáticas a partir dos erros (sem a IA)."""
    mapa = mapa or IMPORTS_JAVA
    feito = []
    linhas = codigo.split("\n")
    remover = set()
    novos = []
    for e in erros:
        if "does not override" in e["msg"]:
            idx = e["linha"] - 1
            for j in (idx, idx - 1):
                if 0 <= j < len(linhas) and linhas[j].strip() == "@Override":
                    remover.add(j)
                    break
        lo = re.match(r"variable (\w+) of type ([\w.<>\[\]]+)", e.get("local", ""))
        if "cannot find symbol" in e["msg"] and e.get("simbolo", "").startswith("method") and lo:
            var, tipo = lo.group(1), lo.group(2)
            idx = e["linha"] - 1
            if _tipo_do_campo(codigo, var, tipo) and 0 <= idx < len(linhas):
                nova = re.sub(r"(?<![\w.])" + re.escape(var) + r"\.", f"this.{var}.", linhas[idx], count=1)
                if nova != linhas[idx]:
                    linhas[idx] = nova
                    feito.append(f"'{var}' tinha o nome de um campo; usei this.{var}")
        if "cannot find symbol" in e["msg"] and e.get("simbolo", "").startswith("class"):
            nome = e["simbolo"].split()[-1]
            if nome in mapa and f"import {mapa[nome]};" not in codigo:
                novos.append(mapa[nome])
    if remover:
        linhas = [ln for i, ln in enumerate(linhas) if i not in remover]
        feito.append(f"removi {len(remover)} @Override indevido(s)")
    codigo = "\n".join(linhas)
    if novos:
        codigo = _adiciona_imports(codigo, novos)
        feito.append(f"adicionei {len(set(novos))} import(s)")
    return codigo, feito


# ======================================================================= Arduino (firmware ESP32)
# cabeçalho -> nome da biblioteca no gerenciador do Arduino (o resto é procurado pelo arduino-cli)
HEADERS_LIBS = {
    "DHT.h": "DHT sensor library", "DHT_U.h": "DHT sensor library", "Adafruit_Sensor.h": "Adafruit Unified Sensor",
    "Adafruit_SSD1306.h": "Adafruit SSD1306", "Adafruit_GFX.h": "Adafruit GFX Library",
    "Adafruit_NeoPixel.h": "Adafruit NeoPixel", "FastLED.h": "FastLED", "PubSubClient.h": "PubSubClient",
    "ArduinoJson.h": "ArduinoJson", "OneWire.h": "OneWire", "DallasTemperature.h": "DallasTemperature",
    "LiquidCrystal_I2C.h": "LiquidCrystal I2C", "Adafruit_BME280.h": "Adafruit BME280 Library",
    "Adafruit_BMP280.h": "Adafruit BMP280 Library", "Adafruit_MPU6050.h": "Adafruit MPU6050",
    "TFT_eSPI.h": "TFT_eSPI", "WiFiManager.h": "WiFiManager", "NTPClient.h": "NTPClient",
    "ESP32Servo.h": "ESP32Servo", "MFRC522.h": "MFRC522", "U8g2lib.h": "U8g2", "IRremoteESP8266.h": "IRremoteESP8266",
    "IRrecv.h": "IRremoteESP8266", "IRsend.h": "IRremoteESP8266", "Keypad.h": "Keypad", "RTClib.h": "RTClib",
    "TinyGPS++.h": "TinyGPSPlus", "TinyGPSPlus.h": "TinyGPSPlus", "HX711.h": "HX711 Arduino Library",
    "AccelStepper.h": "AccelStepper", "BH1750.h": "BH1750", "NewPing.h": "NewPing", "Bounce2.h": "Bounce2",
    "ESPAsyncWebServer.h": "ESPAsyncWebServer", "AsyncTCP.h": "Async TCP", "UniversalTelegramBot.h": "UniversalTelegramBot",
    "MAX30105.h": "SparkFun MAX3010x Pulse and Proximity Sensor Library", "Adafruit_ADS1X15.h": "Adafruit ADS1X15",
    "Adafruit_INA219.h": "Adafruit INA219", "SHT31.h": "SHT31", "Adafruit_SHT31.h": "Adafruit SHT31 Library",
}

DICAS_CORE3 = {
    "ledcSetup": "No núcleo ESP32 3.x NÃO existem ledcSetup/ledcAttachPin. Use: ledcAttach(PINO, FREQ, BITS); e ledcWrite(PINO, duty);",
    "ledcAttachPin": "No núcleo ESP32 3.x use ledcAttach(PINO, FREQ, BITS) (não existe ledcAttachPin) e ledcWrite(PINO, duty).",
    "ledcDetachPin": "No núcleo ESP32 3.x use ledcDetach(PINO).",
    "timerAlarmWrite": "No núcleo ESP32 3.x: hw_timer_t *t = timerBegin(1000000); timerAttachInterrupt(t, &funcao); timerAlarm(t, microssegundos, true, 0);",
    "timerAlarmEnable": "No núcleo ESP32 3.x o timerAlarm(t, us, true, 0) já liga o alarme; não existe timerAlarmEnable.",
    "timerBegin": "No núcleo ESP32 3.x timerBegin recebe só a frequência: hw_timer_t *t = timerBegin(1000000);",
    "timerAttachInterrupt": "No núcleo ESP32 3.x: timerAttachInterrupt(t, &funcao); (sem o terceiro parâmetro).",
    "analogWriteResolution": "Para PWM no ESP32 prefira ledcAttach(PINO, FREQ, BITS) e ledcWrite(PINO, duty).",
}


def porta_ledc(codigo: str) -> tuple[str, list[str]]:
    """Converte a API antiga de PWM (ledcSetup + ledcAttachPin + ledcWrite(canal)) para a do núcleo 3.x."""
    if "ledcSetup" not in codigo and "ledcAttachPin" not in codigo:
        return codigo, []
    setups = {m.group(1).strip(): (m.group(2).strip(), m.group(3).strip(), m.group(0))
              for m in re.finditer(r"ledcSetup\s*\(\s*([^,()]+?)\s*,\s*([^,()]+?)\s*,\s*([^()]+?)\s*\)\s*;", codigo)}
    anexos = [(m.group(1).strip(), m.group(2).strip(), m.group(0))
              for m in re.finditer(r"ledcAttachPin\s*\(\s*([^,()]+?)\s*,\s*([^()]+?)\s*\)\s*;", codigo)]
    canal_pino = {}
    for pino, canal, _ in anexos:
        if canal in canal_pino and canal_pino[canal] != pino:
            return codigo, []        # um canal para vários pinos: deixa a IA resolver (com a dica)
        canal_pino[canal] = pino
    if not anexos or any(c not in setups for c in canal_pino):
        return codigo, []
    for canal, pino in canal_pino.items():
        freq, bits, txt = setups[canal]
        codigo = codigo.replace(txt, f"ledcAttach({pino}, {freq}, {bits});", 1)
        codigo = re.sub(r"ledc(Write|WriteTone|Read)\s*\(\s*" + re.escape(canal) + r"\s*([,)])",
                        lambda m: f"ledc{m.group(1)}({pino}{m.group(2)}", codigo)
    for _, _, txt in anexos:
        codigo = codigo.replace(txt, "", 1)
    return codigo, ["converti o PWM para a API do núcleo ESP32 3.x (ledcAttach/ledcWrite por pino)"]


def normaliza_ino(codigo: str) -> tuple[str, list[str]]:
    feito = []
    if re.search(r"#include\s*[<\"]Servo\.h[>\"]", codigo):
        codigo = re.sub(r"#include\s*[<\"]Servo\.h[>\"]", "#include <ESP32Servo.h>", codigo)
        feito.append("troquei Servo.h por ESP32Servo.h (a do ESP32)")
    codigo, f = porta_ledc(codigo)
    return codigo, feito + f


def erros_gcc(log: str) -> list[dict]:
    saida, vistos = [], set()
    for ln in log.splitlines():
        m = re.match(r"^(?P<arq>\S+?\.(?:ino|cpp|c|h|hpp)):(?P<l>\d+):(?:\d+:)?\s*(?P<tipo>fatal error|error):\s*(?P<msg>.+)$", ln.strip())
        if m:
            chave = (Path(m["arq"]).name, m["l"], m["msg"])
            if chave not in vistos:
                vistos.add(chave)
                saida.append({"arquivo": Path(m["arq"]).name, "linha": int(m["l"]), "msg": m["msg"].strip(),
                              "do_sketch": m["arq"].endswith(".ino")})
            continue
        u = re.search(r"undefined reference to `([^']+)'", ln)
        if u and ("ref", u.group(1)) not in vistos:
            vistos.add(("ref", u.group(1)))
            saida.append({"arquivo": "", "linha": 0, "msg": f"undefined reference to {u.group(1)}", "do_sketch": False})
    saida.sort(key=lambda e: not e["do_sketch"])
    return saida


def headers_faltando(erros: list[dict]) -> list[str]:
    out = []
    for e in erros:
        m = re.match(r"([\w+./-]+\.h(?:pp)?): No such file or directory", e["msg"])
        if m:
            out.append(Path(m.group(1)).name)
    return out


def dica_esp32(e: dict, codigo: str, placa: str = "esp32") -> str:
    msg = e["msg"]
    for nome, dica in DICAS_CORE3.items():
        if f"'{nome}'" in msg or f"`{nome}" in msg:
            return dica
    if "BluetoothSerial" in msg or ("Bluetooth" in msg and placa != "esp32"):
        return "Esta placa não tem Bluetooth clássico (BluetoothSerial). Use BLE (BLEDevice.h) ou WiFi."
    if "No such file or directory" in msg:
        return "Biblioteca que não existe/não foi encontrada: troque por uma biblioteca comum ou faça sem ela."
    if "was not declared in this scope" in msg:
        nome = re.search(r"'(\w+)'", msg)
        n = nome.group(1) if nome else "esse nome"
        return (f"{n} não foi declarado: declare a variável/função ANTES de usar (no topo do arquivo) "
                "ou inclua a biblioteca certa.")
    if "does not name a type" in msg:
        return "Esse tipo não existe: falta o #include da biblioteca dele, ou o nome está errado."
    if "invalid conversion" in msg or "cannot convert" in msg:
        return "Tipos incompatíveis: converta explicitamente (ex.: String(x), x.c_str(), (int)x)."
    if "redefinition" in msg or "previously declared" in msg:
        return "Nome declarado duas vezes: remova a duplicata."
    if "expected" in msg and ("before" in msg or "at end of input" in msg):
        return "Erro de sintaxe: falta ; ou } ou ) perto desta linha (veja também a linha anterior)."
    if "undefined reference" in msg:
        return "A função foi declarada mas não implementada (ou falta a biblioteca). Implemente a função."
    return ""


def _arduino_env() -> dict:
    import os
    return dict(os.environ)


def instala_bibliotecas(headers: list[str]) -> list[str]:
    """Instala pelo arduino-cli as bibliotecas que fornecem esses .h. Devolve os nomes instalados."""
    cli = config.ARDUINO_CLI
    instaladas = []
    for h in dict.fromkeys(headers):
        nome = HEADERS_LIBS.get(h) or _procura_lib(h)
        if not nome:
            continue
        try:
            r = subprocess.run([cli, "lib", "install", nome], capture_output=True, text=True, timeout=300,
                               env=_arduino_env())
            if r.returncode == 0:
                instaladas.append(nome)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return instaladas


def _procura_lib(header: str) -> str | None:
    """Pergunta ao índice do Arduino qual biblioteca fornece esse cabeçalho."""
    stem = Path(header).stem
    try:
        r = subprocess.run([config.ARDUINO_CLI, "lib", "search", stem, "--format", "json"],
                           capture_output=True, text=True, timeout=120, env=_arduino_env())
        libs = json.loads(r.stdout or "{}").get("libraries", [])
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    cands = [l["name"] for l in libs if header in ((l.get("latest") or {}).get("provides_includes") or [])]
    if not cands:
        return None
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    exato = [c for c in cands if norm(c) == norm(stem)]
    return (exato or sorted(cands, key=len))[0]


# ======================================================================= prompts para a IA
def numera(codigo: str) -> str:
    return "\n".join(f"{i:>4}| {ln}" for i, ln in enumerate(codigo.split("\n"), 1))


def _linha(codigo: str, n: int) -> str:
    ls = codigo.split("\n")
    return ls[n - 1].strip() if 0 < n <= len(ls) else ""


def relatorio_erros(erros: list[dict], codigo: str, dica, limite: int = 8, log: str = "") -> str:
    """Texto curto para a IA: cada erro com a linha, o código daquela linha e uma dica."""
    if not erros:
        m = re.search(r"\* What went wrong:\n(.*?)(?:\n\* |\Z)", log, re.S)
        return (m.group(1) if m else log[-1500:]).strip()[:1500]
    partes = []
    for e in erros[:limite]:
        txt = f"- Linha {e['linha']}: {e['msg']}" if e.get("linha") else f"- {e['msg']}"
        if e.get("simbolo"):
            txt += f" [{e['simbolo']}" + (f" em {e['local']}" if e.get("local") else "") + "]"
        if e.get("arquivo") and e.get("do_sketch") is False and e["arquivo"]:
            txt += f" (no arquivo {e['arquivo']})"
        lc = _linha(codigo, e["linha"]) if e.get("linha") and e.get("do_sketch", True) else ""
        if lc:
            txt += f"\n    código: {lc}"
        d = dica(e, codigo)
        if d:
            txt += f"\n    dica: {d}"
        partes.append(txt)
    if len(erros) > limite:
        partes.append(f"(e mais {len(erros) - limite} erro(s) parecido(s))")
    return "\n".join(partes)


def assinatura_erros(erros: list[dict], codigo: str) -> set:
    """Identifica os erros pelo conteúdo (não pelo número da linha, que muda), para saber se eles se repetem."""
    return {(e["msg"][:80], e.get("simbolo", ""), _linha(codigo, e.get("linha", 0))[:80]) for e in erros}


AVISO_REPETIDO = ("\n\nATENÇÃO: estes mesmos erros já apareceram na tentativa anterior, então a correção anterior NÃO "
                  "funcionou. Não repita a mesma solução: reescreva o trecho com problema de OUTRO jeito, mais simples "
                  "(por exemplo, renomeie variáveis, separe em métodos, use outra classe/função mais comum).")


def mensagens_correcao(system: str, pedido: str, codigo: str, relatorio: str, linguagem: str, rotulo: str) -> list[dict]:
    """Conversa LIMPA para a correção: regras + pedido original + código numerado + erros com dicas."""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"Pedido original: {pedido}\n\nEste é o código atual (os números à esquerda são só referência de linha, "
            f"NÃO fazem parte do código):\n```\n{numera(codigo)}\n```\n\nO compilador encontrou estes erros:\n{relatorio}\n\n"
            f"Corrija TODOS os erros mantendo o que já funciona e o que o pedido exige. Devolva o arquivo COMPLETO, sem os "
            f"números de linha, no formato:\nNOME: {rotulo}\n```{linguagem}\n<código completo>\n```")},
    ]


# ======================================================================= modelo de código
def _modelo_atual_e_codigo() -> bool:
    try:
        for ln in config.MODELO_ENV.read_text().splitlines():
            if ln.startswith("MODEL="):
                return "coder" in ln.lower() or "code" in Path(ln[6:]).name.lower()
    except OSError:
        pass
    return False


async def garante_modelo_codigo(emit) -> None:
    """Usa o modelo de código (Qwen2.5-Coder) para gerar apps/firmware: troca sozinho se ele estiver no PC."""
    import os
    import perfis
    if os.environ.get("CODIGO_AUTO", "1") == "0" or _modelo_atual_e_codigo():
        return
    p = perfis.PERFIS["codigo"]
    if not perfis.presente(p):
        try:
            perfis.baixar("codigo")
            await emit({"type": "status", "msg": "Dica: estou baixando NO PC o modelo de código (Qwen2.5-Coder, ~4,7 GB), "
                                                 "que escreve apps e firmware bem melhor. Desta vez uso o modelo atual."})
        except ValueError:
            pass
        return
    await emit({"type": "status", "msg": "Trocando para o modelo de código (Qwen2.5-Coder)… leva 1 a 2 minutos só desta vez."})
    try:
        await asyncio.to_thread(perfis.seleciona, "codigo")
    except (RuntimeError, ValueError) as e:
        await emit({"type": "status", "msg": f"Não consegui trocar o modelo sozinho ({e}). Sigo com o atual."})
        return
    for _ in range(180):   # até ~6 min
        await asyncio.sleep(2)
        if await perfis.estado_llm() == "ok":
            return
    await emit({"type": "status", "msg": "O modelo de código está demorando para carregar; vou tentar mesmo assim."})


# ======================================================================= revisão (o código faz TUDO o que foi pedido?)
def _sem_coment_cpp(codigo: str) -> str:
    codigo = re.sub(r"/\*.*?\*/", " ", codigo, flags=re.S)
    return re.sub(r"//[^\n]*", " ", codigo)


def confere_pedido(pedido: str, codigo: str, linguagem: str) -> list[str]:
    """Conferência SEM IA das partes objetivas do pedido: pinos, intervalos de tempo, página que se atualiza,
    dados salvos, toque longo e endereços citados. Devolve o que o código não faz (lista vazia = tudo certo)."""
    p = pedido.lower()
    c = _sem_coment_cpp(codigo)
    cl = c.lower()
    falta = []
    # pinos citados ("pino 4", "GPIO 25", "no pino D5")
    for n in dict.fromkeys(re.findall(r"\b(?:pino|gpio|pin)\s*(?:n[ºo°.]?\s*)?(?:d|gpio)?\s*(\d{1,2})\b", p)):
        if not re.search(r"(?<![\w.])(?:GPIO_NUM_)?" + n + r"(?![\w.])", c):
            falta.append(f"usar o pino {n}, como o pedido diz")
    # intervalos de tempo ("a cada 5 segundos", "10 s", "2 minutos", "500 ms")
    for num, uni in re.findall(r"(\d+(?:[.,]\d+)?)\s*(milissegundos?|ms|segundos?|seg|s|minutos?|min|horas?|h)\b", p):
        v = float(num.replace(",", "."))
        mult = 1 if uni.startswith(("mili", "ms")) else 3600000 if uni.startswith("h") else 60000 if uni.startswith("min") else 1000
        ms = int(v * mult)
        seg = ms / 1000
        seg_txt = str(int(seg)) if seg == int(seg) else str(seg)
        opcoes = [rf"(?<![\w.]){ms}(?:UL|L|u|l)?(?![\w.])", rf"(?<![\w.]){seg_txt}\s*\*\s*1000\b", rf"\b1000\s*\*\s*{seg_txt}(?![\w.])",
                  rf"content\s*=\s*\\?['\"]{seg_txt}\b"]
        if mult == 60000 and v == int(v):
            opcoes += [rf"(?<![\w.]){int(v)}\s*\*\s*60\s*\*\s*1000", rf"(?<![\w.]){int(v)}\s*\*\s*60000"]
        if mult == 3600000 and v == int(v):
            opcoes += [rf"(?<![\w.]){int(v)}\s*\*\s*3600\s*\*\s*1000", rf"(?<![\w.]){int(v)}\s*\*\s*60\s*\*\s*60\s*\*\s*1000"]
        if ms >= 1000 and not any(re.search(o, c) for o in opcoes):
            falta.append(f"o intervalo de {num} {uni} pedido (= {ms} ms) não aparece no código")
    # página web que se atualiza sozinha
    web = re.search(r"p[aá]gina|web|site|navegador|html", p)
    atualiza = re.search(r"atualiz|tempo real|ao vivo|sozinh|autom[aá]tic", p)
    if linguagem == "cpp" and web and atualiza and re.search(r"webserver|asyncwebserver|server\.send|request->send", cl):
        if not re.search(r"http-equiv|setinterval|settimeout|fetch\s*\(|xmlhttprequest|eventsource|websocket", cl):
            falta.append("a página web não se atualiza sozinha: use <meta http-equiv='refresh' content='N'> ou JavaScript "
                         "(setInterval + fetch de uma rota que devolve os valores)")
    # dados que o usuário cria precisam ficar salvos (app Android)
    if linguagem == "java" and re.search(r"\bsalv|\bguard|lembrar|persist|não perder|nao perder|hist[oó]rico|anota|notas?\b|tarefas?\b", p):
        if not re.search(r"sharedpreferences|sqlite|openfileoutput|fileoutputstream", cl):
            falta.append("salvar os dados no celular (SharedPreferences) e carregá-los ao abrir o app")
    # toque longo
    if linguagem == "java" and re.search(r"segur|toque longo|pressionar e segurar|long[ -]?press", p):
        if not re.search(r"setonitemlongclicklistener|setonlongclicklistener|onlongclick", cl):
            falta.append("a ação ao tocar e SEGURAR (setOnItemLongClickListener / setOnLongClickListener)")
    # endereços citados (broker MQTT, sites)
    for host in dict.fromkeys(re.findall(r"\b((?:[a-z0-9-]+\.)+(?:org|com|net|io|br|cloud))\b", p)):
        if host not in cl:
            falta.append(f"usar o endereço {host} citado no pedido")
    return falta[:4]


async def revisa(pedido: str, codigo: str, linguagem: str, pede) -> list[str]:
    """Pede à IA uma checagem curta do código contra o pedido. Devolve o que falta (até 3 itens; vazio = tudo ok).

    `pede(mensagens, temperatura, max_tokens)` é a função que chama o modelo (vem do androidgen, sem import circular).
    """
    msgs = [
        {"role": "system", "content": "Você é um revisor de código rigoroso e objetivo. Responda só com JSON."},
        {"role": "user", "content": (
            f"Pedido do usuário: {pedido}\n\nCódigo:\n```{linguagem}\n{codigo}\n```\n\n"
            "Confira se o código faz TUDO o que o pedido exige (funções, comportamento, números como pinos, intervalos e "
            "tempos). Ignore estilo, comentários e detalhes não pedidos. Responda SOMENTE com JSON no formato "
            '{"faltando": ["..."]}, com no máximo 3 itens curtos do que o pedido exige e o código NÃO faz. '
            'Se o código faz tudo, responda {"faltando": []}.')},
    ]
    try:
        txt = await pede(msgs, 0.0, 220)
    except RuntimeError:
        return []
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        return []
    try:
        itens = json.loads(m.group(0)).get("faltando", [])
    except (ValueError, AttributeError):
        return []
    return [str(i).strip()[:160] for i in itens if str(i).strip()][:3] if isinstance(itens, list) else []


def mensagens_melhoria(system: str, pedido: str, codigo: str, faltando: list[str], linguagem: str, rotulo: str) -> list[dict]:
    lista = "\n".join(f"- {f}" for f in faltando)
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"Pedido original: {pedido}\n\nEste código já compila:\n```{linguagem}\n{codigo}\n```\n\n"
            f"Mas a revisão encontrou o que ainda FALTA para cumprir o pedido:\n{lista}\n\n"
            f"Implemente o que falta mantendo TUDO o que já funciona. Devolva o arquivo COMPLETO no formato:\n"
            f"NOME: {rotulo}\n```{linguagem}\n<código completo>\n```")},
    ]
