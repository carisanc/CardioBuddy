@echo off
REM Ejecutar UNA sola vez: clic derecho > "Ejecutar como administrador"
net session >nul 2>&1
if errorlevel 1 (
  echo Este script necesita permisos de administrador.
  echo Clic derecho sobre el archivo y elige "Ejecutar como administrador".
  pause
  exit /b 1
)
net stop mosquitto >nul 2>&1
sc config mosquitto start= demand >nul 2>&1
netsh advfirewall firewall add rule name="Mosquitto 1883" dir=in action=allow protocol=TCP localport=1883
netsh advfirewall firewall add rule name="CardioBuddy 8000" dir=in action=allow protocol=TCP localport=8000
echo.
echo Listo: puertos 1883 y 8000 abiertos y servicio de Mosquitto detenido.
pause
