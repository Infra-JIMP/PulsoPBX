# Arquitetura local-first

```text
MikoPBX (AMI e API) -> Monitor Python local -> SQLite local -> Painel LAN
                                              -> SMTP corporativo
```

- A AMI entrega mudancas de estado em tempo real ao monitor.
- O `StateTracker` atualiza o painel; o navegador consulta apenas a maquina
  interna do monitor.
- SQLite conserva diretorio, incidentes, disponibilidade, chamadas e entregas
  de alertas. Ele nao e necessario para detectar uma mudanca ao vivo.
- O SMTP envia alertas de queda e de chamadas internas perdidas quando as
  regras locais permitem. Queda e retorno tem um unico destino operacional
  (`OUTAGE_ALERT_RECIPIENTS`, a equipe de TI); o colaborador nao recebe aviso
  do proprio ramal e acompanha tudo pelo painel e pelos relatorios.

O painel nao publica historico, cadastros ou dados de ramais para Internet.
Qualquer acesso remoto futuro deve ser desenhado como uma funcionalidade nova,
com dados minimos e intervalo controlado.
