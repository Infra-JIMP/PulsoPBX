# === RODAR COMO ADMINISTRADOR ===
# Instala o monitor como SYSTEM, no boot, e libera somente a porta configurada
# para enderecos da sub-rede local em qualquer perfil de rede ativo.
$ErrorActionPreference = "Stop"
$dir = "C:\Users\eduardo.p\ramais_monitor"
$pythonw = "$dir\.venv\Scripts\pythonw.exe"
$envFile = Join-Path $dir ".env"
$dashboardPort = 8080

if (Test-Path -LiteralPath $envFile) {
    $portLine = Get-Content -LiteralPath $envFile |
        Where-Object { $_ -match '^\s*DASHBOARD_PORT\s*=' } |
        Select-Object -Last 1
    if ($portLine) {
        $candidate = [int](($portLine -split "=", 2)[1].Trim())
        if ($candidate -lt 1 -or $candidate -gt 65535) {
            throw "DASHBOARD_PORT invalida no .env: $candidate"
        }
        $dashboardPort = $candidate
    }
}

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "main.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtStartup
$settingsParameters = @{
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
    RestartCount = 999
    RestartInterval = (New-TimeSpan -Minutes 1)
    ExecutionTimeLimit = [TimeSpan]::Zero
    StartWhenAvailable = $true
    MultipleInstances = "IgnoreNew"
}
$settings = New-ScheduledTaskSettingsSet @settingsParameters
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$taskParameters = @{
    TaskName = "RamaisMonitor"
    Action = $action
    Trigger = $trigger
    Settings = $settings
    Principal = $principal
    Description = "PulsoPBX - monitor de ramais e alertas por e-mail"
    Force = $true
}

# Garante que nenhuma instancia interativa sobreviva a troca de principal.
Stop-ScheduledTask -TaskName "RamaisMonitor" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*ramais_monitor*main.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Register-ScheduledTask @taskParameters

Get-NetFirewallRule -DisplayName "Ramais Monitor *" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
$firewallParameters = @{
    DisplayName = "Ramais Monitor $dashboardPort"
    Direction = "Inbound"
    LocalPort = $dashboardPort
    Protocol = "TCP"
    Action = "Allow"
    Profile = @("Domain", "Private", "Public")
    RemoteAddress = "LocalSubnet"
}
New-NetFirewallRule @firewallParameters | Out-Null

Start-ScheduledTask -TaskName "RamaisMonitor"
Write-Output "Servico SYSTEM instalado e iniciado."
Write-Output "Firewall: porta $dashboardPort, somente LocalSubnet em qualquer perfil ativo."
