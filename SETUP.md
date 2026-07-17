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

## 2. Configurar alertas por E-mail

Preencha `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT` e `EMAIL_FROM`. Se o servidor exigir autenticação, configure também `EMAIL_SMTP_USERNAME` e `EMAIL_SMTP_PASSWORD`. Use `EMAIL_SMTP_STARTTLS=true` para a porta 587 ou `EMAIL_SMTP_SSL=true` para SSL direto, normalmente na porta 465; não ative os dois ao mesmo tempo.

`EMAIL_RECIPIENTS` é opcional e serve apenas para o botão **Enviar teste** do painel. Os avisos reais usam o e-mail individual vinculado ao ramal. O MikoPBX continua sendo a fonte automática, mas um cadastro local pode assumir prioridade quando a central estiver indisponível. O endereço nunca é devolvido pelas APIs normais do painel; somente a informação “configurado/não configurado” é exibida.

Para habilitar a tela interna **Responsáveis**, configure `RESPONSIBLES_ADMIN_PASSWORD` com pelo menos 12 caracteres ou salve a senha isoladamente em `data/responsibles_admin_password.txt`. A tela aparece somente em acesso direto pela rede interna; requisições encaminhadas pelo Cloudflare são recusadas. A senha fica apenas na memória da aba do navegador. O cadastro é salvo em `ramais_nomes.json`, entra em vigor sem reiniciar o serviço e pode ser removido pelo botão **Voltar ao MikoPBX**.

O fluxo é: 30 segundos para confirmar a mudança de estado, mais 2 minutos de tolerância (`RESPONSIBLE_ALERT_DELAY_SECONDS=120`). Se o ramal reconectar nesse período, o e-mail é cancelado. Se continuar offline, estiver dentro do expediente e não fizer parte de uma queda coletiva, um único aviso amigável pede para verificar MicroSIP, internet e registro do ramal. O retorno só é avisado se o e-mail de queda realmente tiver sido entregue.

O monitor coloca cada entrega em uma fila separada. Se o SMTP ou a rede falhar, ele tenta novamente sem interromper a AMI. Por padrão são 3 tentativas, com espera de 15s e 30s; ajuste `ALERT_MAX_ATTEMPTS` e `ALERT_RETRY_BASE_SECONDS` se necessário. Eventos, tentativas e jobs pendentes ficam no SQLite local e sobrevivem a reinícios. Cinco quedas na mesma janela de 60 segundos são tratadas como indisponibilidade coletiva (`MASS_OUTAGE_THRESHOLD` / `MASS_OUTAGE_WINDOW_SECONDS`) e não geram uma sequência de e-mails individuais.

### 2.1. Configurar expediente, feriados e folgas

Copie `work_calendar.example.json` para `work_calendar.json` e substitua os horários de exemplo pelos horários oficiais. O arquivo não é versionado e é recarregado automaticamente, sem reiniciar o serviço.

Cada dia da semana pode ter um ou mais intervalos, permitindo representar almoço. Em `exceptions`, use a data no formato `AAAA-MM-DD`:

```json
"2026-12-25": {
  "label": "Natal",
  "intervals": []
},
"2026-12-19": {
  "label": "Expediente excepcional",
  "intervals": [["08:00", "12:00"]]
}
```

Uma lista vazia marca feriado, folga ou dia atípico sem expediente; uma lista preenchida substitui o horário normal somente naquela data. Se o arquivo estiver ausente ou inválido, o histórico continua sendo coletado, mas os e-mails individuais ficam suspensos por segurança.

## 3. Rodar o monitor manualmente (antes de virar tarefa agendada)

Para o uso cotidiano, há dois atalhos na raiz do projeto:

- Dê duplo clique em `iniciar_demo.cmd` para iniciar uma demonstração isolada na porta `18080`; o navegador abre automaticamente e `Ctrl+C` encerra o processo.
- Dê duplo clique em `abrir_painel.cmd` para abrir o painel oficial em `http://172.20.171.206:8080/`, sem iniciar outro monitor.

Os mesmos atalhos também podem ser executados pelo terminal:

```powershell
.\iniciar_demo.cmd
.\abrir_painel.cmd
```

Para validar os atalhos sem iniciar serviço nem abrir o navegador, acrescente `--check`.

O comando manual completo continua disponível:

```
cd C:\Users\eduardo.p\Desktop\Ramais\ramais_monitor
.venv\Scripts\python.exe main.py
```

Acompanhe `logs/monitor.log`. Pressione Ctrl+C para parar.

AMI e E-mail são opcionais: sem credenciais o painel ainda sobe e informa o que falta configurar.

Valores inválidos no `.env` são informados nominalmente em `logs/monitor.log` e impedem a inicialização. Intervalos de reconciliação/atualização precisam ser positivos e portas devem estar entre 1 e 65535.

## 3.1. Painel web de status

Com o `main.py` rodando, abra no navegador (de qualquer PC da rede interna):

```
http://172.20.171.206:8080/
```

(esse é o IP da `DKS-FG-006` na rede interna; `DASHBOARD_PORT` no `.env` muda a porta se precisar). O painel (estilo NOC, tabela densa) atualiza sozinho a cada 3s e mostra:
- Contadores no topo: total de ramais, online e offline (o bloco "Offline" fica vermelho quando há algum fora).
- Um quarto contador, **Em confirmação**, mostra leituras ainda dentro do debounce. Uma queda só vira "Offline confirmado" e pode gerar alerta depois desse tempo, evitando falsos positivos.
- Selos de saúde da AMI e da entrega de alertas por E-mail.
- Faixa de prioridade operacional, tabela com os offline no topo (linha destacada em vermelho), nome, setor, status de entrega do alerta e há quanto tempo está no estado atual.
- Campo de busca (por número, nome ou setor) e filtros Todos / Atenção / Offline / Online.
- Histórico persistente de incidentes: queda confirmada, retorno, duração e situação atual. Ele usa SQLite local (`data/pulsopbx.db`), que não é versionado.

Quando o painel estiver acessível pela rede, configure `DASHBOARD_USERNAME` e `DASHBOARD_PASSWORD`. O acesso usa HTTP Basic; portanto, mantenha-o restrito à rede interna/VPN. Para exposição fora dessa rede, use um proxy HTTPS e não abra a porta diretamente para a Internet. O endpoint `/api/health` não exige autenticação e retorna apenas prontidão e situação da AMI, sem nomes ou ramais.

### 3.2. Nomes dos ramais (automático via API do MikoPBX)

O painel busca o nome do funcionário de cada ramal direto do MikoPBX, via API REST:

1. No painel do MikoPBX: **Sistema → Chaves API → criar nova chave**.
   - Descrição: `ramais` (ou o que preferir).
   - Permissões: **Leitura** em **Employees Management** (`/api/v3/employees`) — o resto fica "Sem acesso".
   - Filtro de rede: restrinja se o painel oferecer essa opção; se só houver "qualquer endereço" ou "somente locais", use "qualquer endereço" mesmo (a chave de 64 caracteres já protege o acesso, e o escopo é só leitura de 2 dados de baixa sensibilidade).
   - Salve e copie a chave gerada (ela some depois, então copie na hora).
2. Preencha no `.env`: `MIKOPBX_API_KEY` (a chave copiada). `MIKOPBX_API_URL` já vem certo por padrão.
3. Pronto — nome e e-mail responsável de cada ramal passam a ser atualizados a cada 5 minutos (`MIKOPBX_NAMES_REFRESH_SECONDS`). O painel não mostra o endereço; apenas a cobertura de cadastro.

Por padrão, a consulta aceita o certificado interno/autoassinado comum no MikoPBX (`MIKOPBX_VERIFY_TLS=false`). Só altere para `true` depois que o PBX apresentar um certificado válido e confiável para a máquina do monitor.

O MikoPBX não tem campo de "setor" separado (o setor, quando existe, já vem dentro do próprio nome, ex.: `Engenharia - Edson`). Se quiser sobrescrever algum nome específico ou adicionar um setor à parte, use o `ramais_nomes.json` manual (opcional, só para exceções). Os e-mails podem ser administrados pela tela **Responsáveis**, sem editar este arquivo:
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

### 3.4. Disponibilidade operacional e histórico

A seção **Disponibilidade operacional** oferece períodos de 7, 30 e 90 dias e três recortes: geral, setores e ramais. Ela registra e calcula:

- primeira conexão e última desconexão observadas em cada dia;
- horários habituais por dia da semana;
- disponibilidade somente dentro do expediente;
- quantidade, média, mediana, maior queda e tempo até reconexão;
- visão geral, por setor e individual;
- finais de semana e exceções do calendário.

O histórico cronológico fica em `data/pulsopbx.db`, junto dos incidentes e entregas, e começa a ser alimentado assim que a versão entra em operação. Incidentes antigos já existentes são aproveitados no primeiro início. Até acumular pelo menos 20 dias úteis com cobertura suficiente, o painel identifica os números como **base em formação**. Essas métricas representam disponibilidade técnica do ramal, não produtividade do colaborador. Exportações CSV/PDF permanecem como evolução futura.

## 4. Deploy 24/7 (JA CONFIGURADO)

Para robustez, o servico roda de uma **copia de producao** em `C:\Users\eduardo.p\ramais_monitor` na `DKS-FG-006`. O repositorio principal de **desenvolvimento e Git** fica em `C:\Users\eduardo.p\Desktop\Ramais\ramais_monitor`; o compartilhamento `\\10.5.0.5\Alma\TI\Ramais\ramais_monitor` e apenas uma copia secundaria e nao deve ser usado para commits.

### Como esta rodando hoje
- Tarefa agendada **`RamaisMonitor`** executada como **SYSTEM**, iniciada no boot e configurada para reiniciar sozinha se cair.
- Painel: `http://172.20.171.206:8080/`, liberado no firewall somente para a sub-rede local.

Para reinstalar a tarefa e a regra de firewall, rode como administrador:
```
powershell -ExecutionPolicy Bypass -File C:\Users\eduardo.p\ramais_monitor\install_system_task.ps1
```
Isso registra novamente a tarefa como **SYSTEM** e abre no firewall a porta definida em `DASHBOARD_PORT`, restrita à sub-rede local em qualquer perfil de rede ativo.

### Scripts de gerenciamento
- `install_system_task.ps1` - upgrade para servico SYSTEM + firewall (rodar como admin).
- `uninstall_task.ps1` - remove a tarefa e para o servico.
- `deploy_local.ps1` (na raiz do projeto local) - valida, cria staging e backup, sincroniza a cópia de produção e reinicia o serviço. Não copia `.env`, `.venv`, `logs`, `data`, `ramais_nomes.json`, `work_calendar.json` ou artefatos locais; se a validação pós-cópia falhar, restaura automaticamente o backup.

Para validar sem copiar nem reiniciar:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy_local.ps1 -ValidateOnly
```

Para reproduzir exatamente o ambiente validado ao criar uma nova `.venv`, use `python -m pip install -r requirements.lock.txt`. O `requirements.txt` declara somente as dependências diretas.

### Comandos uteis
- Ver estado: `Get-ScheduledTask -TaskName RamaisMonitor`
- Parar/iniciar: `Stop-ScheduledTask -TaskName RamaisMonitor` / `Start-ScheduledTask -TaskName RamaisMonitor`
- Logs: `C:\Users\eduardo.p\ramais_monitor\logs\monitor.log`
