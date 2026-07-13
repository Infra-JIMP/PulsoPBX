# Monitor de Ramais - Guia de Configuracao

## 1. Criar usuario dedicado da AMI no MikoPBX

Acesse `https://192.168.1.254/` com o login admin.

1. Menu **Sistema -> Asterisk Manager Interface (AMI)**.
2. Se a AMI estiver desativada, ative-a.
3. Clique em adicionar novo usuario:
   - Username: `ramais_monitor`
   - Password: gere uma senha forte (ex.: gerenciador de senhas) - **nao reaproveite a senha do admin**.
   - Permissoes: marque apenas **Call** e **Reporting**. Nao marque `System`/`Command`.
   - Network Filter: restrinja ao IP da maquina `DKS-FG-006` (nao deixe "todos os IPs").
4. Salve e aplique as configuracoes.
5. Preencha `AMI_USER` e `AMI_SECRET` no arquivo `.env` (copie `.env.example` para `.env` primeiro).

Depois disso, rode para validar:

```
.venv\Scripts\python.exe test_ami_connection.py
```

Deve aparecer `[OK] Login AMI bem-sucedido` e a lista de ramais encontrados.

## 2. Criar app WhatsApp Cloud API (Meta for Developers)

1. Acesse https://developers.facebook.com/ e crie/entre com uma conta.
2. Crie um App do tipo **Business**.
3. Dentro do app, adicione o produto **WhatsApp**.
4. Em **WhatsApp -> Introducao/API Setup**, a Meta ja fornece um numero de teste gratuito e um token temporario (validade 24h) - use-os para os primeiros testes.
5. Ainda nessa tela, em "To" (destinatarios de teste), adicione seu numero (e de quem mais deva receber alerta) e confirme o codigo recebido por WhatsApp.
6. Anote o **Phone Number ID** exibido na tela de API Setup.
7. Para nao depender de um token que expira em 24h:
   - Va em **Business Settings -> Usuarios -> Usuarios do sistema** e crie um System User.
   - Gere um token permanente para esse System User com a permissao `whatsapp_business_messaging`.
8. Crie um **Message Template** (em WhatsApp Manager -> Modelos de mensagem):
   - Nome: `ramal_alerta`
   - Categoria: Utilidade/Utility
   - Idioma: Portugues (BR)
   - Corpo: `Alerta de ramal: o ramal {{1}} {{2}} em {{3}}.`
   - Envie para aprovacao (costuma sair em minutos a poucas horas).
9. Preencha no `.env`: `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_RECIPIENTS` (numeros com DDI, ex.: `5547999999999`, separados por virgula). A versao da rota fica em `WHATSAPP_GRAPH_API_VERSION` e pode ser atualizada sem mudar o codigo.

O monitor coloca cada alerta confirmado em uma fila separada por destinatario. Se a Meta ou a rede falhar, ele tenta novamente sem interromper o monitoramento da AMI. Por padrao sao 3 tentativas, com espera de 15s e 30s entre elas; ajuste `ALERT_MAX_ATTEMPTS` e `ALERT_RETRY_BASE_SECONDS` no `.env` se necessario.

As entregas ficam registradas no SQLite local. Se o servico reiniciar durante um envio, somente os destinatarios ainda pendentes voltam para a fila; quem ja recebeu nao recebe a mesma mensagem novamente. Transicoes repetidas do mesmo ramal e estado tambem sao deduplicadas.

Depois de configurar as credenciais, use **Enviar teste** na secao **Entregas WhatsApp** do painel. O teste passa pela mesma fila, template, destinatarios, retentativas e historico usados em uma queda real, mas a mensagem informa claramente que nenhum ramal caiu. O painel pede confirmacao e impede testes repetidos por 60 segundos (`ALERT_TEST_COOLDOWN_SECONDS`).

**Enquanto o template nao for aprovado**: defina `WHATSAPP_USE_TEMPLATE=false` no `.env` para testar com mensagem de texto livre. Isso só funciona se o destinatário tiver mandado alguma mensagem para o número de teste nas últimas 24h (é uma regra do WhatsApp, não do nosso código). Depois que o template `ramal_alerta` for aprovado, mude para `WHATSAPP_USE_TEMPLATE=true` — assim o alerta funciona a qualquer momento, sem depender dessa janela de 24h.

## 3. Rodar o monitor manualmente (antes de virar tarefa agendada)

```
cd C:\Users\eduardo.p\Desktop\Ramais\ramais_monitor
.venv\Scripts\python.exe main.py
```

Acompanhe `logs/monitor.log`. Pressione Ctrl+C para parar.

AMI e WhatsApp são opcionais: se `AMI_USER`/`AMI_SECRET` ou as variáveis do WhatsApp ainda não estiverem no `.env`, o programa sobe assim mesmo (só loga um aviso) - dá pra ver o painel funcionando antes mesmo de terminar a configuração externa.

## 3.1. Painel web de status

Com o `main.py` rodando, abra no navegador (de qualquer PC da rede interna):

```
http://172.20.171.206:8080/
```

(esse é o IP da `DKS-FG-006` na rede interna; `DASHBOARD_PORT` no `.env` muda a porta se precisar). O painel (estilo NOC, tabela densa) atualiza sozinho a cada 3s e mostra:
- Contadores no topo: total de ramais, online e offline (o bloco "Offline" fica vermelho quando há algum fora).
- Um quarto contador, **Em confirmação**, mostra leituras ainda dentro do debounce. Uma queda só vira "Offline confirmado" e pode gerar alerta depois desse tempo, evitando falsos positivos.
- Selos de saúde da AMI (conectada / desconectada / não configurada / demonstração) e do WhatsApp (ativo / não configurado).
- Faixa de prioridade operacional, tabela com os offline no topo (linha destacada em vermelho), nome, setor, status de entrega do alerta e há quanto tempo está no estado atual.
- Campo de busca (por número, nome ou setor) e filtros Todos / Atenção / Offline / Online.
- Histórico de incidentes recentes: queda confirmada, retorno, duração e situação atual. Ele usa SQLite local (`data/pulsopbx.db`) e é preservado após reinícios; a pasta `data/` não é versionada nem sobrescrita no deploy.
- Histórico de entregas do WhatsApp: fila, tentativas, sucesso ou falha por evento, também preservado no SQLite. O botão **Enviar teste** valida a integração sem simular a queda de um ramal.

### 3.2. Nomes dos ramais (automático via API do MikoPBX)

O painel busca o nome do funcionário de cada ramal direto do MikoPBX, via API REST:

1. No painel do MikoPBX: **Sistema → Chaves API → criar nova chave**.
   - Descrição: `ramais` (ou o que preferir).
   - Permissões: **Leitura** em **Employees Management** (`/api/v3/employees`) — o resto fica "Sem acesso".
   - Filtro de rede: restrinja se o painel oferecer essa opção; se só houver "qualquer endereço" ou "somente locais", use "qualquer endereço" mesmo (a chave de 64 caracteres já protege o acesso, e o escopo é só leitura de 2 dados de baixa sensibilidade).
   - Salve e copie a chave gerada (ela some depois, então copie na hora).
2. Preencha no `.env`: `MIKOPBX_API_KEY` (a chave copiada). `MIKOPBX_API_URL` já vem certo por padrão.
3. Pronto — o nome de cada ramal aparece sozinho no painel, atualizado a cada 5 minutos (`MIKOPBX_NAMES_REFRESH_SECONDS`).

Por padrão, a consulta aceita o certificado interno/autoassinado comum no MikoPBX (`MIKOPBX_VERIFY_TLS=false`). Só altere para `true` depois que o PBX apresentar um certificado válido e confiável para a máquina do monitor.

O MikoPBX não tem campo de "setor" separado (o setor, quando existe, já vem dentro do próprio nome, ex.: `Engenharia - Edson`). Se quiser sobrescrever algum nome específico ou adicionar um setor à parte, use o `ramais_nomes.json` manual (opcional, só para exceções):
1. Copie `ramais_nomes.example.json` para `ramais_nomes.json`.
2. Preencha só os ramais que quer sobrescrever:
   ```json
   {
     "1001": {"setor": "Atendimento"},
     "1002": {"nome": "João Vendas"}
   }
   ```
3. Salve. O arquivo é recarregado automaticamente (não precisa reiniciar o serviço) e tem prioridade sobre o nome vindo da API.

### 3.3. Ver o painel antes da AMI estar pronta (modo demonstração)

Para conferir o visual com ramais de exemplo, rode com `DEMO_MODE=true` no `.env` (ou como variável de ambiente). Nesse modo o monitor não se conecta à AMI real, mesmo que as credenciais estejam preenchidas. Lembre de voltar para `false` em produção.

## 4. Deploy 24/7 (JA CONFIGURADO)

Para robustez, o servico roda de uma **copia local** em `C:\Users\eduardo.p\ramais_monitor` na `DKS-FG-006`. O repositório em `C:\Users\eduardo.p\Desktop\Ramais\ramais_monitor` é a cópia de desenvolvimento/fonte versionada no GitHub.

### Como esta rodando hoje
- Tarefa agendada **`RamaisMonitor`** (criada por `install_task.ps1`): inicia no **logon** do usuario e reinicia sozinha se cair. Funciona sem admin, mas **so roda enquanto o usuario estiver logado** na `DKS-FG-006`.
- Painel: `http://localhost:8080/` na propria maquina.

### Upgrade recomendado para servico 24/7 de verdade (precisa de admin, 1 vez)
Para o monitor rodar mesmo apos reboot **sem ninguem logado**, e para liberar o painel a **outros PCs da rede**, rode como administrador:
```
powershell -ExecutionPolicy Bypass -File C:\Users\eduardo.p\ramais_monitor\install_system_task.ps1
```
Isso troca a tarefa para rodar como **SYSTEM** iniciando junto com o Windows, e abre a porta 8080 no firewall. Depois disso o painel fica acessivel em `http://172.20.171.206:8080/` de qualquer PC da rede.

### Scripts de gerenciamento (na pasta local e no compartilhamento)
- `install_task.ps1` - registra a tarefa de logon (sem admin). **Ja executado.**
- `install_system_task.ps1` - upgrade para servico SYSTEM + firewall (rodar como admin).
- `uninstall_task.ps1` - remove a tarefa e para o servico.
- `deploy_local.ps1` (na raiz do repositório) - depois de editar o código versionado, rode isto na `DKS-FG-006` para sincronizar a cópia local e reiniciar o serviço. **NÃO** copia `.env`/`.venv`/`logs`/`data` (esses ficam só na cópia local).

### Comandos uteis
- Ver estado: `Get-ScheduledTask -TaskName RamaisMonitor`
- Parar/iniciar: `Stop-ScheduledTask -TaskName RamaisMonitor` / `Start-ScheduledTask -TaskName RamaisMonitor`
- Logs: `C:\Users\eduardo.p\ramais_monitor\logs\monitor.log`
