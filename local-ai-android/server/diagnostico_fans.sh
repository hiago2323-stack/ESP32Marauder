#!/usr/bin/env bash
# Mostra o que dá para controlar nas ventoinhas pelo Linux (GPU e CPU).
#   bash diagnostico_fans.sh                   -> lista sensores e ventoinhas
#   sudo bash diagnostico_fans.sh testar hwmonN pwmM
#        -> põe esse PWM em 100% por 8 s e mostra se alguma ventoinha acelerou (depois restaura)

if [ "${1:-}" = "testar" ]; then
  d="/sys/class/hwmon/$2"; p="$d/$3"
  [ -w "$p" ] || { echo "Sem permissão ou não existe: $p (rode com sudo)"; exit 1; }
  orig=$(cat "${p}_enable" 2>/dev/null || echo "")
  restaura() { [ -n "$orig" ] && echo "$orig" > "${p}_enable"; echo "Restaurado ao modo original ($orig)."; }
  trap restaura EXIT
  leitura() { for f in "$d"/fan*_input; do [ -e "$f" ] && printf '  %s=%s RPM' "$(basename "${f%_input}")" "$(cat "$f")"; done; echo; }
  echo "Antes:"; leitura
  echo 1 > "${p}_enable"; echo 255 > "$p"; sleep 8
  echo "Com $3 em 100%:"; leitura
  echo "Se alguma ventoinha ACELEROU (e era a da CPU), use: FANCTL_CPU_PWM=$p"
  exit 0
fi

echo "== Placa de vídeo NVIDIA =="
nvidia-smi --query-gpu=name,temperature.gpu,fan.speed --format=csv 2>/dev/null || echo "nvidia-smi não encontrado"
echo
echo "== Sensores (hwmon): temperatura da CPU, ventoinhas (RPM) e controles PWM =="
achou_pwm=0
for d in /sys/class/hwmon/hwmon*; do
  nome=$(cat "$d/name" 2>/dev/null) || continue
  fans=""; pwms=""
  for f in "$d"/fan*_input; do [ -e "$f" ] && fans="$fans $(basename "${f%_input}")=$(cat "$f")rpm"; done
  for p in "$d"/pwm[0-9]; do [ -e "$p" ] && { pwms="$pwms $(basename "$p")"; achou_pwm=1; }; done
  case "$nome" in k10temp|coretemp|zenpower) echo "$(basename "$d")  $nome  CPU: $(( $(cat "$d/temp1_input") / 1000 )) °C" ;; esac
  [ -n "$fans$pwms" ] && echo "$(basename "$d")  $nome  ventoinhas:${fans:- nenhuma}  PWM:${pwms:- nenhum}"
done
echo
if [ "$achou_pwm" = 1 ]; then
  echo "Sua placa-mãe expõe controles PWM no Linux. Para a ventoinha da CPU:"
  echo "  1) Descubra qual PWM comanda a ventoinha da CPU:  sudo bash diagnostico_fans.sh testar hwmonN pwmM"
  echo "  2) Coloque a linha  FANCTL_CPU_PWM=/sys/class/hwmon/hwmonN/pwmM  no arquivo ~/localai/server/fan.env"
  echo "  3) sudo systemctl restart localai-fan"
else
  echo "Nenhum PWM de ventoinha exposto pelo Linux (comum em placas-mãe AMD novas sem o driver do chip)."
  echo "A ventoinha da CPU continua sendo controlada pela BIOS. Para ela acelerar sob demanda:"
  echo "  - Entre na BIOS (tecla Del/F2 ao ligar) > Monitor/Hardware Monitor/Q-Fan/Smart Fan"
  echo "  - Escolha o perfil 'Standard' ou 'Turbo' (ou uma curva própria: ~40% a 40 °C, 70% a 65 °C, 100% a 80 °C)"
fi
echo
echo "Controle automático da ventoinha da GPU: systemctl status localai-fan"
