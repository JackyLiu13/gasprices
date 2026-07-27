// gasprices — ESP32-C6 gas price indicator for Richmond Hill, ON.
//
// Wakes up, pulls one small JSON file, runs the verdict engine locally, and
// shows the answer on the onboard LCD plus the RGB LED. All the scraping and
// modelling lives in the backend, so tuning the data never means reflashing.
//
// Boards Manager: esp32 by Espressif >= 3.0  (C6 support landed in 3.0)
// Board:          ESP32C6 Dev Module
// Hardware:       Waveshare ESP32-C6-LCD-1.47 (onboard 172x320 ST7789)
// Libraries:      ArduinoJson (v7), Adafruit ST7735/ST7789, Adafruit GFX
//
// Copy config.h.example -> config.h before building.

#include <Arduino.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <esp_sleep.h>
#include <time.h>

#include <ArduinoJson.h>

#include "config.h"
#include "verdict.h"
#include "ui.h"

#define HIST_MAX        32
#define CACHE_MAGIC     0x47415301UL   // "GAS\1"
#define WIFI_TIMEOUT_MS 20000
#define AWAKE_REFRESH_S (6 * 3600)     // fallback cadence if the clock never syncs
#define LED_BRIGHTNESS  48             // 0-255; the onboard WS2812 is very bright

// --- State that survives deep sleep ----------------------------------------
// The C6 keeps RTC memory powered while sleeping, so a failed fetch can still
// fall back to yesterday's numbers instead of showing nothing.
RTC_DATA_ATTR static struct {
  uint32_t magic;
  int32_t  today;
  int32_t  pred[GP_PRED_MAX];
  uint8_t  predLen;
  int32_t  window_lo, window_hi;
  int32_t  hist[HIST_MAX];
  uint8_t  histLen;
  uint32_t epoch;          // 'epoch' field from the JSON, i.e. when it was built
  // Cheapest tracked station. today/pred/window are already priced at this
  // station, so this is the label for the number, not a second number.
  char     bestLabel[20];
  int32_t  bestSave;       // tenths of a cent vs the cheapest "regular" station
  bool     bestConfident;  // enough observations for the offset to be trusted
} cache;

RTC_DATA_ATTR static TankState gTank = TANK_HALF;
RTC_DATA_ATTR static uint32_t  gBoots = 0;

static GpConfig gCfg;
static bool     gHaveLcd = false;
static bool     gOnline   = false;
static uint32_t gLastFetchMs = 0;

// ---------------------------------------------------------------------------
// LED
// ---------------------------------------------------------------------------
static void setLed(uint32_t rgb) {
#ifdef RGB_BUILTIN
  uint8_t r = (rgb >> 16) & 0xFF, g = (rgb >> 8) & 0xFF, b = rgb & 0xFF;
  rgbLedWrite(RGB_BUILTIN, (r * LED_BRIGHTNESS) / 255,
                           (g * LED_BRIGHTNESS) / 255,
                           (b * LED_BRIGHTNESS) / 255);
#else
  (void)rgb;
#endif
}

// ---------------------------------------------------------------------------
// Network
// ---------------------------------------------------------------------------
static bool connectWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(true);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS) {
    delay(250);
  }
  bool ok = WiFi.status() == WL_CONNECTED;
  Serial.printf("wifi: %s\n", ok ? WiFi.localIP().toString().c_str() : "FAILED");
  return ok;
}

static void syncTime() {
  configTzTime(TZ_INFO, "pool.ntp.org", "time.nist.gov");
  struct tm t;
  if (getLocalTime(&t, 8000)) {
    Serial.printf("time: %04d-%02d-%02d %02d:%02d local\n",
                  t.tm_year + 1900, t.tm_mon + 1, t.tm_mday, t.tm_hour, t.tm_min);
  } else {
    Serial.println("time: NTP failed (age checks disabled)");
  }
}

static bool fetchData() {
  WiFiClientSecure client;
  // No cert pinning: this endpoint is a public price feed, and a stale or
  // spoofed number is a cosmetic problem, not a security one. If that bothers
  // you, swap in client.setCACert() with the ISRG/GTS root for your host.
  client.setInsecure();
  client.setTimeout(15);

  HTTPClient http;
  if (!http.begin(client, DATA_URL)) {
    Serial.println("http: begin failed");
    return false;
  }
  http.setTimeout(15000);
  http.addHeader("Accept", "application/json");
  http.setUserAgent("gasprices-c6/1.0");

  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("http: %d\n", code);
    http.end();
    return false;
  }

  JsonDocument doc;
  DeserializationError err = deserializeJson(doc, http.getStream());
  http.end();
  if (err) {
    Serial.printf("json: %s\n", err.c_str());
    return false;
  }

  int32_t today = doc["today_cad"] | 0;
  if (today < 500 || today > 3500) {          // sanity: 0.50-3.50 $/L
    Serial.printf("json: implausible price %ld\n", (long)today);
    return false;
  }

  cache.magic     = CACHE_MAGIC;
  cache.today     = today;
  cache.window_lo = doc["window_lo"] | today;
  cache.window_hi = doc["window_hi"] | today;
  cache.epoch     = doc["epoch"] | 0UL;

  cache.predLen = 0;
  for (JsonVariant v : doc["pred"].as<JsonArray>()) {
    if (cache.predLen >= GP_PRED_MAX) break;
    cache.pred[cache.predLen++] = v.as<int32_t>();
  }

  cache.histLen = 0;
  for (JsonVariant v : doc["hist"].as<JsonArray>()) {
    if (cache.histLen >= HIST_MAX) break;
    cache.hist[cache.histLen++] = v.as<int32_t>();
  }

  // Station block is optional — a schema-1 feed still drives the display.
  JsonObject best = doc["best"].as<JsonObject>();
  const char *label = best["label"] | "";
  strncpy(cache.bestLabel, label, sizeof cache.bestLabel - 1);
  cache.bestLabel[sizeof cache.bestLabel - 1] = '\0';
  cache.bestSave = best["save"] | 0;
  cache.bestConfident = best["confident"] | false;

  Serial.printf("data: today=%ld pred=%u hist=%u lo=%ld hi=%ld\n",
                (long)cache.today, cache.predLen, cache.histLen,
                (long)cache.window_lo, (long)cache.window_hi);
  if (cache.bestLabel[0]) {
    Serial.printf("best: %s save=%ld%s\n", cache.bestLabel, (long)cache.bestSave,
                  cache.bestConfident ? "" : " (low confidence)");
  }
  return true;
}

// ---------------------------------------------------------------------------
// Decide + show
// ---------------------------------------------------------------------------
static int32_t dataAgeMinutes() {
  time_t now = time(nullptr);
  if (now < 1700000000 || cache.epoch == 0) return -1;   // clock never synced
  int32_t age = (int32_t)((now - (time_t)cache.epoch) / 60);
  return age < 0 ? 0 : age;
}

static void decideAndRender() {
  if (cache.magic != CACHE_MAGIC) {
    setLed(gp_verdict_color(V_STALE));
    if (gHaveLcd) uiMessage("No data yet", "check wifi / URL");
    return;
  }

  GpInput in;
  in.today      = cache.today;
  in.pred_len   = cache.predLen;
  for (uint8_t i = 0; i < cache.predLen; i++) in.pred[i] = cache.pred[i];
  in.window_lo  = cache.window_lo;
  in.window_hi  = cache.window_hi;
  in.age_minutes = dataAgeMinutes();
  in.tank       = gTank;

  GpVerdict v;
  gp_evaluate(&in, &gCfg, &v);

  char price[16], reason[48];
  gp_fmt_price(in.today, price, sizeof price);
  gp_reason(&in, &v, reason, sizeof reason);
  Serial.printf("verdict: %s | %s | %s | level=%ld%% tank=%d age=%ldm\n",
                price, gp_verdict_name(v.verdict), reason,
                (long)v.level_pct, (int)gTank, (long)in.age_minutes);

  setLed(gp_verdict_color(v.verdict));
  if (gHaveLcd) {
    uiRender(&in, &v, cache.hist, cache.histLen, gOnline,
             cache.bestLabel, cache.bestSave, cache.bestConfident);
  }
}

static void refresh() {
  gOnline = connectWifi();
  if (gOnline) {
    syncTime();
    if (!fetchData()) gOnline = false;   // fall through to the cached numbers
  }
  gLastFetchMs = millis();
  decideAndRender();
}

// ---------------------------------------------------------------------------
// Button: short press cycles the tank state, long press forces a refresh.
// ---------------------------------------------------------------------------
static void pollButton() {
  static uint32_t downAt = 0;

  bool down = digitalRead(TANK_BUTTON_PIN) == LOW;
  if (down && downAt == 0) {
    downAt = millis();
  } else if (!down && downAt != 0) {
    uint32_t held = millis() - downAt;
    downAt = 0;
    if (held < 40) return;                       // debounce
    if (held > 1000) {
      Serial.println("button: forced refresh");
      if (gHaveLcd) uiMessage("Refreshing...", nullptr);
      refresh();
    } else {
      gTank = (TankState)((gTank + 1) % 3);
      Serial.printf("button: tank=%d\n", (int)gTank);
      decideAndRender();                         // re-decide, no refetch needed
    }
  }
}

// ---------------------------------------------------------------------------
// Sleep scheduling
// ---------------------------------------------------------------------------
static uint32_t secondsUntilNextRefresh() {
  struct tm t;
  if (!getLocalTime(&t, 200)) return AWAKE_REFRESH_S;

  int32_t now = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec;
  int32_t targets[2] = {REFRESH_HOUR_AM * 3600 + REFRESH_MINUTE * 60,
                        REFRESH_HOUR_PM * 3600 + REFRESH_MINUTE * 60};
  for (int i = 0; i < 2; i++) {
    if (targets[i] > now) return (uint32_t)(targets[i] - now);
  }
  return (uint32_t)(86400 - now + targets[0]);   // tomorrow morning
}

// ---------------------------------------------------------------------------
void setup() {
  Serial.begin(115200);
  delay(200);
  gBoots++;
  Serial.printf("\ngasprices boot #%lu (%s)\n", (unsigned long)gBoots,
                esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER ? "timer" : "power-on");

  gCfg = gp_default_config();
  pinMode(TANK_BUTTON_PIN, INPUT_PULLUP);
  setLed(0x000010);

  gHaveLcd = uiBegin();
  Serial.printf("lcd: %dx%d ST7789 rot=%d\n", LCD_W, LCD_H, LCD_ROTATION);
  uiMessage("gasprices", "connecting...");

  refresh();

#if USE_DEEP_SLEEP
  uint32_t s = secondsUntilNextRefresh();
  Serial.printf("sleeping %lu s (screen keeps the last verdict)\n", (unsigned long)s);
  Serial.flush();
  WiFi.disconnect(true);
  setLed(0x000000);
  esp_sleep_enable_timer_wakeup((uint64_t)s * 1000000ULL);
  esp_deep_sleep_start();
#endif
}

void loop() {
#if !USE_DEEP_SLEEP
  pollButton();

  static uint32_t nextRefreshMs = 0;
  if (nextRefreshMs == 0) nextRefreshMs = millis() + secondsUntilNextRefresh() * 1000UL;
  if ((int32_t)(millis() - nextRefreshMs) >= 0) {
    refresh();
    nextRefreshMs = millis() + secondsUntilNextRefresh() * 1000UL;
  }
  delay(20);
#endif
}
