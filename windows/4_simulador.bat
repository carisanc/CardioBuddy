@echo off
REM Simula el ESP32 por MQTT (sin hardware). Cierra esta ventana antes de conectar el ESP32 real.
cd /d "%~dp0.."
python -m pip install -q paho-mqtt
python herramientas\simulador.py --mqtt localhost --pico-cada 30
pause
