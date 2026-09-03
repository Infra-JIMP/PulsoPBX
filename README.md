# PulsoPBX

Monitor interno de ramais MikoPBX. A operacao e local: AMI e API do MikoPBX
alimentam o monitor, que guarda historico no SQLite local e serve o painel na
rede interna. Alertas por e-mail tambem saem diretamente desta maquina.

Nao ha integracao com Vercel, Neon ou qualquer banco externo. Isso elimina a
transferencia publica recorrente que causava consumo desnecessario.

## Inicio rapido

1. Copie `.env.example` para `.env` e informe somente as credenciais locais
   necessarias.
2. Execute `.venv\Scripts\python.exe -m unittest discover -s tests -t . -v`.
3. Rode `.venv\Scripts\python.exe main.py` ou use a tarefa `RamaisMonitor`.
4. Abra `http://IP-DA-MAQUINA:8080/` apenas pela rede interna.

Consulte [SETUP.md](SETUP.md) para configuracao detalhada e
[OPERATIONS.md](OPERATIONS.md) para a operacao segura.
