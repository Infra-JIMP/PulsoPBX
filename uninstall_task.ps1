# Remove a tarefa agendada do Monitor de Ramais e para o servico.
# (a regra de firewall, se criada pelo install_system_task, precisa de admin para remover)
$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "RamaisMonitor"
Unregister-ScheduledTask -TaskName "RamaisMonitor" -Confirm:$false
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*ramais_monitor*main.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Write-Output "Tarefa 'RamaisMonitor' removida e processos encerrados."
