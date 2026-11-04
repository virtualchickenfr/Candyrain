@echo off
chcp 65001 >nul
title Karaoke Studio
cd /d "%~dp0"

if not exist "tools\installed.txt" (
  echo.
  echo  Erster Start: Karaoke Studio wird eingerichtet.
  echo  Das dauert 10 bis 20 Minuten. Bitte dieses Fenster offen lassen.
  echo.
  powershell -NoProfile -ExecutionPolicy Bypass -File "desktop\install.ps1"
  if errorlevel 1 (
    echo.
    echo  Die Einrichtung hat nicht geklappt.
    echo  Mach bitte ein Foto von diesem Fenster und schick es an Claude.
    echo.
    pause
    exit /b 1
  )
)

set "PATH=%~dp0tools\ffmpeg;%~dp0tools\deno;%~dp0tools\python;%~dp0tools\python\Scripts;%PATH%"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

"tools\python\python.exe" "desktop\server.py"

echo.
echo  Karaoke Studio wurde beendet.
pause
