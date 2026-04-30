/*
  ModernReader — ESP32 Output Node
  ═══════════════════════════════════════════════════════════════════════════

  Role
  ────
  This node connects to the ModernReader Signal Gateway over WiFi/WebSocket,
  listens for "state_update" messages, and drives:

    • WS2812 / RGB LED  — colour + brightness
    • Vibration motor   — PWM intensity via MOSFET/transistor
    • SG90 servo        — angle position

  Hardware BOM (minimum)
  ──────────────────────
    ESP32 DevKit V1
    WS2812 RGB LED or single RGB LED (common-cathode)
    SG90 micro servo
    Vibration motor (coin type or ERM)
    NPN transistor (e.g. 2N2222) or MOSFET (e.g. IRL520) for vibration motor
    1N4007 flyback diode (across motor)
    1kΩ resistor (base/gate of transistor)
    330Ω resistor (NeoPixel data line, optional but recommended)
    External 5V power supply for servo + NeoPixel (share GND with ESP32)

  Pin map  (change ONLY these defines, then rebuild)
  ────────────────────────────────────────────────────
    LED_PIN       GPIO 16   — NeoPixel data  (single WS2812) or R-G-B PWM
    LED_R_PIN     GPIO 16   — used when USE_RGB_PWM = true
    LED_G_PIN     GPIO 17
    LED_B_PIN     GPIO 18
    VIB_PIN       GPIO 19   — vibration motor control (PWM → transistor)
    SERVO_PIN     GPIO 21   — SG90 signal wire

  UART Monitor
  ────────────
    Baud 115200.  Prints received JSON and current state/tangible values.

  Libraries required  (install via Arduino Library Manager)
  ──────────────────────────────────────────────────────────
    • ArduinoJson  (v7 or v6)
    • WebSockets   by Markus Sattler
    • ESP32Servo   (for servo PWM)
    • Adafruit NeoPixel  (if USE_NEOPIXEL = true)

  ═══════════════════════════════════════════════════════════════════════════
*/

#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <ESP32Servo.h>

// ── NeoPixel switch ─────────────────────────────────────────────────────────
// Set to true  if you have a WS2812 / NeoPixel LED.
// Set to false to use three separate PWM channels (common-cathode RGB).
#define USE_NEOPIXEL false

#if USE_NEOPIXEL
  #include <Adafruit_NeoPixel.h>
  #define NEOPIXEL_PIN   16
  #define NEOPIXEL_COUNT 1
  Adafruit_NeoPixel strip(NEOPIXEL_COUNT, NEOPIXEL_PIN, NEO_GRB + NEO_KHZ800);
#else
  #define LED_R_PIN 16
  #define LED_G_PIN 17
  #define LED_B_PIN 18
#endif

#define VIB_PIN   19
#define SERVO_PIN 21

// ── WiFi credentials ─────────────────────────────────────────────────────────
// Change to your network.
const char* WIFI_SSID     = "YOUR_SSID";
const char* WIFI_PASSWORD = "YOUR_PASSWORD";

// ── Gateway address ──────────────────────────────────────────────────────────
// IP of the machine running  uvicorn app.main:app --port 8765
const char* GW_HOST = "192.168.1.100";   // ← change to your server IP
const uint16_t GW_PORT = 8765;
const char* GW_PATH = "/ws";

// ── PWM channels ─────────────────────────────────────────────────────────────
#if !USE_NEOPIXEL
  #define PWM_CH_R   0
  #define PWM_CH_G   1
  #define PWM_CH_B   2
#endif
#define PWM_CH_VIB   3
#define PWM_FREQ     1000    // Hz
#define PWM_RES      8       // bits → 0–255

// ── Objects ───────────────────────────────────────────────────────────────────
WebSocketsClient wsClient;
Servo            myServo;

// ── Current tangible state ────────────────────────────────────────────────────
struct Tangible {
  uint8_t r, g, b;
  uint8_t vibration;   // 0–255
  uint8_t servo;       // 0–180°
  String  label;
} current = {20, 20, 20, 0, 90, "unknown"};

bool wsConnected = false;

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

void setLED(uint8_t r, uint8_t g, uint8_t b) {
#if USE_NEOPIXEL
  strip.setPixelColor(0, strip.Color(r, g, b));
  strip.show();
#else
  ledcWrite(PWM_CH_R, r);
  ledcWrite(PWM_CH_G, g);
  ledcWrite(PWM_CH_B, b);
#endif
}

void setVibration(uint8_t val) {
  ledcWrite(PWM_CH_VIB, val);
}

void setServo(uint8_t angle) {
  myServo.write(constrain(angle, 0, 180));
}

void applyTangible(const Tangible& t) {
  setLED(t.r, t.g, t.b);
  setVibration(t.vibration);
  setServo(t.servo);
}

// ─────────────────────────────────────────────────────────────────────────────
// WebSocket event handler
// ─────────────────────────────────────────────────────────────────────────────

void onWebSocketEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {

    case WStype_DISCONNECTED:
      wsConnected = false;
      Serial.println("[WS] Disconnected — retrying…");
      // Slow pulse to signal no connection
      setLED(10, 0, 0);
      setVibration(0);
      setServo(90);
      break;

    case WStype_CONNECTED:
      wsConnected = true;
      Serial.printf("[WS] Connected to ws://%s:%d%s\n", GW_HOST, GW_PORT, GW_PATH);
      setLED(0, 30, 0);  // green flash on connect
      delay(200);
      setLED(0, 0, 0);
      break;

    case WStype_TEXT: {
      // Parse JSON
      JsonDocument doc;
      DeserializationError err = deserializeJson(doc, payload, length);
      if (err) {
        Serial.printf("[WS] JSON parse error: %s\n", err.c_str());
        break;
      }

      const char* msgType = doc["type"] | "";

      if (strcmp(msgType, "state_update") == 0 ||
          strcmp(msgType, "connected")    == 0) {

        JsonObject tangible = doc["tangible"];
        if (tangible.isNull()) break;

        JsonArray led = tangible["led"];
        if (led.size() >= 3) {
          current.r = (uint8_t)constrain((int)led[0], 0, 255);
          current.g = (uint8_t)constrain((int)led[1], 0, 255);
          current.b = (uint8_t)constrain((int)led[2], 0, 255);
        }
        current.vibration = (uint8_t)constrain((int)(tangible["vibration"] | 0), 0, 255);
        current.servo     = (uint8_t)constrain((int)(tangible["servo"]     | 90), 0, 180);
        current.label     = tangible["label"] | "unknown";

        applyTangible(current);

        Serial.printf("[OUT] state=%s  LED=(%d,%d,%d)  vib=%d  servo=%d°\n",
          current.label.c_str(),
          current.r, current.g, current.b,
          current.vibration, current.servo);
      }

      // Heartbeat ping — nothing to do, WebSockets library handles pong
      break;
    }

    case WStype_PING:
    case WStype_PONG:
      break;

    default:
      break;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Setup
// ─────────────────────────────────────────────────────────────────────────────

void setup() {
  Serial.begin(115200);
  Serial.println("\n\n=== ModernReader Output Node ===");

  // ── PWM channels ──────────────────────────────────────────────────────────
#if !USE_NEOPIXEL
  ledcSetup(PWM_CH_R,   PWM_FREQ, PWM_RES);
  ledcSetup(PWM_CH_G,   PWM_FREQ, PWM_RES);
  ledcSetup(PWM_CH_B,   PWM_FREQ, PWM_RES);
  ledcAttachPin(LED_R_PIN, PWM_CH_R);
  ledcAttachPin(LED_G_PIN, PWM_CH_G);
  ledcAttachPin(LED_B_PIN, PWM_CH_B);
#else
  strip.begin();
  strip.show();
#endif

  ledcSetup(PWM_CH_VIB, PWM_FREQ, PWM_RES);
  ledcAttachPin(VIB_PIN, PWM_CH_VIB);

  // ── Servo ─────────────────────────────────────────────────────────────────
  ESP32PWM::allocateTimer(0);
  myServo.setPeriodHertz(50);       // Standard 50Hz servo
  myServo.attach(SERVO_PIN, 500, 2400);  // min/max pulse µs
  myServo.write(90);

  // Startup LED flash (white)
  setLED(50, 50, 50);
  delay(300);
  setLED(0, 0, 0);

  // ── WiFi ──────────────────────────────────────────────────────────────────
  Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    setLED(0, 0, 20);
    delay(250);
    setLED(0, 0, 0);
  }
  Serial.printf("\n[WiFi] Connected  IP=%s\n", WiFi.localIP().toString().c_str());

  // ── WebSocket ─────────────────────────────────────────────────────────────
  wsClient.begin(GW_HOST, GW_PORT, GW_PATH);
  wsClient.onEvent(onWebSocketEvent);
  wsClient.setReconnectInterval(3000);   // retry every 3 s if disconnected
}

// ─────────────────────────────────────────────────────────────────────────────
// Loop
// ─────────────────────────────────────────────────────────────────────────────

void loop() {
  wsClient.loop();

  // ── Optional: "breathing" animation while in calm state ───────────────────
  // Remove this block if you want purely event-driven output.
  static unsigned long lastBreath = 0;
  static float breathPhase        = 0.0f;

  if (wsConnected && current.label == "calm") {
    unsigned long now = millis();
    if (now - lastBreath > 30) {
      lastBreath = now;
      breathPhase += 0.05f;
      if (breathPhase > TWO_PI) breathPhase -= TWO_PI;
      float factor = (sin(breathPhase) + 1.0f) / 2.0f;   // 0–1
      uint8_t r = (uint8_t)(current.r * factor * 0.5f);
      uint8_t g = (uint8_t)(current.g * factor * 0.5f);
      uint8_t b = (uint8_t)(current.b * factor);
      setLED(r, g, b);
    }
  }
}
