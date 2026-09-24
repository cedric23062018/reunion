@echo off
title Serveur Planificateur de Reunion
cd /d "%~dp0"
echo =======================================================
echo     Lancement du serveur Planificateur de Reunion
echo =======================================================
echo.
echo 1. Démarrage du serveur web local...
start /B uv run --with fastapi --with uvicorn python server.py
timeout /t 3 /nobreak > nul

echo 2. Ouverture de votre navigateur local...
start http://127.0.0.1:8000

echo.
echo 3. Generation du lien PUBLIC Internet (Serveo)...
echo Ne fermez pas cette fenetre pour que le lien reste actif.
echo.
ssh -o StrictHostKeyChecking=no -R 80:127.0.0.1:8000 serveo.net
pause
