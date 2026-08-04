# Operacao

## Estado atual seguro

O servico deve permanecer parado enquanto a manutencao estiver em validacao.
Confirme com `Get-ScheduledTask -TaskName RamaisMonitor`.

Para validar codigo sem iniciar nem copiar o servico:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy_local.ps1 -ValidateOnly
```

Para iniciar ou parar depois de uma aprovacao operacional explicita:

```powershell
Start-ScheduledTask -TaskName RamaisMonitor
Stop-ScheduledTask -TaskName RamaisMonitor
```

O painel e acessivel somente na rede interna em `http://IP-DA-MAQUINA:8080/`.
Mantenha usuario e senha HTTP Basic configurados para qualquer host diferente
de `127.0.0.1`.

Logs: `logs/monitor.log`. Dados locais: `data/pulsopbx.db`.
