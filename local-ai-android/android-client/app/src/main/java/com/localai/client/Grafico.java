package com.localai.client;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.Path;
import android.view.View;

/** Gráfico estilo osciloscópio: linha neon sobre grade escura, com os últimos valores lidos. */
public class Grafico extends View {
    private static final int PONTOS = 60;
    private final float[] valores = new float[PONTOS];
    private int usados = 0;
    private final float maximo;
    private final Paint linha = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint grade = new Paint();
    private final Paint brilho = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Path caminho = new Path();

    public Grafico(Context c, int cor, float maximo) {
        super(c);
        this.maximo = maximo;
        float d = c.getResources().getDisplayMetrics().density;
        linha.setColor(cor);
        linha.setStyle(Paint.Style.STROKE);
        linha.setStrokeWidth(1.6f * d);
        brilho.setColor(cor);
        brilho.setStyle(Paint.Style.STROKE);
        brilho.setStrokeWidth(4f * d);
        brilho.setAlpha(50);
        grade.setColor(0xFF16313A);
        grade.setStrokeWidth(1f);
    }

    public void adiciona(float v) {
        if (usados == PONTOS) {
            System.arraycopy(valores, 1, valores, 0, PONTOS - 1);
            usados--;
        }
        valores[usados++] = v;
        invalidate();
    }

    @Override
    protected void onDraw(Canvas c) {
        float w = getWidth(), h = getHeight();
        for (int i = 1; i < 4; i++) {
            c.drawLine(0, h * i / 4, w, h * i / 4, grade);
        }
        for (int i = 1; i < 6; i++) {
            c.drawLine(w * i / 6, 0, w * i / 6, h, grade);
        }
        if (usados < 2) {
            return;
        }
        caminho.reset();
        for (int i = 0; i < usados; i++) {
            float x = w * i / (PONTOS - 1);
            float y = h - 2 - (h - 4) * Math.min(1f, Math.max(0f, valores[i] / maximo));
            if (i == 0) {
                caminho.moveTo(x, y);
            } else {
                caminho.lineTo(x, y);
            }
        }
        c.drawPath(caminho, brilho);
        c.drawPath(caminho, linha);
    }
}
