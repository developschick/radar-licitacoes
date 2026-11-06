@echo off
chcp 65001 >nul
title Radar de Licitacoes - modo rapido
echo Rodando o Radar em modo RAPIDO (so Sao Jose dos Pinhais)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0radar.ps1" -Rapido
echo.
pause
