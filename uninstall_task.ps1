# Remove a tarefa, encerra somente o processo do monitor e limpa suas regras de firewall.
$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "RamaisMonitor"
Unregister-ScheduledTask -TaskName "RamaisMonitor" -Confirm:$false
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*ramais_monitor*main.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Get-NetFirewallRule -DisplayName "Ramais Monitor *" -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
Write-Output "Tarefa, processo do monitor e regras de firewall removidos."
