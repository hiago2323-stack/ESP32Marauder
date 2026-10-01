#!/usr/bin/env bash
# Espera o PRIMEIRO instalador terminar e, em seguida, roda sozinho o instalador
# completo (7B + telemetria + voz + memória). Deixe este terminal aberto.
#
# Uso (na pasta onde estão os dois arquivos):
#   bash esperar_e_instalar.sh
# Para o modelo 3B no lugar do 7B:   MODELO=3b bash esperar_e_instalar.sh
# Para checar com mais frequência:   INTERVALO=2 bash esperar_e_instalar.sh
set -u
NOVO="${NOVO:-instalar_tudo_v2.sh}"
cd "$(dirname "$0")"
[ -f "$NOVO" ] || { echo "Não achei $NOVO nesta pasta. Salve-o junto deste arquivo."; exit 1; }

echo "Digite a senha UMA vez; assim eu continuo sozinho quando o primeiro acabar."
sudo -v || exit 1
( while true; do sudo -n true; sleep 50; kill -0 "$$" 2>/dev/null || exit; done ) >/dev/null 2>&1 &

# O primeiro instalador pode ter qualquer um destes nomes
PADRAO='^(/usr)?(/bin/)?bash( -[A-Za-z]+)* ([^ ]*/)?(instalar_tudo|install_all)\.sh( |$)'

if ! pgrep -f "$PADRAO" >/dev/null; then
  echo "Não vejo o primeiro instalador rodando (já terminou?). Começando agora."
else
  echo "Primeiro instalador em andamento. Esperando terminar (checo a cada ${INTERVALO:-20} segundos)..."
  while pgrep -f "$PADRAO" >/dev/null; do sleep "${INTERVALO:-20}"; done
  echo
  echo "=== O primeiro instalador terminou. Resumo do final do log: ==="
  tail -n 15 "$HOME/localai-install.log" 2>/dev/null
  echo "================================================================"
  sleep 3
fi

echo "=== Iniciando o instalador completo (modelo: ${MODELO:-7b}) ==="
MODELO="${MODELO:-7b}" bash "$NOVO"
echo
echo "Terminou. Se não houve erro acima, REINICIE o PC."
