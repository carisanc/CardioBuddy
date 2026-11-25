"""CardioBuddy – servidor de aplicación (capa de aplicación, Fig. 1 del informe).

- Registra el perfil del menor y ajusta los umbrales a su edad.
- Recibe telemetría del ESP32 por MQTT (tópico cardiobuddy/<dispositivo>/telemetria)
  o por HTTP POST /api/telemetria (útil para pruebas sin broker).
- Clasifica cada lectura, guarda el historial en SQLite y la envía al panel por WebSocket.
- Genera alertas y avisa a la familia por correo (real si hay SMTP configurado; si no, simulado).
"""
import asyncio
import json
import logging
import os
import smtplib
import sqlite3
import threading
import time
from email.message import EmailMessage
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field

from .clasificacion import (ENFRIAMIENTO_S, LECTURAS_PARA_ALERTA, SPO2_MIN,
                            estado_fc, estado_spo2, fc_max)

log = logging.getLogger("cardiobuddy")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE = Path(__file__).resolve().parent.parent
FRONTEND = Path(os.getenv("CB_FRONTEND", BASE.parent / "frontend"))
DB_PATH = os.getenv("CB_DB", str(BASE / "cardiobuddy.db"))
# Token compartido que el dispositivo envía en cada lectura (credencial por dispositivo en el diseño objetivo).
DEVICE_TOKEN = os.getenv("CB_DEVICE_TOKEN", "cambia-este-token")
MQTT_HOST = os.getenv("CB_MQTT_HOST", "")          # vacío = sin MQTT
MQTT_PORT = int(os.getenv("CB_MQTT_PORT", "1883"))
MQTT_USER = os.getenv("CB_MQTT_USER", "")
MQTT_PASS = os.getenv("CB_MQTT_PASS", "")
PANEL_URL = os.getenv("CB_PANEL_URL", "http://localhost:8000")
SMTP_HOST = os.getenv("CB_SMTP_HOST", "")          # vacío = aviso simulado
SMTP_PORT = int(os.getenv("CB_SMTP_PORT", "587"))
SMTP_USER = os.getenv("CB_SMTP_USER", "")
SMTP_PASS = os.getenv("CB_SMTP_PASS", "")
OFFLINE_S = 5  # sin lecturas por más de 5 s = dispositivo desconectado

app = FastAPI(title="CardioBuddy API")

# ---------------------------------------------------------------- base de datos
_db_lock = threading.Lock()


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


DB = db()
with _db_lock:
    DB.executescript(
        """
        CREATE TABLE IF NOT EXISTS perfiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL, genero TEXT NOT NULL, edad INTEGER NOT NULL,
            correo TEXT NOT NULL, dispositivo TEXT NOT NULL UNIQUE,
            creado REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS lecturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dispositivo TEXT NOT NULL, ts REAL NOT NULL,
            fc REAL NOT NULL, spo2 REAL, estado_fc TEXT, estado_spo2 TEXT);
        CREATE INDEX IF NOT EXISTS ix_lect ON lecturas(dispositivo, ts);
        CREATE TABLE IF NOT EXISTS eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dispositivo TEXT NOT NULL, ts REAL NOT NULL, tipo TEXT NOT NULL,
            mensaje TEXT NOT NULL, reconocido INTEGER NOT NULL DEFAULT 0);
        """
    )
    DB.commit()


def q(sql, args=(), one=False):
    with _db_lock:
        cur = DB.execute(sql, args)
        DB.commit()
        rows = cur.fetchall()
        return (rows[0] if rows else None) if one else rows


# ---------------------------------------------------------------- modelos
class PerfilIn(BaseModel):
    nombre: str = Field(min_length=1, max_length=60)
    genero: str = Field(pattern="^(Niña|Niño|Prefiero no decirlo)$")
    edad: int = Field(ge=0, le=17)
    correo: EmailStr
    dispositivo: str = Field(default="cb-01", min_length=1, max_length=32)


class Lectura(BaseModel):
    dispositivo: str = Field(min_length=1, max_length=32)
    fc: float = Field(ge=0, le=300)
    spo2: float | None = Field(default=None, ge=0, le=100)
    ts: float | None = None


def perfil_dict(r):
    d = dict(r)
    d["fc_max"] = fc_max(d["edad"])
    d["spo2_min"] = SPO2_MIN
    return d


# ---------------------------------------------------------------- WebSocket hub
class Hub:
    def __init__(self):
        self.clientes: dict[str, set[WebSocket]] = {}

    async def unir(self, disp, ws):
        self.clientes.setdefault(disp, set()).add(ws)

    def salir(self, disp, ws):
        self.clientes.get(disp, set()).discard(ws)

    async def enviar(self, disp, msg):
        for ws in list(self.clientes.get(disp, set())):
            try:
                await ws.send_json(msg)
            except Exception:
                self.salir(disp, ws)


hub = Hub()
LOOP: asyncio.AbstractEventLoop | None = None
_racha: dict[str, int] = {}
_ultimo_aviso: dict[str, float] = {}
_ultima_lectura: dict[str, float] = {}


# ---------------------------------------------------------------- aviso por correo
def enviar_correo(destino: str, asunto: str, cuerpo: str) -> bool:
    if not SMTP_HOST:
        log.info("AVISO SIMULADO a %s: %s", destino, asunto)
        return False
    try:
        m = EmailMessage()
        m["From"], m["To"], m["Subject"] = SMTP_USER, destino, asunto
        m.set_content(cuerpo)
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.starttls()
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(m)
        return True
    except Exception as e:  # no detiene el monitoreo
        log.error("No se pudo enviar el correo: %s", e)
        return False


# ---------------------------------------------------------------- procesamiento
async def procesar(l: Lectura):
    ts = l.ts or time.time()
    _ultima_lectura[l.dispositivo] = ts
    perfil = q("SELECT * FROM perfiles WHERE dispositivo=?", (l.dispositivo,), one=True)
    limite = fc_max(perfil["edad"]) if perfil else 130
    efc, esp = estado_fc(l.fc, limite), estado_spo2(l.spo2)
    q("INSERT INTO lecturas(dispositivo,ts,fc,spo2,estado_fc,estado_spo2) VALUES(?,?,?,?,?,?)",
      (l.dispositivo, ts, l.fc, l.spo2, efc, esp))
    await hub.enviar(l.dispositivo, {"tipo": "lectura", "ts": ts, "fc": round(l.fc),
                                     "spo2": None if l.spo2 is None else round(l.spo2),
                                     "estado_fc": efc, "estado_spo2": esp})

    critica = efc == "critica" or esp == "critica"
    _racha[l.dispositivo] = _racha.get(l.dispositivo, 0) + 1 if critica else 0
    if (perfil and _racha[l.dispositivo] >= LECTURAS_PARA_ALERTA
            and ts - _ultimo_aviso.get(l.dispositivo, 0) >= ENFRIAMIENTO_S):
        _ultimo_aviso[l.dispositivo] = ts
        sp = "sin lectura" if l.spo2 is None else f"{round(l.spo2)}%"
        msg = f"Pico crítico de {perfil['nombre']}: {round(l.fc)} lpm y oxígeno {sp}."
        # Aviso discreto: solo el evento y el enlace al panel, sin historial clínico.
        real = await asyncio.to_thread(
            enviar_correo, perfil["correo"], "CardioBuddy: alerta",
            f"{msg}\nRevisa el panel: {PANEL_URL}\n\nCardioBuddy no sustituye la atención médica.")
        msg += f" Se envió un aviso a {perfil['correo']}" + ("." if real else " (simulado).")
        with _db_lock:
            cur = DB.execute("INSERT INTO eventos(dispositivo,ts,tipo,mensaje) VALUES(?,?,?,?)",
                             (l.dispositivo, ts, "alerta", msg))
            DB.commit()
            eid = cur.lastrowid
        await hub.enviar(l.dispositivo, {"tipo": "alerta", "id": eid, "ts": ts, "mensaje": msg})


# ---------------------------------------------------------------- MQTT (capa de comunicación)
def iniciar_mqtt():
    if not MQTT_HOST:
        log.info("MQTT desactivado (CB_MQTT_HOST vacío). Se acepta telemetría por HTTP.")
        return
    import paho.mqtt.client as mqtt

    def on_connect(c, u, f, rc, p=None):
        log.info("MQTT conectado (%s)", rc)
        c.subscribe("cardiobuddy/+/telemetria", qos=1)

    def on_message(c, u, m):
        try:
            data = json.loads(m.payload)
            if data.pop("token", None) != DEVICE_TOKEN:
                log.warning("Lectura MQTT rechazada: token inválido")
                return
            data["dispositivo"] = m.topic.split("/")[1]
            asyncio.run_coroutine_threadsafe(procesar(Lectura(**data)), LOOP)
        except Exception as e:
            log.warning("Mensaje MQTT inválido: %s", e)

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="cardiobuddy-server")
    if MQTT_USER:
        c.username_pw_set(MQTT_USER, MQTT_PASS)
    c.on_connect, c.on_message = on_connect, on_message
    c.connect_async(MQTT_HOST, MQTT_PORT)
    c.loop_start()


@app.on_event("startup")
async def startup():
    global LOOP
    LOOP = asyncio.get_running_loop()
    iniciar_mqtt()


# ---------------------------------------------------------------- API REST
@app.post("/api/perfiles")
def crear_perfil(p: PerfilIn):
    # Un perfil por dispositivo: si ya existe, se actualiza.
    q("""INSERT INTO perfiles(nombre,genero,edad,correo,dispositivo,creado) VALUES(?,?,?,?,?,?)
         ON CONFLICT(dispositivo) DO UPDATE SET nombre=excluded.nombre, genero=excluded.genero,
         edad=excluded.edad, correo=excluded.correo""",
      (p.nombre.strip(), p.genero, p.edad, p.correo, p.dispositivo.strip(), time.time()))
    r = q("SELECT * FROM perfiles WHERE dispositivo=?", (p.dispositivo.strip(),), one=True)
    q("INSERT INTO eventos(dispositivo,ts,tipo,mensaje,reconocido) VALUES(?,?,?,?,1)",
      (r["dispositivo"], time.time(), "info", "Perfil guardado. Límites ajustados a la edad."))
    return perfil_dict(r)


@app.get("/api/perfiles/{pid}")
def leer_perfil(pid: int):
    r = q("SELECT * FROM perfiles WHERE id=?", (pid,), one=True)
    if not r:
        raise HTTPException(404, "Perfil no encontrado")
    return perfil_dict(r)


@app.get("/api/perfiles/{pid}/resumen")
def resumen(pid: int, segundos: int = 60):
    p = leer_perfil(pid)
    d = p["dispositivo"]
    desde = time.time() - min(segundos, 3600)
    lect = q("SELECT ts,fc,spo2,estado_fc,estado_spo2 FROM lecturas WHERE dispositivo=? AND ts>=? ORDER BY ts",
             (d, desde))
    ev = q("SELECT id,ts,tipo,mensaje,reconocido FROM eventos WHERE dispositivo=? ORDER BY ts DESC LIMIT 30", (d,))
    ultima = _ultima_lectura.get(d)
    return {"perfil": p, "lecturas": [dict(x) for x in lect], "eventos": [dict(x) for x in ev],
            "en_linea": bool(ultima and time.time() - ultima < OFFLINE_S)}


@app.post("/api/telemetria")
async def telemetria(l: Lectura, x_device_token: str = Header(default="")):
    if x_device_token != DEVICE_TOKEN:
        raise HTTPException(401, "Token de dispositivo inválido")
    await procesar(l)
    return {"ok": True}


@app.post("/api/eventos/{eid}/reconocer")
def reconocer(eid: int):
    q("UPDATE eventos SET reconocido=1 WHERE id=?", (eid,))
    return {"ok": True}


@app.get("/api/estado")
def estado():
    return {"mqtt": bool(MQTT_HOST), "correo_real": bool(SMTP_HOST)}


@app.websocket("/ws/{dispositivo}")
async def ws(websocket: WebSocket, dispositivo: str):
    await websocket.accept()
    await hub.unir(dispositivo, websocket)
    try:
        while True:  # latido para detectar desconexiones; el panel no envía datos
            await asyncio.sleep(2)
            ultima = _ultima_lectura.get(dispositivo)
            await websocket.send_json({"tipo": "estado",
                                       "en_linea": bool(ultima and time.time() - ultima < OFFLINE_S)})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.salir(dispositivo, websocket)


# ---------------------------------------------------------------- frontend
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
