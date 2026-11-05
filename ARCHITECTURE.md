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
- O `ListPublisher` mantém as listas de ramais e e-mails em Excel numa pasta,
  regravando somente quando um cadastro é criado, alterado ou arquivado
  (sem conferência periódica).
- O envio de e-mail fica desligado até `EMAIL_DISPATCH_ENABLED=true`.
- O SMTP, quando ligado, envia alertas de queda e de chamadas internas perdidas quando as
  regras locais permitem. Queda e retorno tem um unico destino operacional
  (`OUTAGE_ALERT_RECIPIENTS`, a equipe de TI); o colaborador nao recebe aviso
  do proprio ramal e acompanha tudo pelo painel e pelos relatorios.

## Painel em nuvem (pulsopbx.vercel.app)

```text
Monitor local --(so quando muda, max. 1x/30s)--> /api/push -> Upstash Redis (1 chave)
Navegador --login (cookie)--> middleware --> CDN cache 15s --> /api/status -> Redis
```

- Codigo em `cloud/`; envio local em `cloud_sync.py` (`CLOUD_SYNC_*`).
- Sai da rede somente o status ao vivo: ramal, nome, setor, online/offline e
  desde quando. E-mails, historico, cadastros e listas ficam apenas locais.
- O login usa cookie assinado, e nao HTTP Basic, porque a CDN nao guarda em
  cache pedidos com `Authorization`; assim todos os usuarios compartilham a
  mesma leitura do Redis a cada 15s.
- Orcamento: pior caso ~2.900 envios/dia + ~2.400 leituras/dia, bem abaixo dos
  500 mil comandos/mes do plano gratuito do Upstash.

O painel local continua sendo o unico com historico, cadastros e listas.
Qualquer outro acesso remoto deve ser desenhado como uma funcionalidade nova,
com dados minimos e intervalo controlado.
