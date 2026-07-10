# Sincroniza a versao de desenvolvimento (esta pasta versionada) para a
# copia que roda de verdade em C:\Users\eduardo.p\ramais_monitor na DKS-FG-006.
# Use isto sempre que editar o codigo e quiser que o servico rode a versao nova.
# NAO copia .venv, logs, cache nem o .env (esses ficam so na copia local).
$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$dst = "C:\Users\eduardo.p\ramais_monitor"

Write-Output "Parando o servico..."
Stop-ScheduledTask -TaskName "RamaisMonitor" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*ramais_monitor*main.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 2

Write-Output "Copiando codigo atualizado..."
robocopy $src $dst /E /XD .git .venv __pycache__ logs .claude output data /XF "*.pyc" ".env" /NFL /NDL /NJH /NJS /NC /NS | Out-Null

Write-Output "Reiniciando o servico..."
Start-ScheduledTask -TaskName "RamaisMonitor"
Write-Output "Pronto. Painel: http://localhost:8080/"
