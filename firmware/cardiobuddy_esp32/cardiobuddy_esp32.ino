/*
  CardioBuddy – firmware ESP32 (capa de dispositivo, Fig. 1 del informe)

  Hardware:
    - ESP32 DevKit
    - Módulo pulsioxímetro MAX30102 (I2C 0x57)
    - Pantalla OLED SSD1306 128x64 (I2C 0x3C)
    - Zumbador en GPIO 25
    Conexión I2C compartida: SDA = GPIO 21, SCL = GPIO 22, VCC = 3V3, GND = GND

  Librerías (Gestor de librerías de Arduino IDE):
    - "SparkFun MAX3010x Pulse and Proximity Sensor Library"
    - "Adafruit SSD1306" y "Adafruit GFX Library"
    - "PubSubClient" (Nick O'Leary)

  Publica cada ~1 s en MQTT, tópico cardiobuddy/<DISPOSITIVO>/telemetria:
    {"fc": 92, "spo2": 98, "token": "..."}
  La pantalla y el zumbador funcionan aunque no haya Wi-Fi.
*/
#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"

// ------------------------------------------------------------ configuración
const char* WIFI_SSID   = "TU_RED";
const char* WIFI_PASS   = "TU_CLAVE";
const char* MQTT_HOST   = "192.168.1.50";      // IP de la PC donde corre el servidor
const int   MQTT_PORT   = 1883;
const char* DISPOSITIVO = "cb-01";             // mismo código que en el formulario web
const char* TOKEN       = "cambia-este-token"; // igual a CB_DEVICE_TOKEN del servidor

const int EDAD = 6;            // para la alerta local (la web usa la edad del perfil)
const int SPO2_MIN = 92;
const int PIN_BUZZER = 25;
const uint32_t IR_DEDO = 50000; // bajo este valor no hay dedo sobre el sensor

// ------------------------------------------------------------ objetos
Adafruit_SSD1306 oled(128, 64, &Wire, -1);
MAX30105 sensor;
WiFiClient red;
PubSubClient mqtt(red);

const int N = 100;             // muestras para el algoritmo (4 s a 25 Hz)
uint32_t irBuf[N], redBuf[N];
int32_t spo2, fc; int8_t spo2Ok, fcOk;
int fcMostrada = 0, spo2Mostrada = 0, criticas = 0;
uint8_t onda[128]; int ondaPos = 0;
unsigned long ultimoIntentoWifi = 0, ultimoIntentoMqtt = 0;

int fcMax(int edad) { return edad <= 2 ? 160 : edad <= 5 ? 150 : edad <= 12 ? 140 : 130; }

// ------------------------------------------------------------ conectividad (no bloqueante)
void mantenerRed() {
  if (WiFi.status() != WL_CONNECTED) {
    if (millis() - ultimoIntentoWifi > 10000) {
      ultimoIntentoWifi = millis();
      WiFi.begin(WIFI_SSID, WIFI_PASS);
    }
    return;
  }
  if (!mqtt.connected() && millis() - ultimoIntentoMqtt > 5000) {
    ultimoIntentoMqtt = millis();
    mqtt.connect((String("cb-") + DISPOSITIVO).c_str());
  }
  mqtt.loop();
}

void publicar() {
  if (!mqtt.connected()) return;
  char json[96];
  if (spo2Mostrada > 0)
    snprintf(json, sizeof(json), "{\"fc\":%d,\"spo2\":%d,\"token\":\"%s\"}", fcMostrada, spo2Mostrada, TOKEN);
  else
    snprintf(json, sizeof(json), "{\"fc\":%d,\"spo2\":null,\"token\":\"%s\"}", fcMostrada, TOKEN);
  char topico[64];
  snprintf(topico, sizeof(topico), "cardiobuddy/%s/telemetria", DISPOSITIVO);
  mqtt.publish(topico, json);
}

// ------------------------------------------------------------ pantalla
void dibujar(bool dedo) {
  oled.clearDisplay();
  oled.setTextColor(SSD1306_WHITE);
  if (!dedo) {
    oled.setTextSize(1); oled.setCursor(10, 24); oled.print("Coloca tu dedo");
    oled.setCursor(10, 36); oled.print("sobre el sensor");
  } else {
    oled.setTextSize(2); oled.setCursor(4, 0);
    if (fcMostrada > 0) oled.print(fcMostrada); else oled.print("--");
    oled.setTextSize(1); oled.print(" BPM");
    oled.setCursor(84, 4); if (spo2Mostrada > 0) { oled.print(spo2Mostrada); oled.print("%"); }
    // corazón
    oled.fillCircle(70, 5, 3, SSD1306_WHITE); oled.fillCircle(76, 5, 3, SSD1306_WHITE);
    oled.fillTriangle(67, 6, 79, 6, 73, 13, SSD1306_WHITE);
    // onda tipo ECG (señal IR desplazándose)
    for (int x = 1; x < 128; x++) {
      int a = onda[(ondaPos + x - 1) % 128], b = onda[(ondaPos + x) % 128];
      oled.drawLine(x - 1, a, x, b, SSD1306_WHITE);
    }
  }
  oled.display();
}

void agregarOnda(uint32_t ir) {
  static float base = 0, minv = 1e9, maxv = 0;
  base = base == 0 ? ir : base * 0.95 + ir * 0.05;
  float v = ir - base;
  minv = min(minv * 0.99f + v * 0.01f, v); maxv = max(maxv * 0.99f + v * 0.01f, v);
  float rango = max(maxv - minv, 50.0f);
  int y = 60 - (int)((v - minv) / rango * 38);   // zona de 22 a 60 px
  onda[ondaPos] = constrain(y, 22, 60);
  ondaPos = (ondaPos + 1) % 128;
}

// ------------------------------------------------------------ setup / loop
void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUZZER, OUTPUT);
  Wire.begin(21, 22);
  if (!oled.begin(SSD1306_SWITCHCAPVCC, 0x3C)) Serial.println("OLED no encontrada");
  if (!sensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("MAX30102 no encontrado. Revisa el cableado.");
    oled.clearDisplay(); oled.setCursor(0, 20); oled.setTextColor(SSD1306_WHITE);
    oled.print("Sensor no encontrado"); oled.display();
    while (true) delay(1000);
  }
  // brillo, promedio, modo (2 = rojo + IR), frecuencia, ancho de pulso, rango ADC
  sensor.setup(60, 4, 2, 100, 411, 4096);
  for (int i = 0; i < 128; i++) onda[i] = 45;
  WiFi.mode(WIFI_STA);
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  // primer llenado del búfer
  for (int i = 0; i < N; i++) {
    while (!sensor.available()) sensor.check();
    redBuf[i] = sensor.getRed(); irBuf[i] = sensor.getIR(); sensor.nextSample();
  }
}

void loop() {
  // desplaza 75 muestras y toma 25 nuevas (~1 s)
  for (int i = 25; i < N; i++) { redBuf[i - 25] = redBuf[i]; irBuf[i - 25] = irBuf[i]; }
  for (int i = 75; i < N; i++) {
    while (!sensor.available()) { sensor.check(); mantenerRed(); }
    redBuf[i] = sensor.getRed(); irBuf[i] = sensor.getIR(); sensor.nextSample();
    agregarOnda(irBuf[i]);
    if (i % 5 == 0) dibujar(irBuf[i] > IR_DEDO);
  }

  bool dedo = irBuf[N - 1] > IR_DEDO;
  if (!dedo) { fcMostrada = spo2Mostrada = 0; criticas = 0; noTone(PIN_BUZZER); dibujar(false); return; }

  maxim_heart_rate_and_oxygen_saturation(irBuf, N, redBuf, &spo2, &spo2Ok, &fc, &fcOk);
  if (fcOk && fc > 30 && fc < 250) fcMostrada = fcMostrada ? (fcMostrada * 3 + fc) / 4 : fc; // suavizado
  if (spo2Ok && spo2 > 50 && spo2 <= 100) spo2Mostrada = spo2;

  if (fcMostrada > 0) {
    publicar();
    Serial.printf("FC %d lpm  SpO2 %d%%\n", fcMostrada, spo2Mostrada);
    // alerta local: no depende de la nube
    bool critica = fcMostrada > fcMax(EDAD) || (spo2Mostrada > 0 && spo2Mostrada < SPO2_MIN);
    criticas = critica ? criticas + 1 : 0;
    if (criticas >= 2) tone(PIN_BUZZER, 2000, 300); else noTone(PIN_BUZZER);
  }
}
