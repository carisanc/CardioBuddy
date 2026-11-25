# CardioBuddy – JIMCe Robotics

Compañero IoT de monitoreo cardíaco pediátrico. Esta carpeta contiene la aplicación descrita en el informe técnico (Fig. 1): firmware del ESP32, servidor y panel web.

```
cardiobuddy/
├── backend/app/          Servidor FastAPI: perfiles, telemetría, alertas, WebSocket, MQTT
├── frontend/             Página web (bienvenida → perfil → monitoreo en vivo)
├── firmware/             Sketch del ESP32 (MAX30102 + OLED + zumbador + MQTT)
├── herramientas/         Simulador del ESP32 para pruebas y demos
├── mosquitto/            Configuración del broker MQTT
├── windows/              Scripts .bat para levantar todo con doble clic
└── docker-compose.yml    Levanta broker + servidor con un comando
```

## Cómo fluyen los datos

```
MAX30102 → ESP32 → (Wi-Fi, MQTT) → Mosquitto → servidor FastAPI → (WebSocket) → panel web
                └→ OLED + zumbador (funcionan sin red)        └→ SQLite + aviso por correo
```

El servidor también acepta lecturas por HTTP (`POST /api/telemetria`), útil para probar sin broker.

## Levantar en Windows con Mosquitto (paso a paso)

Requisitos: Python 3.10 o más reciente (marca "Add Python to PATH" al instalarlo) y Mosquitto para Windows 64 bits (<https://mosquitto.org/download/>, instalación por defecto).

La carpeta `windows/` tiene un script por paso. Se abren con doble clic, en orden, y cada uno queda en su propia ventana:

| Script | Qué hace | Cuándo |
|---|---|---|
| `1_abrir_firewall.bat` | Abre los puertos 1883 y 8000 y detiene el servicio de Mosquitto que trae el instalador (solo acepta conexiones locales) | **Una sola vez**, con clic derecho > *Ejecutar como administrador* |
| `2_mosquitto.bat` | Arranca el broker con `mosquitto/mosquitto.conf`. Debe decir `Opening ipv4 listen socket on port 1883` | Siempre, y se deja abierta |
| `3_servidor.bat` | Instala dependencias, levanta el servidor y muestra la IP de la PC | Siempre, y se deja abierta. Debe decir `MQTT conectado (Success)` |
| `4_simulador.bat` | Simula el ESP32 por MQTT, con un pico cada 30 s | Solo para probar sin hardware |
| `5_ver_trafico_mqtt.bat` | Muestra los mensajes crudos que llegan al broker | Para depurar |

Después:

1. Abre <http://localhost:8000>, llena el perfil con el código de dispositivo `cb-01` y entra al monitoreo.
2. Prueba con `4_simulador.bat`: el panel debe decir "Datos en vivo" y mostrar una alerta en cada pico. **Ciérralo antes de conectar el ESP32**, porque los dos publican como `cb-01`.
3. Configura el firmware (ver abajo) con la IP que muestra `3_servidor.bat` y cárgalo al ESP32.

### Los mismos pasos a mano

Los comandos de PowerShell y los de CMD (Símbolo del sistema) no son iguales:

| Paso | CMD | PowerShell |
|---|---|---|
| Firewall (como administrador) | `netsh advfirewall firewall add rule name="Mosquitto 1883" dir=in action=allow protocol=TCP localport=1883` | `New-NetFirewallRule -DisplayName "Mosquitto 1883" -Direction Inbound -Protocol TCP -LocalPort 1883 -Action Allow` |
| Detener el servicio | `net stop mosquitto` | `net stop mosquitto` |
| Arrancar el broker (desde `cardiobuddy`) | `"C:\Program Files\mosquitto\mosquitto.exe" -c mosquitto\mosquitto.conf -v` | `& "C:\Program Files\mosquitto\mosquitto.exe" -c mosquitto\mosquitto.conf -v` |
| Servidor (desde `backend`) | `set CB_MQTT_HOST=localhost` y luego `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000` | `$env:CB_MQTT_HOST="localhost"` y luego el mismo `python -m uvicorn ...` |

La regla de firewall se repite para el puerto 8000. `set` y `$env:` solo valen para esa ventana.

### Si algo no conecta

- **El ESP32 no aparece en la ventana de Mosquitto:**
  - La PC y el ESP32 deben estar en la misma red de **2.4 GHz**.
  - Las redes de la universidad o de invitados suelen aislar los equipos entre sí. Para la demo, usen el hotspot de un celular.
  - Revisen que hayan corrido `1_abrir_firewall.bat`.
- **"Only one usage of each socket address":** el servicio de Mosquitto sigue activo. Ejecuten `net stop mosquitto` como administrador.
- **Llegan mensajes pero el panel no cambia:** el `DISPOSITIVO` del firmware no coincide con el código del formulario, o el `TOKEN` no coincide con `CB_DEVICE_TOKEN`.

### Linux / macOS

Instalen Mosquitto con `sudo apt install mosquitto mosquitto-clients` o `brew install mosquitto` y detengan el servicio con `sudo systemctl stop mosquitto` o `brew services stop mosquitto`. Luego:

```bash
mosquitto -c mosquitto/mosquitto.conf -v
cd backend && pip install -r requirements.txt
CB_MQTT_HOST=localhost python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
python herramientas/simulador.py --mqtt localhost --pico-cada 30   # opcional
```

### Sin broker (solo para ver la página)

Si se levanta el servidor sin `CB_MQTT_HOST`, acepta lecturas por HTTP y el simulador funciona sin Mosquitto: `python herramientas/simulador.py --pico-cada 30`.

## Opción B: con Docker

```bash
cp .env.example .env      # ajusta el token y, si quieres, el correo
docker compose up --build
```

Panel en <http://localhost:8000>, broker MQTT en el puerto 1883.

## Firmware del ESP32

1. Instala en Arduino IDE el soporte de placas ESP32 y las librerías listadas al inicio de `firmware/cardiobuddy_esp32/cardiobuddy_esp32.ino`.
2. Conexiones: OLED y MAX30102 comparten I2C (SDA = GPIO 21, SCL = GPIO 22, 3V3, GND); zumbador en GPIO 25.
3. Cambia `WIFI_SSID`, `WIFI_PASS` y `MQTT_HOST` (la IP de la PC con el servidor, ver con `ipconfig`). La PC y el ESP32 deben estar en la misma red de 2.4 GHz.
4. `DISPOSITIVO` debe coincidir con el código del formulario web, y `TOKEN` con `CB_DEVICE_TOKEN`.

Si usan otro sensor de pulso (no MAX30102), basta con reemplazar el cálculo de `fcMostrada`/`spo2Mostrada`; el resto del sketch (pantalla, alerta local y MQTT) no cambia. Si no hay SpO2, el firmware envía `"spo2": null` y el panel muestra `--`.

## Configuración (variables de entorno)

| Variable | Para qué sirve | Por defecto |
|---|---|---|
| `CB_DEVICE_TOKEN` | Token que el dispositivo envía con cada lectura | `cambia-este-token` |
| `CB_MQTT_HOST` / `CB_MQTT_PORT` | Broker MQTT; vacío desactiva MQTT | vacío / 1883 |
| `CB_MQTT_USER` / `CB_MQTT_PASS` | Credenciales del broker | vacío |
| `CB_SMTP_HOST`, `CB_SMTP_PORT`, `CB_SMTP_USER`, `CB_SMTP_PASS` | Envío real del aviso por correo; vacío = aviso simulado | vacío / 587 |
| `CB_PANEL_URL` | Enlace al panel que va en el correo | `http://localhost:8000` |
| `CB_DB` | Archivo SQLite | `backend/cardiobuddy.db` |

Con Gmail se usa `smtp.gmail.com`, puerto 587 y una "contraseña de aplicación".

## Lógica de alertas (Sección III-E)

- FC máxima por edad: 160 lpm (0–2 años), 150 (3–5), 140 (6–12), 130 (13–17). Atención desde 20 lpm por debajo del máximo.
- SpO2: Crítica bajo 92 %, Atención de 92 a 94 %.
- Se avisa tras 2 lecturas críticas seguidas, con 20 s mínimo entre avisos. El banner queda hasta pulsar «Entendido».
- Valores de demostración: deben validarse con personal de pediatría. Se cambian en `backend/app/clasificacion.py`.

## API

| Método | Ruta | Uso |
|---|---|---|
| POST | `/api/perfiles` | Crea o actualiza el perfil del dispositivo |
| GET | `/api/perfiles/{id}` y `/api/perfiles/{id}/resumen` | Perfil, último minuto, eventos y estado del dispositivo |
| POST | `/api/telemetria` | Lectura por HTTP (cabecera `X-Device-Token`) |
| POST | `/api/eventos/{id}/reconocer` | Botón «Entendido» |
| WS | `/ws/{dispositivo}` | Lecturas, alertas y estado en vivo |

Documentación interactiva en <http://localhost:8000/docs>.

## Pendiente para producción (Sección III-G)

Esta versión es para laboratorio y demo: MQTT sin TLS en la red local, sin cuentas de familia. Para producción falta MQTT sobre TLS (puerto 8883) con usuario por dispositivo, HTTPS para el panel, inicio de sesión por familia y cifrado de la base de datos.
