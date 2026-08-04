# Controle de consumo

O PulsoPBX nao depende de banco ou hospedagem publica. Assim, o consumo
recorrente de transferencia externa e zero.

- O fluxo ao vivo permanece: MikoPBX -> monitor local -> painel LAN.
- O SQLite recebe somente os eventos e alteracoes necessarios para historico.
- O navegador atualiza o status contra a maquina interna; isso nao gera trafego
  para Vercel, Neon ou Internet.
- O envio SMTP ocorre apenas quando uma regra de alerta e satisfeita.

Antes de qualquer futura integracao externa, medir tamanho de payload, enviar
somente alteracoes, limitar frequencia e nunca replicar historico completo em
ciclos curtos.
