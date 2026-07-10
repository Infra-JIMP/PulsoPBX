# Registra a tarefa agendada do Monitor de Ramais para o usuario atual.
# Roda no logon e reinicia sozinha se cair. NAO precisa de admin.
$ErrorActionPreference = "Stop"
$dir = "C:\Users\eduardo.p\ramais_monitor"
$pythonw = "$dir\.venv\Scripts\pythonw.exe"

$action = New-ScheduledTaskAction -Execute $pythonw -Argument "main.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable

Register-ScheduledTask -TaskName "RamaisMonitor" -Action $action -Trigger $trigger `
    -Settings $settings -Description "Monitor de Ramais (MikoPBX -> painel/WhatsApp)" -Force

Write-Output "Tarefa 'RamaisMonitor' registrada. Iniciando agora..."
Start-ScheduledTask -TaskName "RamaisMonitor"
Write-Output "Pronto. Painel em http://localhost:8080/"
