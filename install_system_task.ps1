# === RODAR COMO ADMINISTRADOR ===
# Faz o "upgrade" do Monitor de Ramais para um servico de verdade:
#  - roda como SYSTEM, iniciando junto com o Windows (mesmo sem ninguem logado)
#  - reinicia sozinho se cair
#  - abre a porta 8080 no firewall para outros PCs da rede acessarem o painel
# Substitui a tarefa de logon criada por install_task.ps1 (mesmo nome).
$ErrorActionPreference = "Stop"
$dir = "C:\Users\eduardo.p\ramais_monitor"
$pythonw = "$dir\.venv\Scripts\pythonw.exe"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "main.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest

Register-ScheduledTask -TaskName "RamaisMonitor" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Monitor de Ramais (MikoPBX -> painel/WhatsApp) - servico SYSTEM" -Force

# Libera a porta 8080 no firewall (acesso ao painel a partir de outros PCs da rede)
if (-not (Get-NetFirewallRule -DisplayName "Ramais Monitor 8080" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Ramais Monitor 8080" -Direction Inbound `
        -LocalPort 8080 -Protocol TCP -Action Allow -Profile Any | Out-Null
    Write-Output "Regra de firewall para a porta 8080 criada."
}

Start-ScheduledTask -TaskName "RamaisMonitor"
Write-Output "Servico SYSTEM 'RamaisMonitor' instalado e iniciado."
Write-Output "Painel: http://172.20.171.206:8080/  (ou http://localhost:8080/ na propria maquina)"
