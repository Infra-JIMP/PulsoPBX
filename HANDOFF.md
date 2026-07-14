# PulsoPBX — Monitor de Ramais · Documento de Handoff

> Documento para quem vai continuar/colaborar no projeto. Explica **o que é**, **como
> funciona por dentro**, **como rodar/desenvolver**, **o que já está pronto**, **o que
> falta** e — o mais importante — o **conhecimento não-óbvio** (as "pegadinhas") que
> custou tempo descobrir. Leia a seção 9 (Pegadinhas) antes de mexer na AMI ou no envio.

Repositório: `https://github.com/Infra-JIMP/PulsoPBX` · branch `main`
Linguagem: Python 3.13 · Painel: HTML/CSS/JS puro (sem framework/build)

---

## 1. Para que serve

A empresa usa **MikoPBX** (PBX baseado em Asterisk) e **MicroSIP** como softphone em cada
estação. Quando o ramal de alguém cai (PC desligado, MicroSIP fechado, queda de rede local),
hoje ninguém percebe até uma ligação ser perdida.

O **PulsoPBX** monitora, em tempo real, o estado de registro de todos os ramais e:
1. mostra tudo num **painel web** ao vivo (status, nome, setor, há quanto tempo, histórico);
2. dispara **alerta no WhatsApp** para a equipe de infra quando um ramal fica indisponível,
   permitindo atendimento presencial antes de perder chamadas.

---

## 2. Visão geral da arquitetura

Um **único processo Python** (`main.py`) roda tudo num loop `asyncio`, sem banco externo
nem processo separado:

```
                       ┌─────────────────────────────────────────────┐
   MikoPBX / Asterisk  │                  main.py                     │
   ┌───────────────┐   │  ┌──────────────┐    ┌───────────────────┐   │
   │ AMI (porta    │◄──┼──┤ ami_client   │───►│ state (debounce)  │   │
   │ 5038)         │   │  │ eventos +    │    │ pendente→confirmado│  │
   └───────────────┘   │  │ ExtensionSt. │    └─────────┬─────────┘   │
                       │  └──────────────┘              │ tick 5s     │
   ┌───────────────┐   │  ┌──────────────┐    ┌─────────▼─────────┐   │
   │ REST API v3   │◄──┼──┤ mikopbx_api  │    │ tick_loop         │   │
   │ /employees    │   │  │ nomes (cache)│    │  ├► incidents (DB) │   │
   └───────────────┘   │  └──────────────┘    │  └► alerts.enqueue │   │
                       │                       └─────────┬─────────┘   │
   ┌───────────────┐   │  ┌──────────────┐    ┌─────────▼─────────┐   │
   │ WhatsApp Cloud│◄──┼──┤ notifier     │◄───┤ alerts (fila+retry)│  │
   │ API (Meta)    │   │  └──────────────┘    │  └► alert_store(DB) │  │
   └───────────────┘   │                       └───────────────────┘   │
                       │  ┌──────────────────────────────────────┐    │
   Navegador ◄─────────┼──┤ web (aiohttp) : / e /api/status      │    │
   (polling 3s)        │  │  lê state/alerts/incidents em memória │    │
                       │  └──────────────────────────────────────┘    │
                       └─────────────────────────────────────────────┘
                                   Persistência: data/pulsopbx.db (SQLite/WAL)
```

**Fluxo de um evento (ramal cai):**
1. `ami_client` recebe evento `ExtensionStatus` (ou a reconciliação periódica) → chama
   `on_snapshot(ramal, online)`.
2. `main.on_snapshot` filtra: só rastreia ramais que têm funcionário vinculado (via
   `mikopbx_api`); depois chama `tracker.update()`.
3. `state.py` guarda como **pendente** (ainda não alerta).
4. `tick_loop` (a cada 5s) chama `tracker.tick()`: se a mudança durou ≥ **debounce (30s)**,
   ela é **confirmada**.
5. Para cada mudança confirmada: grava no histórico de incidentes (`incidents`, SQLite) e
   coloca na fila de alertas (`alerts.enqueue`).
6. `alerts.run()` consome a fila e envia via `notifier` (WhatsApp), com **retry/backoff** e
   registro em `alert_store` (SQLite).
7. O painel (`web`) lê tudo da memória e devolve JSON; o front atualiza a cada 3s.

---

## 3. Módulos (arquivo por arquivo)

| Arquivo | Responsabilidade |
|---|---|
| `main.py` | Ponto de entrada. Monta config, logging, todos os componentes e roda as tasks `asyncio` (`tick_loop`, `run_dashboard`, `alerts.run`, `periodic_reconcile`, `mikopbx_names_loop`). Contém a função `on_snapshot` com o **filtro de ramais reais**. |
| `config.py` | Lê `.env` → dataclass `Config` (imutável). Tudo é opcional: sem AMI/WhatsApp/API o programa sobe mesmo assim (o painel mostra o que falta). |
| `ami_client.py` | Conexão AMI (biblioteca `panoramisk`). Consome eventos `ExtensionStatus` em tempo real + reconciliação periódica via ação `ExtensionStateList`. Normaliza para `online`/`offline`. |
| `state.py` | Máquina de estados por ramal com **debounce** (pendente → confirmado). `snapshot()` para o painel, `tick()` para confirmar mudanças, `recent_events()` para o histórico de transições. |
| `incidents.py` | Histórico persistente de quedas (SQLite). Abre um "incidente" quando o ramal cai e fecha quando volta, calculando a duração. |
| `alerts.py` | **Fila resiliente** de entrega de alertas. Um evento por transição confirmada; uma entrega independente por destinatário; retry com backoff exponencial; dedup de repetições; suporte a **teste manual**. Restaura pendências do SQLite ao reiniciar. |
| `alert_store.py` | Persistência (SQLite) dos eventos de alerta e suas entregas por destinatário. |
| `notifier.py` | Envio via **WhatsApp Cloud API** (Graph API da Meta). Template aprovado ou texto livre. Lança `WhatsAppNotificationError` em falha (a fila decide o retry). |
| `mikopbx_api.py` | Busca o nome do funcionário de cada ramal via REST API v3 (`GET /employees`). Cache em memória; se a chamada falhar, mantém o último resultado. |
| `names.py` | Lê `ramais_nomes.json` (opcional) para **sobrescrever** nome ou **adicionar setor** manualmente. Recarrega sozinho quando o arquivo muda. |
| `web.py` | Servidor **aiohttp**. Rotas: `GET /` (painel), `GET /api/status` (JSON com tudo), `POST /api/alerts/test` (dispara teste, com proteções), `GET /favicon.ico`. |
| `demo.py` | Dados de exemplo (`DEMO_MODE=true`) para trabalhar o visual sem AMI real. |
| `static/index.html` | O painel inteiro (HTML+CSS+JS puro, ~350 linhas de HTML + ~34 KB com estilos). Tema navy+laranja (PulsoPBX / Joinville Implementos). Faz polling de `/api/status`. |

**Scripts de operação (PowerShell):** `install_task.ps1` (tarefa no logon, sem admin),
`install_system_task.ps1` (upgrade para serviço SYSTEM + firewall, precisa admin),
`uninstall_task.ps1`, `deploy_local.ps1` (sincroniza repo→cópia local e reinicia).

**Testes:** `test_state.py`, `test_incidents.py`, `test_alerts.py`, `test_notifier.py`,
`test_web.py` — usam `unittest` da biblioteca padrão (sem dependência extra). 13 testes,
todos passando. `test_ami_connection.py` é um script manual de diagnóstico da AMI (precisa
do PBX real, não é teste automatizado).

---

## 4. Contrato da API (`GET /api/status`)

O painel consome este JSON (campos principais):

```jsonc
{
  "ami_status": "connected|disconnected|not_configured|demo",
  "last_reconcile_at": 1783683559.08,      // epoch da última sincronização completa
  "whatsapp_enabled": true,
  "whatsapp": { "configured": true, "recipient_count": 2, "test_available": true,
                "test_cooldown_seconds": 60, "latest_status": "sent" },
  "total": 36, "online": 21, "offline": 15,
  "confirming": 1,                          // leituras ainda dentro do debounce
  "extensions": [
    { "extension": "1001", "online": false, "since": 1783531060.1,
      "nome": "Financeiro - Thayse", "setor": "",
      "pending_status": null, "confirmation_remaining_seconds": 0,
      "alert": { "status": "sent|queued|retrying|failed|idle|...", "sent_count": 2, "total_recipients": 2 },
      "incident": { "opened_at": 1783531060.1, "duration_seconds": 812, ... } }
  ],
  "recent_events": [ ... ],   // transições confirmadas recentes (state)
  "recent_alerts": [ ... ],   // entregas WhatsApp recentes (alert_store)
  "incidents": [ ... ]        // incidentes abertos primeiro, depois resolvidos
}
```

`POST /api/alerts/test` dispara um teste de ponta a ponta (mesma fila/template/retry, mas a
mensagem diz claramente que **nenhum ramal caiu**). Protegido por: header
`X-PulsoPBX-Action: test-alert`, `Content-Type: application/json`, corpo `{"confirm": true}`,
checagem de Origin, e cooldown (`ALERT_TEST_COOLDOWN_SECONDS`, mín. 10s).

---

## 5. Configuração (`.env`)

Copie `.env.example` para `.env`. **Tudo é opcional** — o painel sobe mesmo sem nada
configurado (útil para ver a UI antes das integrações). Principais variáveis:

| Grupo | Variáveis |
|---|---|
| AMI | `AMI_HOST` (192.168.1.254), `AMI_PORT` (5038), `AMI_USER`, `AMI_SECRET` |
| Painel | `DASHBOARD_HOST` (0.0.0.0), `DASHBOARD_PORT` (8080) |
| WhatsApp | `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`, `WHATSAPP_GRAPH_API_VERSION` (v25.0), `WHATSAPP_TEMPLATE` (ramal_alerta), `WHATSAPP_USE_TEMPLATE` (true), `WHATSAPP_RECIPIENTS` (com DDI, separados por vírgula) |
| Detecção | `DEBOUNCE_SECONDS` (30), `RECONCILE_SECONDS` (60) |
| Alertas | `ALERT_MAX_ATTEMPTS` (3), `ALERT_RETRY_BASE_SECONDS` (15), `ALERT_TEST_COOLDOWN_SECONDS` (60) |
| Persistência | `INCIDENTS_DB_PATH` (data/pulsopbx.db) |
| MikoPBX API | `MIKOPBX_API_KEY`, `MIKOPBX_API_URL`, `MIKOPBX_VERIFY_TLS` (false), `MIKOPBX_NAMES_REFRESH_SECONDS` (300) |
| Demo | `DEMO_MODE` (false) |

O `.env` **não** é versionado (`.gitignore`). Contém segredos reais (chave AMI, chave API,
token WhatsApp).

---

## 6. Integrações externas

### 6.1 AMI (Asterisk Manager Interface)
- Usuário dedicado `ramais_monitor` no MikoPBX (**Sistema → Interface AMI**). Permissões:
  `call` + `reporting` (lendo e gravação) + `system` **só leitura**. Nunca `system`
  gravação (equivale a shell no servidor).
- Filtro de rede: só existem "qualquer endereço" ou "somente local"; ficou em **qualquer
  endereço** (a senha forte é a proteção; "somente local" bloquearia a máquina do monitor).

### 6.2 REST API v3 do MikoPBX (nomes dos ramais)
- **Sistema → Chaves API** → nova chave com **Leitura** em *Employees Management*
  (`/api/v3/employees`). Resto "Sem acesso". Chave de 64 hex vai em `MIKOPBX_API_KEY`.
- O MikoPBX **não tem campo de "setor"** — quando existe, já vem embutido no nome
  (ex.: `Engenharia - Edson`). Para separar setor ou trocar um nome, use `ramais_nomes.json`.
- Certificado interno autoassinado → `MIKOPBX_VERIFY_TLS=false`.

### 6.3 WhatsApp Cloud API (Meta) — **pendente de conclusão**
- App "Business" no Meta for Developers → produto WhatsApp → token permanente (System User,
  permissão `whatsapp_business_messaging`) → `Phone Number ID` → template `ramal_alerta`
  (3 parâmetros: ramal / status / horário) aprovado.
- Enquanto o template não é aprovado: `WHATSAPP_USE_TEMPLATE=false` (texto livre, só funciona
  dentro da janela de 24h após o destinatário escrever para o número).
- **Passo a passo detalhado em `SETUP.md`, seções 2 e 3.**

---

## 7. Persistência (SQLite)

Arquivo `data/pulsopbx.db` (WAL, na cópia local; **não versionado**). Sobrevive a reinícios.

- **`incidents`** — uma linha por queda: `extension`, `status` (open/resolved), `opened_at`,
  `resolved_at`, `duration_seconds`.
- **`alert_events`** + **`alert_deliveries`** — cada alerta e o status de entrega por
  destinatário. Ao reiniciar, entregas ainda pendentes voltam para a fila (quem já recebeu
  **não** recebe de novo).

---

## 8. Como rodar / desenvolver / deploy

### Rodar local (desenvolvimento)
```
cd C:\Users\eduardo.p\Desktop\Ramais\ramais_monitor
.venv\Scripts\python.exe main.py                                    # painel em http://localhost:8080/
.venv\Scripts\python.exe -m unittest test_state test_incidents test_alerts test_notifier test_web
```
`DEMO_MODE=true` mostra ramais de exemplo sem tocar na AMI real.

### Onde roda em produção (24/7)
- Roda de uma **cópia local** em `C:\Users\eduardo.p\ramais_monitor` na máquina
  **DKS-FG-006** (IP na LAN: `172.20.171.206`) — **não** direto do repositório/rede, para
  que um reboot do servidor de arquivos não derrube o monitor.
- Tarefa agendada **`RamaisMonitor`** (hoje: gatilho no logon, criada por `install_task.ps1`,
  sem admin). Upgrade recomendado para serviço **SYSTEM** (roda sem login + libera a porta no
  firewall): rodar `install_system_task.ps1` **como administrador** (ainda não feito).
- Painel: `http://localhost:8080/` na máquina; `http://172.20.171.206:8080/` na LAN depois do
  upgrade SYSTEM (que abre a porta no firewall).

### Fluxo para publicar uma alteração
1. Editar o código no repositório (`Desktop\Ramais\ramais_monitor`), commitar/push.
2. Na DKS-FG-006, rodar `deploy_local.ps1` (para o serviço, copia o código — **exceto**
   `.env`/`.venv`/`logs`/`data` — e reinicia).

> Detalhes completos e comandos úteis: **`SETUP.md`, seção 4**.

---

## 9. Pegadinhas / conhecimento não-óbvio (LEIA)

1. **AMI: use `ExtensionStateList`, não `PJSIPShowEndpoints`.** Neste MikoPBX,
   `PJSIPShowEndpoints`/`PJSIPShowContacts` retornam **"Permission denied"** mesmo com
   call+reporting+system-read. `ExtensionStateList` entrega o mesmo sinal ("o ramal está
   registrado?") com as permissões que temos. `StatusText` = `Unavailable`/`Unknown` → offline;
   qualquer outro (Idle/InUse/...) → online.
2. **`panoramisk` 1.4:** `send_action(...)` retorna um `asyncio.Future`, **não** um iterador
   async. Use `await send_action({...}, as_list=True)` para pegar a lista completa. E se uma
   ação de lista recebe uma resposta de erro única, esse Future **nunca resolve (trava)** —
   por isso a reconciliação é embrulhada em `asyncio.wait_for(...)`.
3. **O filtro de ramais reais vem da API de funcionários.** `ExtensionStateList` também
   devolve filas, salas de conferência e apps de teste (2200100, 1111, 000063...). Em
   `on_snapshot` só rastreamos ramais que existem em `mikopbx_api.get_cached_names()`. Por
   isso a lista de funcionários é carregada **antes** de conectar a AMI, e o filtro só age
   quando o cache já tem dados (se o cache estiver vazio, rastreia tudo — degradação segura).
4. **Debounce de 30s** evita alarme falso (MicroSIP reiniciando, blip de rede). Uma queda só
   vira "offline confirmado" — e só então gera incidente/alerta — depois desse tempo. O painel
   mostra isso como "Em confirmação".
5. **`pythonw.exe` do venv abre 2 processos** (lançador + worker). É normal, não é instância
   duplicada. Só um escuta na 8080.
6. **Conta `SYSTEM` não acessa compartilhamento de rede.** Por isso rodamos de cópia local, e
   não do `\\10.5.0.5\...`.
7. **Idempotência dos alertas:** um evento por transição confirmada; repetição do mesmo estado
   é deduplicada; ao reiniciar, só pendências voltam para a fila.

---

## 10. Status atual e o que falta

| Parte | Status |
|---|---|
| AMI — status dos ramais em tempo real | ✅ funcionando (36 ramais reais) |
| Nomes automáticos via API MikoPBX | ✅ funcionando |
| Painel web (PulsoPBX) | ✅ funcionando |
| Histórico de incidentes + entregas (SQLite) | ✅ funcionando |
| Fila de alertas com retry + botão de teste | ✅ implementado |
| Deploy 24/7 (tarefa no logon) | ✅ configurado |
| **Alerta WhatsApp (conta Meta)** | ⏳ **pendente** — conta travou na verificação de telefone (Accounts Center) |
| Teste end-to-end (derrubar ramal → alerta chega) | ⏳ depende do WhatsApp |
| Upgrade para serviço SYSTEM (reboot sem login + firewall LAN) | ⏳ 1 comando como admin (`install_system_task.ps1`) |

**Próximo passo em andamento (decidido com o dono do projeto):** melhorias de **UI/UX** no
painel — foco em (a) estética/acabamento geral, (b) responsividade/mobile, (c) interações e
feedback (animações de transição, alerta visual/sonoro quando um ramal cai, notificações do
navegador). A proposta visual é livre ("do zero").

---

## 11. Contato / origem

Projeto criado e mantido pela equipe de **TI/Infra da Joinville Implementos**
(infra@joinvilleimplementos.com.br). Nome do produto: **PulsoPBX** (analogia com monitor de
pulso — cada ramal tem um "pulso"; o sistema detecta quando ele para).
