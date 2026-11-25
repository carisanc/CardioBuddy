@echo off
REM Instala dependencias (la primera vez) y levanta el servidor conectado a Mosquitto.
cd /d "%~dp0..\backend"
python -m pip install -q -r requirements.txt
set CB_MQTT_HOST=localhost
echo.
echo Panel en http://localhost:8000
echo Para el ESP32, usa como MQTT_HOST esta IP:
ipconfig | findstr /i "IPv4"
echo.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
pause
