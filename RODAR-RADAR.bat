@echo off
chcp 65001 >nul
title Radar de Licitacoes
echo Rodando o Radar de Licitacoes (regiao completa). Pode levar alguns minutos...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0radar.ps1"
echo.
pause
