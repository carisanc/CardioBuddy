@echo off
REM Muestra en crudo lo que llega al broker.
"C:\Program Files\mosquitto\mosquitto_sub.exe" -h localhost -t "cardiobuddy/#" -v
pause
