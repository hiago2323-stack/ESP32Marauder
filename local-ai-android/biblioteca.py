@@BIBLIOTECA@@
EOF''',1)
s=s.replace('step "15/15 Atalho na área de trabalho" instalar_atalho','step "15/15 Atalho na área de trabalho" instalar_atalho\nstep "Biblioteca no disco grande"     instalar_biblioteca',1)
open(p,'w',encoding='utf-8').write(s)

p='/tmp/claude-0/mkupd.py'
m=open(p,encoding='utf-8').read()
m=m.replace("'instalar_tailscale',","'instalar_tailscale','disco_biblioteca','persistir_montagem','instalar_biblioteca',")
m=m.replace(".replace('@@CODEGEN@@',r('codegen.py'))",".replace('@@CODEGEN@@',r('codegen.py')).replace('@@BIBLIOTECA@@',r('biblioteca.py'))")
m=m.replace('''instalar_tailscale || echo "AVISO: o Tailscale não ficou conectado (rode: sudo tailscale up)."''','''instalar_tailscale || echo "AVISO: o Tailscale não ficou conectado (rode: sudo tailscale up)."
[ -n "${SEM_BIBLIOTECA:-}" ] || instalar_biblioteca || echo "AVISO: a biblioteca no disco grande não ficou pronta (rode de novo: bash atualizar.sh)."''')
open(p,'w',encoding='utf-8').write(m)
p='/tmp/claude-0/chk.py'
c=open(p,encoding='utf-8').read()
c=c.replace('("codegen.py","server/codegen.py")','("codegen.py","server/codegen.py"),("biblioteca.py","server/biblioteca.py")')
open(p,'w',encoding='utf-8').write(c)
