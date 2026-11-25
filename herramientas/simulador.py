"""Simulador del ESP32 para probar el panel sin el hardware.

Envía una lectura por segundo con valores basales y, cada cierto tiempo, un pico
crítico de ~8 s (escenarios basal, pico y recuperación de la Tabla III).

Uso:
  python simulador.py                         # HTTP a http://localhost:8000
  python simulador.py --pico-cada 30          # pico cada 30 s
  python simulador.py --mqtt localhost        # publica por MQTT, como el ESP32
"""
import argparse
import json
import math
import random
import time
import urllib.request

p = argparse.ArgumentParser()
p.add_argument("--servidor", default="http://localhost:8000")
p.add_argument("--dispositivo", default="cb-01")
p.add_argument("--token", default="cambia-este-token")
p.add_argument("--pico-cada", type=int, default=40, help="segundos entre picos (0 = sin picos)")
p.add_argument("--mqtt", default="", help="host del broker MQTT; vacío = usar HTTP")
p.add_argument("--puerto-mqtt", type=int, default=1883)
a = p.parse_args()

cliente = None
if a.mqtt:
    import paho.mqtt.client as mqtt
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"sim-{a.dispositivo}")
    cliente.connect(a.mqtt, a.puerto_mqtt)
    cliente.loop_start()


def enviar(fc, spo2):
    if cliente:
        cliente.publish(f"cardiobuddy/{a.dispositivo}/telemetria",
                        json.dumps({"fc": fc, "spo2": spo2, "token": a.token}), qos=1)
        return
    req = urllib.request.Request(
        a.servidor.rstrip("/") + "/api/telemetria",
        data=json.dumps({"dispositivo": a.dispositivo, "fc": fc, "spo2": spo2}).encode(),
        headers={"Content-Type": "application/json", "X-Device-Token": a.token})
    urllib.request.urlopen(req, timeout=5).read()


t, pico = 0, 0
print(f"Simulando {a.dispositivo}. Ctrl+C para detener.")
while True:
    t += 1
    if a.pico_cada and t % a.pico_cada == 0:
        pico = 8
    fc = 92 + 7 * math.sin(t / 7) + random.uniform(-2, 2)
    spo2 = 98 - (1 if random.random() < .2 else 0)
    if pico:
        fc, spo2, pico = random.uniform(152, 166), random.choice([91, 92]), pico - 1
    try:
        enviar(round(fc, 1), spo2)
        print(f"{time.strftime('%H:%M:%S')}  FC {fc:5.1f} lpm  SpO2 {spo2}%{'  << pico' if pico else ''}")
    except Exception as e:
        print("No se pudo enviar:", e)
    time.sleep(1)
