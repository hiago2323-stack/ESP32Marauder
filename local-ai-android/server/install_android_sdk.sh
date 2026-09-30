#!/usr/bin/env bash
# Instala o Android SDK (linha de comando) em ~/Android/Sdk para o /build funcionar.
set -euo pipefail
SDK="$HOME/Android/Sdk"
mkdir -p "$SDK/cmdline-tools"
cd /tmp
curl -L --fail -o cmdtools.zip "https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
unzip -q -o cmdtools.zip -d "$SDK/cmdline-tools"
rm -rf "$SDK/cmdline-tools/latest"
mv "$SDK/cmdline-tools/cmdline-tools" "$SDK/cmdline-tools/latest"
export JAVA_HOME="${JAVA_HOME:-/usr/lib/jvm/java-17-openjdk-amd64}"
yes | "$SDK/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null || true
"$SDK/cmdline-tools/latest/bin/sdkmanager" "platform-tools" "platforms;android-34" "build-tools;34.0.0"

# Registra no .env do servidor para o run.sh exportar
ENVF="$(dirname "$0")/.env"
grep -q '^ANDROID_HOME=' "$ENVF" 2>/dev/null || {
  echo "ANDROID_HOME=$SDK" >> "$ENVF"
  echo "JAVA_HOME=$JAVA_HOME" >> "$ENVF"; }
echo "==> Android SDK instalado em $SDK"
