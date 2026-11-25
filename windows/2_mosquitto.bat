@echo off
REM Arranca el broker MQTT con la configuracion del proyecto. Deja esta ventana abierta.
cd /d "%~dp0.."
set MOSQ="C:\Program Files\mosquitto\mosquitto.exe"
if not exist %MOSQ% (
  echo No se encontro Mosquitto en C:\Program Files\mosquitto
  echo Instalalo desde https://mosquitto.org/download/
  pause
  exit /b 1
)
%MOSQ% -c mosquitto\mosquitto.conf -v
pause
