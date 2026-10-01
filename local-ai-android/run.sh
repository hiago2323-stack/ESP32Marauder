#!/usr/bin/env bash
cd "$(dirname "$0")"
set -a; source .env; set +a
exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8080
EOF''')
a=t.index("# ------------------------------------------------------------- 8. Tailscale")
b=t.index("# ---------------------------------------------------- 9. Serviços no boot")
t=t[:a]+'''# ------------------------------------------------- 8. Voz (ouvir e falar)
instalar_voz() {
  mkdir -p "$HOME/models"
  local U=https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/faber/medium
  curl -L --fail -C - -o "$HOME/models/pt_BR-faber-medium.onnx" "$U/pt_BR-faber-medium.onnx" &&
  curl -L --fail -C - -o "$HOME/models/pt_BR-faber-medium.onnx.json" "$U/pt_BR-faber-medium.onnx.json" || return 1
  echo "==> Baixando o modelo que entende sua voz (~460 MB)"
  "$SRV/.venv/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
}

instalar_atalho() {
  mkdir -p "$HOME/.local/share/applications" "$HOME/Desktop"
  cat > "$HOME/.local/share/applications/ia-local.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Betina & IA
Comment=Conversar com a IA local por texto e voz
Exec=xdg-open http://localhost:8080
Icon=utilities-terminal
Terminal=false
Categories=Utility;
DESK
  cp "$HOME/.local/share/applications/ia-local.desktop" "$HOME/Desktop/" && chmod +x "$HOME/Desktop/ia-local.desktop"
}

'''+t[b:]
rep('step "8/9 Tailscale"                instalar_tailscale','step "8/9 Voz (ouvir e falar)"       instalar_voz')
rep('step "9/9 Serviços automáticos"     instalar_servicos','step "9/9 Serviços automáticos"     instalar_servicos\nstep "9/9 Atalho na área de trabalho" instalar_atalho')
rep('''echo "        3) sudo tailscale up      (abra o link e faça login)"
echo "        4) curl http://localhost:8080/health"''','''echo "        3) espere ~1 minuto e abra o atalho 'Betina & IA' na área de trabalho"
echo "           (ou o navegador em http://localhost:8080)"
echo "        Se não abrir:  systemctl status localai-llm localai-server"''')
open('/tmp/claude-0/tpl.sh','w').write(t)
r=lambda f:open('server/'+f).read().rstrip('\n')
out=t.replace('@@REQ@@',r('requirements.txt')).replace('@@CONFIG@@',r('config.py')).replace('@@MAIN@@',r('main.py')).replace('@@INDEX@@',r('static/index.html'))
open('instalar_tudo.sh','w').write(out)
