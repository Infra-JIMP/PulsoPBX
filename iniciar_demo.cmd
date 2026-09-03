@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
title PulsoPBX - Modo demonstracao
pushd "%~dp0" >nul 2>&1
if errorlevel 1 (
  echo [ERRO] Nao foi possivel acessar a pasta do projeto.
  exit /b 1
)

set "ROOT=%CD%"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "URL=http://127.0.0.1:18080/"

if not exist "%PYTHON%" (
  echo.
  echo [ERRO] Ambiente Python nao encontrado em:
  echo %PYTHON%
  echo.
  echo Crie o ambiente com: python -m venv .venv
  echo Depois instale: .venv\Scripts\python.exe -m pip install -r requirements.lock.txt
  echo.
  pause
  set "EXIT_CODE=1"
  goto :finish
)

rem Estas variaveis valem somente para esta janela e isolam a demonstracao.
set "DEMO_MODE=true"
set "DASHBOARD_HOST=127.0.0.1"
set "DASHBOARD_PORT=18080"
set "DASHBOARD_USERNAME="
set "DASHBOARD_PASSWORD="
set "AMI_USER="
set "AMI_SECRET="
set "MIKOPBX_API_KEY="
set "EMAIL_SMTP_HOST="
set "EMAIL_SMTP_USERNAME="
set "EMAIL_SMTP_PASSWORD="
set "EMAIL_FROM="
set "EMAIL_RECIPIENTS="
set "EMAIL_SMTP_STARTTLS=false"
set "EMAIL_SMTP_SSL=false"
set "INCIDENTS_DB_PATH=%TEMP%\pulsopbx-demo.db"

if /I "%~1"=="--check" (
  "%PYTHON%" -c "from config import load_config; c=load_config(); assert c.demo_mode and c.dashboard_host == '127.0.0.1' and c.dashboard_port == 18080; import main, web; print('Atalho de demonstracao: OK')"
  set "EXIT_CODE=!ERRORLEVEL!"
  goto :finish
)

rem Se a demonstracao ja estiver aberta, apenas mostra o painel existente.
powershell.exe -NoProfile -Command "try { $health=Invoke-RestMethod -Uri '%URL%api/health' -TimeoutSec 2; if($health.ready){exit 0} } catch {}; exit 1" >nul 2>&1
if not errorlevel 1 (
  echo PulsoPBX ja esta rodando em %URL%
  start "" "%URL%"
  set "EXIT_CODE=0"
  goto :finish
)

echo.
echo ========================================
echo   PulsoPBX - Modo demonstracao
echo ========================================
echo.
echo Painel: %URL%
echo O navegador abrira automaticamente.
echo Para encerrar, pressione Ctrl+C.
echo.

rem Aguarda o endpoint responder antes de abrir o navegador.
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "$url='%URL%'; for($i=0; $i -lt 30; $i++){try{$health=Invoke-RestMethod -Uri ($url+'api/health') -TimeoutSec 1; if($health.ready){Start-Process $url; exit 0}}catch{}; Start-Sleep -Milliseconds 500}; exit 1"

"%PYTHON%" "%ROOT%\main.py"
set "EXIT_CODE=!ERRORLEVEL!"

if not "!EXIT_CODE!"=="0" (
  echo.
  echo O PulsoPBX encerrou com codigo !EXIT_CODE!.
  pause
)

:finish
popd
exit /b !EXIT_CODE!
