@echo off
chcp 65001 >nul
title Radar de Licitacoes MEI - Servidor Web
echo Iniciando o site do Radar de Licitacoes...
echo O navegador vai abrir automaticamente em http://localhost:8080
echo (Mantenha esta janela aberta enquanto estiver usando o site)
echo.
py "%~dp0servidor_web.py"
pause
