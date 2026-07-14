@echo off
setlocal EnableExtensions

set "URL=http://172.20.171.206:8080/"

if /I "%~1"=="--check" (
  echo Atalho do painel oficial: %URL%
  exit /b 0
)

start "" "%URL%"
exit /b 0
