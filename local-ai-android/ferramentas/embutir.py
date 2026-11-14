#!/usr/bin/env python3
"""Atualiza a cópia dos arquivos do servidor que vai DENTRO do atualizar.sh e do instalar_tudo_v2.sh.

Os dois scripts levam o servidor inteiro embutido (blocos  cat > ARQUIVO <<'EOF' ... EOF), para o PC não depender do
GitHub. Sempre que um arquivo de server/ mudar, rode:

    python3 ferramentas/embutir.py            (atualiza os dois scripts)
    python3 ferramentas/embutir.py --conferir (só confere; sai com erro se algum estiver desatualizado)

Arquivo novo em server/ entra logo depois do bloco do codegen.py (acrescente-o em ARQUIVOS abaixo).
"""
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SCRIPTS = [RAIZ / "atualizar.sh", RAIZ / "instalar_tudo_v2.sh"]
# nome no script -> arquivo em server/
ARQUIVOS = {n: n for n in [
    "requirements.txt", "config.py", "main.py", "memory.py", "androidgen.py", "entregas.py", "esp32gen.py", "perfis.py",
    "telemetria.py", "fanctl.py", "recursos.py", "uploads.py", "imagens.py", "imggen.py", "codegen.py", "gerador.py",
    "biblioteca.py", "modelos.py", "video.py", "vidgen.py", "static/index.html",
    "agente.py", "visao.py", "perfil.py", "placa.py", "aprendizado.py"]}
ARQUIVOS['"$BASE/diagnostico_fans.sh"'] = "diagnostico_fans.sh"
ANCORA = "codegen.py"   # onde entram os arquivos novos


def bloco(nome: str, conteudo: str) -> str:
    return f"  cat > {nome} <<'EOF'\n{conteudo.rstrip(chr(10))}\nEOF\n"


def atualiza(texto: str) -> tuple[str, list[str]]:
    mudou = []
    for nome, arq in ARQUIVOS.items():
        conteudo = (RAIZ / "server" / arq).read_text(encoding="utf-8")
        if "\nEOF\n" in "\n" + conteudo + "\n":
            raise SystemExit(f"{arq} tem uma linha 'EOF' sozinha: isso quebraria o script")
        padrao = re.compile(r"^  cat > " + re.escape(nome) + r" <<'EOF'\n.*?\nEOF\n", re.S | re.M)
        m = padrao.search(texto)
        novo = bloco(nome, conteudo)
        if m:
            if m.group(0) != novo:
                texto = texto[:m.start()] + novo + texto[m.end():]
                mudou.append(arq)
        else:
            a = re.compile(r"^  cat > " + re.escape(ANCORA) + r" <<'EOF'\n.*?\nEOF\n", re.S | re.M).search(texto)
            if not a:
                raise SystemExit(f"não achei onde colocar {arq}")
            texto = texto[:a.end()] + novo + texto[a.end():]
            mudou.append(arq + " (novo)")
    return texto, mudou


def main():
    conferir = "--conferir" in sys.argv
    erro = False
    for s in SCRIPTS:
        texto = s.read_text(encoding="utf-8")
        novo, mudou = atualiza(texto)
        if conferir:
            if mudou:
                erro = True
                print(f"{s.name}: desatualizado ({', '.join(mudou)})")
            else:
                print(f"{s.name}: em dia")
        elif mudou:
            s.write_text(novo, encoding="utf-8")
            print(f"{s.name}: atualizado ({', '.join(mudou)})")
        else:
            print(f"{s.name}: já estava em dia")
    sys.exit(1 if erro else 0)


if __name__ == "__main__":
    main()
