@echo off
title ALFACREDIT S.A. - Plataforma de Novedades y Combustible
color 1F
echo ================================================================
echo    ALFACREDIT S.A. - SISTEMA DE NOVEDADES Y COMBUSTIBLE
echo ================================================================
echo Iniciando servidor web...
cd /d "c:\sistema\Novedades"
start "" http://localhost:8000
python server.py
pause
