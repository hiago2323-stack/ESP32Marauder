package com.localai.client;

import android.content.Context;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.text.SpannableStringBuilder;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONObject;

import java.util.Iterator;
import java.util.Locale;

/**
 * Faixa de telemetria em tempo real do PC (sempre visível no topo). Tocando nela abre o painel
 * com gráficos estilo osciloscópio. Visual "terminal de segurança": fundo escuro, verde neon e ciano.
 */
public class PainelTele extends LinearLayout {
    static final int FUNDO = 0xFF05090D, CARTAO = 0xFF0C161E, LINHA = 0xFF16313A;
    static final int VERDE = 0xFF00E58F, CIANO = 0xFF22D3EE, AMBAR = 0xFFFFB020, VERMELHO = 0xFFFF4D5E, MUDO = 0xFF6F9A8C;

    private final TextView selos, metricas, detalhes, video, seta;
    private final LinearLayout painel;
    private final Grafico gGpu, gVram, gTemp, gCpu, gCpuT, gRam;
    private int falhas = 0;
    private boolean temDados = false;

    public PainelTele(Context c) {
        super(c);
        setOrientation(VERTICAL);
        setBackgroundColor(FUNDO);

        LinearLayout faixa = new LinearLayout(c);
        faixa.setOrientation(HORIZONTAL);
        faixa.setGravity(Gravity.CENTER_VERTICAL);
        faixa.setPadding(dp(12), dp(6), dp(12), dp(6));

        ImageView logo = new ImageView(c);
        logo.setImageResource(R.drawable.ic_marca);
        faixa.addView(logo, new LayoutParams(dp(30), dp(34)));

        LinearLayout textos = new LinearLayout(c);
        textos.setOrientation(VERTICAL);
        textos.setPadding(dp(10), 0, dp(6), 0);
        selos = texto(c, 11, VERDE, true);
        metricas = texto(c, 12, CIANO, false);
        video = texto(c, 11, AMBAR, false);
        video.setVisibility(GONE);
        textos.addView(selos);
        textos.addView(metricas);
        textos.addView(video);
        faixa.addView(textos, new LayoutParams(0, LayoutParams.WRAP_CONTENT, 1f));

        seta = texto(c, 16, MUDO, true);
        seta.setText("▾");
        faixa.addView(seta);
        faixa.setOnClickListener(v -> alterna());
        addView(faixa);

        painel = new LinearLayout(c);
        painel.setOrientation(VERTICAL);
        painel.setPadding(dp(12), dp(4), dp(12), dp(10));
        painel.setVisibility(GONE);

        gGpu = grafico(c, "// GPU  ·  uso %", VERDE);
        gVram = grafico(c, "// VRAM  ·  uso %", VERDE);
        gTemp = grafico(c, "// GPU  ·  temperatura °C", AMBAR);
        gCpu = grafico(c, "// CPU  ·  uso %", CIANO);
        gCpuT = grafico(c, "// CPU  ·  temperatura °C", AMBAR);
        gRam = grafico(c, "// RAM  ·  uso %", CIANO);
        detalhes = texto(c, 12, 0xFFD7F5E6, false);
        detalhes.setPadding(0, dp(8), 0, 0);
        painel.addView(detalhes);
        TextView dica = texto(c, 10, MUDO, false);
        dica.setText("toque na faixa para recolher");
        painel.addView(dica);
        addView(painel);

        View fio = new View(c);
        fio.setBackgroundColor(LINHA);
        addView(fio, new LayoutParams(LayoutParams.MATCH_PARENT, 1));
        mostraOffline("conectando…");
    }

    private void alterna() {
        boolean abrir = painel.getVisibility() == GONE;
        painel.setVisibility(abrir ? VISIBLE : GONE);
        seta.setText(abrir ? "▴" : "▾");
    }

    /** Mostra (ou esconde) o vídeo que está sendo gerado no PC, vindo de /video/estado. */
    public void atualizaVideo(JSONObject j) {
        if (j == null || !j.optBoolean("rodando", false)) {
            video.setVisibility(GONE);
            return;
        }
        String m = j.optString("msg", "");
        video.setText("🎬 " + (m.isEmpty() ? "gerando vídeo…" : m));
        video.setVisibility(VISIBLE);
    }

    private Grafico grafico(Context c, String nome, int cor) {
        painel.addView(titulo(c, nome));
        Grafico g = new Grafico(c, cor, 100f);
        painel.addView(g, new LayoutParams(LayoutParams.MATCH_PARENT, dp(44)));
        return g;
    }

    private int dp(int v) {
        return (int) (v * getResources().getDisplayMetrics().density);
    }

    private TextView texto(Context c, int sp, int cor, boolean negrito) {
        TextView t = new TextView(c);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(cor);
        t.setTypeface(Typeface.MONOSPACE, negrito ? Typeface.BOLD : Typeface.NORMAL);
        t.setGravity(Gravity.START);
        return t;
    }

    private TextView titulo(Context c, String s) {
        TextView t = texto(c, 11, VERDE, true);
        t.setText(s);
        t.setPadding(0, dp(8), 0, dp(2));
        return t;
    }

    private static void junta(SpannableStringBuilder b, String s, int cor) {
        int ini = b.length();
        b.append(s);
        b.setSpan(new ForegroundColorSpan(cor), ini, b.length(), Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
    }

    private static int cor(double v, double amarelo, double vermelho) {
        return v >= vermelho ? VERMELHO : v >= amarelo ? AMBAR : VERDE;
    }

    /** Falha ao ler: depois de 3 falhas seguidas mostra o PC como sem link. */
    public void falhou(String motivo) {
        if (++falhas >= 3) {
            mostraOffline(motivo);
        }
    }

    private void mostraOffline(String motivo) {
        SpannableStringBuilder b = new SpannableStringBuilder();
        junta(b, "BETINA & IA  ", VERDE);
        junta(b, temDados ? "✕ SEM LINK" : "… " + motivo, temDados ? VERMELHO : AMBAR);
        selos.setText(b);
        metricas.setText(temDados ? "sem resposta do PC (" + motivo + ")" : "aguardando o PC");
        metricas.setTextColor(MUDO);
    }

    public void atualiza(JSONObject j) {
        falhas = 0;
        temDados = true;
        metricas.setTextColor(CIANO);
        JSONObject gpu = j.optJSONObject("gpu"), cpu = j.optJSONObject("cpu"), ram = j.optJSONObject("ram");
        String llm = j.optString("llm", "");

        SpannableStringBuilder s = new SpannableStringBuilder();
        junta(s, "BETINA & IA  ", VERDE);
        junta(s, "● PC ONLINE  ", VERDE);
        junta(s, llm.equals("ok") ? "● IA PRONTA" : llm.equals("carregando") ? "◐ IA CARREGANDO" : "✕ IA PARADA",
                llm.equals("ok") ? VERDE : llm.equals("carregando") ? AMBAR : VERMELHO);
        selos.setText(s);

        SpannableStringBuilder m = new SpannableStringBuilder();
        StringBuilder d = new StringBuilder();
        if (gpu != null) {
            double t = gpu.optDouble("temp", 0), u = gpu.optDouble("uso", 0);
            double mu = gpu.optDouble("mem_usada", 0), mt = gpu.optDouble("mem_total", 0);
            junta(m, "GPU ", MUDO);
            junta(m, String.format(Locale.US, "%.0f°C %.0f%%  ", t, u), cor(t, 70, 80));
            junta(m, "VRAM ", MUDO);
            junta(m, String.format(Locale.US, "%.1f/%.1fG  ", mu / 1024, mt / 1024), cor(mt > 0 ? mu * 100 / mt : 0, 80, 95));
            gGpu.adiciona((float) u);
            gTemp.adiciona((float) t);
            gVram.adiciona(mt > 0 ? (float) (mu * 100 / mt) : 0f);
            d.append(String.format(Locale.US, "%s\nventoinha %.0f%%  ·  consumo %.0f W\n",
                    gpu.optString("nome", "GPU"), gpu.optDouble("ventoinha", 0), gpu.optDouble("watts", 0)));
        }
        if (cpu != null) {
            double t = cpu.optDouble("temp", Double.NaN), uso = cpu.optDouble("uso", 0);
            junta(m, "CPU ", MUDO);
            junta(m, String.format(Locale.US, "%.0f%% ", uso), cor(uso, 70, 90));
            junta(m, Double.isNaN(t) ? "--  " : String.format(Locale.US, "%.0f°C  ", t), Double.isNaN(t) ? MUDO : cor(t, 70, 80));
            gCpu.adiciona((float) uso);
            if (!Double.isNaN(t)) {
                gCpuT.adiciona((float) t);
            }
            d.append(String.format(Locale.US, "CPU %d núcleos\n", cpu.optInt("nucleos", 0)));
        }
        if (ram != null) {
            double us = ram.optDouble("usada", 0), to = ram.optDouble("total", 0);
            junta(m, "RAM ", MUDO);
            junta(m, String.format(Locale.US, "%.1f/%.1fG", us / 1024, to / 1024), cor(to > 0 ? us * 100 / to : 0, 75, 90));
            gRam.adiciona(to > 0 ? (float) (us * 100 / to) : 0f);
            d.append(String.format(Locale.US, "RAM %.0f de %.0f MB usados\n", us, to));
        }
        metricas.setText(m);

        JSONObject v = j.optJSONObject("ventoinhas");
        if (v != null && v.length() > 0) {
            d.append("ventoinhas:");
            for (Iterator<String> it = v.keys(); it.hasNext(); ) {
                String k = it.next();
                d.append("  ").append(k.substring(k.indexOf('/') + 1)).append(' ').append(v.optInt(k)).append(" rpm");
            }
            d.append('\n');
        }
        JSONObject vc = j.optJSONObject("ventoinha_ctl");
        if (vc != null && vc.optBoolean("ativo", false)) {
            JSONObject g = vc.optJSONObject("gpu");
            org.json.JSONArray pm = vc.optJSONArray("placa_mae");
            d.append("controle das ventoinhas: GPU ");
            d.append(g != null && !g.optString("metodo", "").isEmpty() ? g.optInt("pct", 0) + "% (" + g.optString("metodo") + ")" : "sem controle");
            d.append(" · placa-mãe ");
            d.append(pm != null && pm.length() > 0 ? pm.length() + " ventoinha(s)" : "sem controle");
        } else {
            d.append("controle das ventoinhas: desligado (rode bash atualizar.sh no PC)");
        }
        detalhes.setText(d.toString());
    }
}
