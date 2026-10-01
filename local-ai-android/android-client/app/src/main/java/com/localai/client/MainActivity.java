package com.localai.client;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.app.DownloadManager;
import android.content.ActivityNotFoundException;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.media.MediaRecorder;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.Settings;
import android.text.InputType;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Betina & IA: o celular só mostra a tela e grava a voz. Todo o processamento
 * (modelo de IA, voz, pesquisa, compilação) acontece no PC, que o app acessa pela rede
 * (de qualquer lugar, usando o Tailscale).
 */
public class MainActivity extends Activity {
    private static final int PEDIDO_MICROFONE = 10;
    private static final int PEDIDO_ARQUIVO = 11;

    private WebView web;
    private SharedPreferences prefs;
    private MediaRecorder gravador;
    private File arquivoVoz;
    private ValueCallback<Uri[]> seletorArquivos;   // resposta pendente do botão 📎 da página
    private PainelTele tele;                        // faixa de telemetria em tempo real
    private volatile boolean visivel = false;
    private Thread leitor;

    // ------------------------------------------------------------------ ciclo de vida
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("config", MODE_PRIVATE);
        web = new WebView(this);
        tele = new PainelTele(this);
        LinearLayout raiz = new LinearLayout(this);
        raiz.setOrientation(LinearLayout.VERTICAL);
        raiz.setBackgroundColor(PainelTele.FUNDO);
        raiz.addView(tele, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT));
        raiz.addView(web, new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));
        setContentView(raiz);
        if (Build.VERSION.SDK_INT >= 21) {
            getWindow().setStatusBarColor(PainelTele.FUNDO);
            getWindow().setNavigationBarColor(PainelTele.FUNDO);
        }
        configuraWeb();

        IntentFilter filtro = new IntentFilter(DownloadManager.ACTION_DOWNLOAD_COMPLETE);
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(downloadTerminou, filtro, Context.RECEIVER_EXPORTED);
        } else {
            registerReceiver(downloadTerminou, filtro);
        }

        if (servidor().isEmpty()) {
            mostraConfig();
        } else {
            carrega();
        }
    }

    // ------------------------------------------------------------------ telemetria em tempo real
    @Override
    protected void onResume() {
        super.onResume();
        visivel = true;
        if (leitor == null || !leitor.isAlive()) {
            leitor = new Thread(this::laco, "telemetria");
            leitor.setDaemon(true);
            leitor.start();
        }
    }

    @Override
    protected void onPause() {
        visivel = false;
        super.onPause();
    }

    /** Lê /telemetry do PC a cada ~1,5 s enquanto o app está na tela. */
    private void laco() {
        while (visivel) {
            String base = servidor();
            if (base.isEmpty()) {
                runOnUiThread(() -> tele.falhou("sem servidor configurado"));
            } else {
                try {
                    HttpURLConnection c = (HttpURLConnection) new URL(base + "/telemetry").openConnection();
                    c.setConnectTimeout(3000);
                    c.setReadTimeout(4000);
                    c.setRequestProperty("Authorization", "Bearer " + token());
                    int codigo = c.getResponseCode();
                    if (codigo != 200) {
                        throw new java.io.IOException("HTTP " + codigo);
                    }
                    ByteArrayOutputStream corpo = new ByteArrayOutputStream();
                    try (InputStream in = c.getInputStream()) {
                        byte[] buf = new byte[4096];
                        int n;
                        while ((n = in.read(buf)) > 0) {
                            corpo.write(buf, 0, n);
                        }
                    }
                    final JSONObject j = new JSONObject(corpo.toString("UTF-8"));
                    runOnUiThread(() -> tele.atualiza(j));
                } catch (Exception e) {
                    final String m = e.getClass().getSimpleName();
                    runOnUiThread(() -> tele.falhou(m));
                }
            }
            try {
                Thread.sleep(1500);
            } catch (InterruptedException e) {
                return;
            }
        }
    }

    @Override
    protected void onDestroy() {
        try {
            unregisterReceiver(downloadTerminou);
        } catch (IllegalArgumentException ignorado) {
            // já estava desregistrado
        }
        paraGravador();
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }

    // ------------------------------------------------------------------ configuração
    /** O PC se chama "betina" na VPN Tailscale: sem configurar nada o app já o encontra aqui. */
    private static final String SERVIDOR_PADRAO = "http://betina:8080";

    private String servidor() {
        String v = prefs.getString("servidor", SERVIDOR_PADRAO);
        return v.isEmpty() ? SERVIDOR_PADRAO : v;
    }

    private String token() {
        return prefs.getString("token", "");
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }

    private static String normaliza(String s) {
        s = s.trim();
        if (s.isEmpty()) {
            return "";
        }
        if (!s.startsWith("http://") && !s.startsWith("https://")) {
            s = "http://" + s;
        }
        while (s.endsWith("/")) {
            s = s.substring(0, s.length() - 1);
        }
        return s;
    }

    private void mostraConfig() {
        LinearLayout caixa = new LinearLayout(this);
        caixa.setOrientation(LinearLayout.VERTICAL);
        caixa.setPadding(dp(20), dp(12), dp(20), 0);

        TextView ajuda = new TextView(this);
        ajuda.setText("Normalmente você não precisa mexer aqui: o app procura o PC sozinho em "
                + SERVIDOR_PADRAO + " pela VPN Tailscale. Só preencha se o seu PC tiver outro endereço "
                + "(aparece em \"Conectar celular\" no PC). O token só é necessário fora da VPN.");
        caixa.addView(ajuda);

        final EditText srv = new EditText(this);
        srv.setHint("Endereço (ex.: http://100.64.0.1:8080)");
        srv.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        srv.setText(servidor());
        caixa.addView(srv);

        final EditText tok = new EditText(this);
        tok.setHint("Token (senha)");
        tok.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD);
        tok.setText(token());
        caixa.addView(tok);

        new AlertDialog.Builder(this)
                .setTitle("Conectar ao seu PC")
                .setView(caixa)
                .setCancelable(!servidor().isEmpty())
                .setPositiveButton("Salvar", (d, w) -> {
                    prefs.edit()
                            .putString("servidor", normaliza(srv.getText().toString()))
                            .putString("token", tok.getText().toString().trim())
                            .apply();
                    carrega();
                })
                .setNegativeButton("Cancelar", null)
                .show();
    }

    private void carrega() {
        String base = servidor();
        if (base.isEmpty()) {
            mostraConfig();
            return;
        }
        // Abrir /?token=... faz o servidor gravar o token num cookie; depois o endereço fica limpo
        web.loadUrl(base + "/?token=" + Uri.encode(token()));
    }

    private void mostraErro(WebView v, String motivo) {
        String seguro = motivo == null ? "" : motivo.replace("<", "&lt;");
        String estilo = "style='font-size:18px;padding:12px 18px;margin:6px 6px 0 0;border-radius:8px;border:1px solid #888'";
        String html = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                + "<body style='font-family:sans-serif;padding:24px;background:#14161a;color:#e8eaed'>"
                + "<h2>Não consegui falar com o seu PC</h2><p>" + seguro + "</p>"
                + "<p>Confira se o PC está ligado e se o Tailscale (a VPN) está <b>conectado</b> no celular.</p>"
                + "<button " + estilo + " onclick='AndroidBridge.openTailscale()'>Abrir o Tailscale</button>"
                + "<button " + estilo + " onclick='AndroidBridge.retry()'>Tentar de novo</button>"
                + "<button " + estilo + " onclick='AndroidBridge.openSettings()'>Configurar servidor</button>"
                + "</body></html>";
        v.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    /** Abre o app Tailscale; se não estiver instalado, abre a página dele na loja. */
    private void abreTailscale() {
        Intent i = getPackageManager().getLaunchIntentForPackage("com.tailscale.ipn");
        try {
            if (i != null) {
                startActivity(i);
            } else {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=com.tailscale.ipn")));
            }
        } catch (ActivityNotFoundException e) {
            startActivity(new Intent(Intent.ACTION_VIEW,
                    Uri.parse("https://play.google.com/store/apps/details?id=com.tailscale.ipn")));
        }
    }

    private void configuraWeb() {
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_ALWAYS_ALLOW);
        CookieManager.getInstance().setAcceptCookie(true);

        web.addJavascriptInterface(new Ponte(), "AndroidBridge");

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                Uri destino = r.getUrl();
                String meuHost = Uri.parse(servidor()).getHost();
                if (destino.getHost() != null && destino.getHost().equals(meuHost)) {
                    return false; // links do próprio servidor ficam dentro do app
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, destino)); // o resto abre no navegador
                } catch (ActivityNotFoundException ignorado) {
                    // sem app para abrir: ignora
                }
                return true;
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) {
                    mostraErro(v, e.getDescription().toString());
                }
            }
        });

        // Botão 📎 da página (anexar imagens, .ino, .bin, .apk, código): abre o seletor de arquivos do Android
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> retorno, FileChooserParams params) {
                if (seletorArquivos != null) {
                    seletorArquivos.onReceiveValue(null);
                }
                seletorArquivos = retorno;
                try {
                    startActivityForResult(params.createIntent(), PEDIDO_ARQUIVO);
                } catch (ActivityNotFoundException e) {
                    seletorArquivos = null;
                    Toast.makeText(MainActivity.this, "Não achei um app para escolher arquivos.", Toast.LENGTH_LONG).show();
                    return false;
                }
                return true;
            }
        });

        // Downloads (.apk, .bin...) vão para a pasta Downloads, com notificação
        web.setDownloadListener((url, userAgent, contentDisposition, mimetype, tamanho) -> {
            String nome = URLUtil.guessFileName(url, contentDisposition, mimetype);
            DownloadManager.Request rq = new DownloadManager.Request(Uri.parse(url));
            String cookie = CookieManager.getInstance().getCookie(url);
            if (cookie != null) {
                rq.addRequestHeader("Cookie", cookie);
            }
            rq.addRequestHeader("Authorization", "Bearer " + token());
            rq.setMimeType(mimetype);
            rq.setTitle(nome);
            rq.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED);
            rq.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, nome);
            ((DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE)).enqueue(rq);
            Toast.makeText(this, "Baixando " + nome + "…", Toast.LENGTH_SHORT).show();
        });
    }

    @Override
    protected void onActivityResult(int codigo, int resultado, Intent dados) {
        super.onActivityResult(codigo, resultado, dados);
        if (codigo == PEDIDO_ARQUIVO && seletorArquivos != null) {
            seletorArquivos.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultado, dados));
            seletorArquivos = null;
        }
    }

    // ------------------------------------------------------------------ instalar APK baixado
    private final BroadcastReceiver downloadTerminou = new BroadcastReceiver() {
        @Override
        public void onReceive(Context ctx, Intent intent) {
            long id = intent.getLongExtra(DownloadManager.EXTRA_DOWNLOAD_ID, -1);
            DownloadManager dm = (DownloadManager) getSystemService(Context.DOWNLOAD_SERVICE);
            Uri uri = dm.getUriForDownloadedFile(id);
            if (uri == null) {
                return; // não é um download nosso, ou falhou
            }
            String mime = dm.getMimeTypeForDownloadedFile(id);
            if ("application/vnd.android.package-archive".equals(mime)) {
                instala(uri);
            } else {
                Toast.makeText(MainActivity.this, "Arquivo salvo na pasta Downloads.", Toast.LENGTH_LONG).show();
            }
        }
    };

    private void instala(Uri uri) {
        if (Build.VERSION.SDK_INT >= 26 && !getPackageManager().canRequestPackageInstalls()) {
            Toast.makeText(this, "Permita que o Betina & IA instale apps e baixe o arquivo de novo.",
                    Toast.LENGTH_LONG).show();
            startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:" + getPackageName())));
            return;
        }
        Intent i = new Intent(Intent.ACTION_VIEW);
        i.setDataAndType(uri, "application/vnd.android.package-archive");
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            startActivity(i);
        } catch (ActivityNotFoundException e) {
            Toast.makeText(this, "Abra o arquivo pela pasta Downloads para instalar.", Toast.LENGTH_LONG).show();
        }
    }

    // ------------------------------------------------------------------ voz (microfone)
    @Override
    public void onRequestPermissionsResult(int codigo, String[] permissoes, int[] resultados) {
        super.onRequestPermissionsResult(codigo, permissoes, resultados);
        if (codigo == PEDIDO_MICROFONE) {
            boolean liberou = resultados.length > 0 && resultados[0] == PackageManager.PERMISSION_GRANTED;
            Toast.makeText(this, liberou ? "Microfone liberado. Toque em Falar de novo."
                    : "Sem o microfone não dá para falar com a IA.", Toast.LENGTH_LONG).show();
        }
    }

    /** Para e solta o gravador. Devolve false se a gravação foi curta demais para ser válida. */
    private boolean paraGravador() {
        if (gravador == null) {
            return false;
        }
        boolean ok = true;
        try {
            gravador.stop();
        } catch (RuntimeException muitoCurta) {
            ok = false;
        }
        gravador.release();
        gravador = null;
        return ok;
    }

    private void js(final String codigo) {
        runOnUiThread(() -> web.evaluateJavascript(codigo, null));
    }

    private void enviaVoz(File arquivo) {
        try {
            String limite = "----ialocal" + System.currentTimeMillis();
            HttpURLConnection c = (HttpURLConnection) new URL(servidor() + "/stt").openConnection();
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setConnectTimeout(15000);
            c.setReadTimeout(120000);
            c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + limite);
            c.setRequestProperty("Authorization", "Bearer " + token());
            try (DataOutputStream out = new DataOutputStream(c.getOutputStream());
                 FileInputStream in = new FileInputStream(arquivo)) {
                out.writeBytes("--" + limite + "\r\n");
                out.writeBytes("Content-Disposition: form-data; name=\"audio\"; filename=\"voz.m4a\"\r\n");
                out.writeBytes("Content-Type: audio/mp4\r\n\r\n");
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) > 0) {
                    out.write(buf, 0, n);
                }
                out.writeBytes("\r\n--" + limite + "--\r\n");
            }
            int codigo = c.getResponseCode();
            if (codigo != 200) {
                js("window.onNativeStt('', " + JSONObject.quote("O servidor respondeu " + codigo) + ")");
                return;
            }
            ByteArrayOutputStream corpo = new ByteArrayOutputStream();
            try (InputStream in = c.getInputStream()) {
                byte[] buf = new byte[4096];
                int n;
                while ((n = in.read(buf)) > 0) {
                    corpo.write(buf, 0, n);
                }
            }
            String texto = new JSONObject(corpo.toString("UTF-8")).optString("text", "");
            js("window.onNativeStt(" + JSONObject.quote(texto) + ", null)");
        } catch (Exception e) {
            js("window.onNativeStt('', " + JSONObject.quote("Falha ao enviar a voz: " + e.getMessage()) + ")");
        } finally {
            arquivo.delete();
        }
    }

    // ------------------------------------------------------------------ ponte com a página
    /** Funções que a página (Betina & IA) chama de dentro do WebView. */
    private class Ponte {
        @JavascriptInterface
        public boolean isApp() {
            return true;
        }

        @JavascriptInterface
        public void openSettings() {
            runOnUiThread(() -> mostraConfig());
        }

        @JavascriptInterface
        public void openTailscale() {
            runOnUiThread(() -> abreTailscale());
        }

        @JavascriptInterface
        public void retry() {
            runOnUiThread(() -> carrega());
        }

        @JavascriptInterface
        public boolean startRec() {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                runOnUiThread(() -> requestPermissions(
                        new String[]{Manifest.permission.RECORD_AUDIO}, PEDIDO_MICROFONE));
                return false;
            }
            try {
                arquivoVoz = new File(getCacheDir(), "voz.m4a");
                gravador = Build.VERSION.SDK_INT >= 31 ? new MediaRecorder(MainActivity.this) : new MediaRecorder();
                gravador.setAudioSource(MediaRecorder.AudioSource.MIC);
                gravador.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4);
                gravador.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);
                gravador.setAudioSamplingRate(16000);
                gravador.setAudioChannels(1);
                gravador.setAudioEncodingBitRate(64000);
                gravador.setOutputFile(arquivoVoz.getAbsolutePath());
                gravador.prepare();
                gravador.start();
                return true;
            } catch (Exception e) {
                paraGravador();
                return false;
            }
        }

        @JavascriptInterface
        public void stopRec() {
            final File arquivo = arquivoVoz;
            if (!paraGravador() || arquivo == null) {
                js("window.onNativeStt('', " + JSONObject.quote("Gravação curta demais. Tente de novo.") + ")");
                return;
            }
            new Thread(() -> enviaVoz(arquivo)).start();
        }

        @JavascriptInterface
        public void cancelRec() {
            paraGravador();
            if (arquivoVoz != null) {
                arquivoVoz.delete();
            }
        }
    }
}
