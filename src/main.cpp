// Fox beacon firmware for the LilyGO T-Beam Supreme 144-148 MHz.
// Each cycle can send a voice clip from the SD card, then the ARDF MO pattern
// for fox 1-5, a tracking signal, an optional Morse message, and a callsign ID,
// then waits for the next cycle. The MO pattern is not a legal ID, so the
// callsign ends every cycle.
// Settings are saved in flash and changed from
// a phone over a Wi-Fi window that opens at boot or on the BOOT button.
// Nothing transmits until Start or Send test is used, or Auto start is set.

#include <Arduino.h>
#include <ESPAsyncWebServer.h>
#include <Preferences.h>
#include <RadioLib.h>
#include <SD.h>
#include <U8g2lib.h>
#include <WiFi.h>
#include <Wire.h>
#include <soc/rtc_cntl_reg.h>

#include <algorithm>
#include <vector>

#define XPOWERS_CHIP_AXP2101
#include <XPowersLib.h>
#include <qrcode.h>

// Pin map from LilyGO's T-Beam Supreme 144-148 MHz hardware doc.
constexpr int PIN_OLED_SDA = 17;
constexpr int PIN_OLED_SCL = 18;
constexpr int PIN_PMU_SDA = 42;
constexpr int PIN_PMU_SCL = 41;
constexpr int PIN_RADIO_SCK = 12;
constexpr int PIN_RADIO_MISO = 13;
constexpr int PIN_RADIO_MOSI = 11;
constexpr int PIN_RADIO_CS = 10;
constexpr int PIN_RADIO_RST = 5;
constexpr int PIN_RADIO_DIO0 = 2;
constexpr int PIN_RADIO_DIO1 = 1;
constexpr int PIN_RADIO_DIO2 = 4;
constexpr int PIN_BOOT_BUTTON = 0;
constexpr int PIN_SD_SCK = 36;
constexpr int PIN_SD_MISO = 37;
constexpr int PIN_SD_MOSI = 35;
constexpr int PIN_SD_CS = 47;
constexpr int PIN_IMU_CS = 34;

// SX1278 FSK registers and frequency step for retuning during audio.
constexpr uint8_t REG_FRF_MSB = 0x06;
constexpr uint8_t REG_PLL_HOP = 0x44;
constexpr uint8_t FAST_HOP_ON = 0x80;
constexpr double FRF_STEP_HZ = 32000000.0 / 524288.0;
constexpr int AUDIO_PEAK_HZ = 4000;
constexpr float AUDIO_FM_DEVIATION_KHZ = 1.0;
constexpr float NORMAL_DEVIATION_KHZ = 5.0;

// Clips are 8 kHz mono 16-bit WAV files, converted by the phone's browser.
constexpr const char *AUDIO_DIR = "/audio";
constexpr uint32_t CLIP_RATE = 8000;
constexpr uint32_t SPLASH_MS = 3000;
constexpr uint32_t CLIP_SAMPLE_US = 125;
constexpr size_t CLIP_HEADER_BYTES = 44;
constexpr size_t CLIP_BUFFER_SAMPLES = 2048;
constexpr size_t MAX_UPLOAD_BYTES = 2UL * 1024UL * 1024UL;

// Slower CPU saves battery. Clips run at full speed to protect sample timing.
constexpr uint32_t IDLE_CPU_MHZ = 80;
constexpr uint32_t AUDIO_CPU_MHZ = 240;

constexpr uint8_t PMU_ADDRESS = 0x34;
constexpr int PITCH_OPTIONS[] = {600, 700, 800, 900, 1000};
constexpr int WARBLE_STEP_HZ = 200;
constexpr uint32_t WARBLE_PERIOD_MS = 125;
// The default Byonics MicroFox 50 tone sequence, from its v0.4 manual. Each
// group plays twice, as silence, high, silence, high, silence, then low for
// three steps.
constexpr int MF50_GROUPS[][2] = {{622, 370}, {740, 494}, {880, 622}, {987, 740}};
constexpr int MF50_MORSE_HZ = 831;
constexpr uint32_t MF50_STEP_MS = 50;
constexpr uint32_t TONE_GAP_MS = 300;
constexpr uint32_t MIN_GAP_MS = 10000;
constexpr float LOW_BATTERY_VOLTS = 3.3;
constexpr uint32_t WIFI_WINDOW_MS = 5UL * 60UL * 1000UL;
constexpr uint32_t DISPLAY_INTERVAL_MS = 1000;
constexpr uint32_t DISPLAY_REFRESH_WAIT_MS = 300;
constexpr uint32_t SERIAL_INTERVAL_MS = 5000;

// Li-ion open circuit millivolts from 100% down to 0% in 10% steps, from
// Meshtastic's default curve. The AXP2101 gauge reads far too low on a fresh
// cell, so percent comes from voltage instead.
constexpr int BATTERY_CURVE_MV[] = {4190, 4050, 3990, 3890, 3800, 3720, 3630, 3530, 3420, 3300, 3100};

// Capped at 10 dBm until the board's real output and limit have been measured.
constexpr int POWER_OPTIONS[] = {2, 5, 8, 10};

// The AXP2101 can't measure battery current, so the runtime estimate uses these
// typical figures, not values measured on this board. TX is extra draw on top
// of the base, one entry per POWER_OPTIONS.
constexpr int BASE_CURRENT_MA = 70;
constexpr int TX_CURRENT_MA[] = {50, 55, 65, 70};
constexpr float USABLE_CAPACITY = 0.9;
constexpr int ESTIMATED_ID_SECONDS = 15;

struct Settings {
  String foxId = "1";
  String callsign = "N0CALL";  // set your own callsign on the settings page
  String message = "";
  float frequency = 146.565;
  int power = 2;
  int toneSeconds = 10;
  int intervalSeconds = 60;
  int wpm = 15;
  int startDelaySeconds = 0;
  int runLimitMinutes = 0;
  int batteryMah = 3500;
  int pitch = 700;
  int tracking = 0;
  bool autoStart = false;
  bool voiceClips = false;
};

enum class BeaconState { Stopped, Waiting, Pattern, Tone, Message, Id, Audio };

// Values of Settings::tracking, in the order shown on the settings page.
constexpr int TRACK_STEADY = 0;
constexpr int TRACK_WARBLE = 1;
constexpr int TRACK_REPEAT_MO = 2;
constexpr int TRACK_MF50 = 3;
constexpr const char *TRACKING_NAMES[] = {"Steady tone", "Warble", "Repeat MO pattern", "MicroFox 50 sequence"};

XPowersAXP2101 pmu;
SPIClass radioSpi(FSPI);
SPIClass sdSpi(HSPI);
Module radioModule(PIN_RADIO_CS, PIN_RADIO_DIO0, PIN_RADIO_RST, PIN_RADIO_DIO1, radioSpi);
SX1278 radio = &radioModule;
AFSKClient audio(&radio, PIN_RADIO_DIO2);
MorseClient morse(&audio);
U8G2 display;
AsyncWebServer server(80);
Preferences preferences;

Settings settings;
SemaphoreHandle_t settingsLock;

bool pmuReady = false;
bool displayReady = false;
int radioState = RADIOLIB_ERR_UNKNOWN;
bool wifiOn = false;
uint32_t wifiStartedAt = 0;
String wifiSsid;
String wifiPassword;

volatile BeaconState beaconState = BeaconState::Stopped;
volatile bool beaconArmed = false;
volatile bool testRequested = false;
char sdInfo[80] = "Not checked";
volatile bool sdReadable = false;
volatile bool sdFormatRequested = false;
char audioResult[96] = "";

// The player reads from one buffer while a task on the other core refills the
// other, so SD delays can't disturb sample timing.
int16_t clipBuffers[2][CLIP_BUFFER_SAMPLES];
volatile size_t clipLengths[2] = {0, 0};
volatile int clipFillIndex = 0;
volatile bool clipReading = false;
File clipFile;
TaskHandle_t clipReaderHandle = nullptr;
size_t nextClip = 0;

volatile size_t clipNumber = 0;
volatile size_t clipCount = 0;
volatile uint32_t displayRefreshRequested = 0;
volatile uint32_t displayRefreshDone = 0;
char lastDrawnState[24] = "";

File uploadFile;
volatile bool uploadOk = false;
volatile bool startRequested = false;
volatile bool stopRequested = false;
volatile bool lowBattery = false;
volatile uint32_t nextCycleAt = 0;
volatile uint32_t stopAt = 0;
volatile uint32_t lastCycleMs = 0;
volatile const char *stopReason = "";

// Talks to the OLED through Wire directly. U8g2's stock Arduino callback takes
// over a minute to initialise the panel on this core.
uint8_t oledByteCallback(u8x8_t *u8x8, uint8_t message, uint8_t length, void *data) {
  switch (message) {
    case U8X8_MSG_BYTE_SEND:
      Wire.write(static_cast<uint8_t *>(data), length);
      break;
    case U8X8_MSG_BYTE_START_TRANSFER:
      Wire.beginTransmission(u8x8_GetI2CAddress(u8x8) >> 1);
      break;
    case U8X8_MSG_BYTE_END_TRANSFER:
      Wire.endTransmission();
      break;
    default:
      break;
  }
  return 1;
}

uint8_t oledDelayCallback(u8x8_t *, uint8_t message, uint8_t value, void *) {
  if (message == U8X8_MSG_DELAY_MILLI) {
    delay(value);
  }
  return 1;
}

// Fetches the status page over the board's own network stack.
void httpSelfTest() {
  WiFiClient client;
  if (!client.connect(WiFi.softAPIP(), 80, 2000)) {
    Serial.println("Self test: connect failed");
    return;
  }
  client.print("GET / HTTP/1.1\r\nHost: fox\r\nConnection: close\r\n\r\n");
  uint32_t startedAt = millis();
  size_t received = 0;
  String firstLine;
  while (millis() - startedAt < 4000 && (client.connected() || client.available())) {
    while (client.available()) {
      char c = client.read();
      received++;
      if (received < 64 && c != '\r' && c != '\n') {
        firstLine += c;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  Serial.printf("Self test: %u bytes in %lu ms, starts: %s\n", received, millis() - startedAt, firstLine.c_str());
}

// Serial commands: 't' sends one test cycle, 'h' fetches the status page, 'r'
// reboots, 'b' reboots into the bootloader for flashing. After 'b', the flag
// has to be cleared over esptool or the chip stays there.
void serialCommandTask(void *) {
  for (;;) {
    while (Serial.available()) {
      int command = Serial.read();
      if (command == 't') {
        testRequested = true;
      } else if (command == 'h') {
        httpSelfTest();
      } else if (command == 'b') {
        REG_WRITE(RTC_CNTL_OPTION1_REG, RTC_CNTL_FORCE_DOWNLOAD_BOOT);
        esp_restart();
      } else if (command == 'r') {
        esp_restart();
      }
    }
    vTaskDelay(pdMS_TO_TICKS(100));
  }
}

Settings currentSettings() {
  xSemaphoreTake(settingsLock, portMAX_DELAY);
  Settings copy = settings;
  xSemaphoreGive(settingsLock);
  return copy;
}

void loadSettings() {
  Settings defaults;
  preferences.begin("fox", true);
  settings.foxId = preferences.getString("foxId", defaults.foxId);
  settings.callsign = preferences.getString("callsign", defaults.callsign);
  settings.message = preferences.getString("message", defaults.message);
  settings.frequency = preferences.getFloat("frequency", defaults.frequency);
  settings.power = preferences.getInt("power", defaults.power);
  settings.toneSeconds = preferences.getInt("tone", defaults.toneSeconds);
  settings.intervalSeconds = preferences.getInt("interval", defaults.intervalSeconds);
  settings.wpm = preferences.getInt("wpm", defaults.wpm);
  settings.startDelaySeconds = preferences.getInt("delaySec", defaults.startDelaySeconds);
  settings.runLimitMinutes = preferences.getInt("limit", defaults.runLimitMinutes);
  settings.batteryMah = preferences.getInt("mah", defaults.batteryMah);
  settings.pitch = preferences.getInt("pitch", defaults.pitch);
  settings.tracking = preferences.getInt("tracking", defaults.tracking);
  settings.autoStart = preferences.getBool("auto", defaults.autoStart);
  settings.voiceClips = preferences.getBool("voice", defaults.voiceClips);
  preferences.end();
}

void saveSettings(const Settings &updated) {
  xSemaphoreTake(settingsLock, portMAX_DELAY);
  settings = updated;
  xSemaphoreGive(settingsLock);

  preferences.begin("fox", false);
  preferences.putString("foxId", updated.foxId);
  preferences.putString("callsign", updated.callsign);
  preferences.putString("message", updated.message);
  preferences.putFloat("frequency", updated.frequency);
  preferences.putInt("power", updated.power);
  preferences.putInt("tone", updated.toneSeconds);
  preferences.putInt("interval", updated.intervalSeconds);
  preferences.putInt("wpm", updated.wpm);
  preferences.putInt("delaySec", updated.startDelaySeconds);
  preferences.putInt("limit", updated.runLimitMinutes);
  preferences.putInt("mah", updated.batteryMah);
  preferences.putInt("pitch", updated.pitch);
  preferences.putInt("tracking", updated.tracking);
  preferences.putBool("auto", updated.autoStart);
  preferences.putBool("voice", updated.voiceClips);
  preferences.end();
}

// Keeps only characters Morse can send, which also makes them safe in HTML.
String cleanText(String text, size_t maxLength) {
  text.toUpperCase();
  String cleaned;
  for (size_t i = 0; i < text.length() && cleaned.length() < maxLength; i++) {
    char c = text[i];
    if (isAlphaNumeric(c) || c == ' ' || c == '/' || c == '?' || c == '.' || c == ',') {
      cleaned += c;
    }
  }
  cleaned.trim();
  return cleaned;
}

struct Battery {
  bool present;
  bool charging;
  bool usb;
  float volts;
  int percent;
};

int batteryPercent(int millivolts) {
  constexpr int steps = sizeof(BATTERY_CURVE_MV) / sizeof(BATTERY_CURVE_MV[0]) - 1;
  if (millivolts >= BATTERY_CURVE_MV[0]) {
    return 100;
  }
  for (int i = 0; i < steps; i++) {
    int high = BATTERY_CURVE_MV[i];
    int low = BATTERY_CURVE_MV[i + 1];
    if (millivolts >= low) {
      return 100 - i * 10 - (high - millivolts) * 10 / (high - low);
    }
  }
  return 0;
}

// Only loop() reads the PMU. Web handlers run in another task and use this copy.
Battery latestBattery = {};

Battery readBattery() {
  Battery battery = {};
  if (!pmuReady) {
    return battery;
  }
  battery.present = pmu.isBatteryConnect();
  battery.charging = pmu.isCharging();
  battery.usb = pmu.isVbusIn();
  if (battery.present) {
    int millivolts = pmu.getBattVoltage();
    battery.volts = millivolts / 1000.0f;
    battery.percent = batteryPercent(millivolts);
  }
  lowBattery = battery.present && !battery.usb && battery.volts < LOW_BATTERY_VOLTS;
  return battery;
}

// Estimated runtime on battery at the current settings, with Wi-Fi off.
String runtimeText(const Battery &battery, const Settings &current) {
  int txMilliamps = TX_CURRENT_MA[0];
  for (size_t i = 0; i < sizeof(POWER_OPTIONS) / sizeof(POWER_OPTIONS[0]); i++) {
    if (POWER_OPTIONS[i] == current.power) {
      txMilliamps = TX_CURRENT_MA[i];
    }
  }
  float txSeconds = lastCycleMs > 0 ? lastCycleMs / 1000.0f : current.toneSeconds + ESTIMATED_ID_SECONDS;
  float duty = min(1.0f, txSeconds / current.intervalSeconds);
  float averageMilliamps = BASE_CURRENT_MA + txMilliamps * duty;
  float remainingMah = current.batteryMah * USABLE_CAPACITY * battery.percent / 100.0f;
  int minutes = remainingMah / averageMilliamps * 60;
  return minutes >= 60 ? String(minutes / 60) + "H" : String(minutes) + "M";
}

String batteryText(const Battery &battery) {
  if (!pmuReady) {
    return "PMU FAIL";
  }
  if (!battery.present) {
    return battery.usb ? "No batt, USB" : "No batt";
  }
  return String(battery.volts, 2) + "V " + String(battery.percent) + "% RT: " +
         runtimeText(battery, currentSettings());
}

String clockText(uint32_t seconds) {
  char text[16];
  snprintf(text, sizeof(text), "%lu:%02lu", seconds / 60, seconds % 60);
  return text;
}

uint32_t wifiSecondsLeft() {
  uint32_t elapsed = millis() - wifiStartedAt;
  return elapsed >= WIFI_WINDOW_MS ? 0 : (WIFI_WINDOW_MS - elapsed) / 1000;
}

String wifiText() {
  if (!wifiOn) {
    return "WiFi off (BOOT=on)";
  }
  uint8_t clients = WiFi.softAPgetStationNum();
  if (clients > 0) {
    return "WiFi " + String(clients) + " connected";
  }
  return "WiFi " + clockText(wifiSecondsLeft()) + " " + wifiSsid;
}

String beaconText() {
  if (radioState != RADIOLIB_ERR_NONE) {
    return "RADIO ERR " + String(radioState);
  }
  switch (beaconState) {
    case BeaconState::Audio:
      return "TX VOICE " + String(clipNumber) + "/" + String(clipCount);
    case BeaconState::Pattern:
      return "TX MO";
    case BeaconState::Tone:
      return "TX TONE";
    case BeaconState::Message:
      return "TX MESSAGE";
    case BeaconState::Id:
      return "TX ID";
    case BeaconState::Waiting: {
      int32_t left = static_cast<int32_t>(nextCycleAt - millis());
      return "NEXT " + clockText(left > 0 ? (left + 999) / 1000 : 0);
    }
    default:
      return String("STOPPED") + (const char *)stopReason;
  }
}

void sendMorse(const String &text, bool stopEarly) {
  for (size_t i = 0; i < text.length(); i++) {
    if (stopEarly && stopRequested) {
      return;
    }
    morse.print(text[i]);
  }
}

// ARDF pattern for fox 1-5: MOE, MOI, MOS, MOH, MO5. Empty for any other ID.
String moPattern(const String &foxId) {
  if (foxId == "1") return "MOE";
  if (foxId == "2") return "MOI";
  if (foxId == "3") return "MOS";
  if (foxId == "4") return "MOH";
  if (foxId == "5") return "MO5";
  return "";
}

void sendTrackingTone(const Settings &cycle) {
  uint32_t startedAt = millis();
  uint32_t toneMs = cycle.toneSeconds * 1000UL;
  bool high = false;
  audio.tone(cycle.pitch);
  while (millis() - startedAt < toneMs && !stopRequested) {
    if (cycle.tracking == TRACK_WARBLE) {
      high = !high;
      audio.tone(high ? cycle.pitch + WARBLE_STEP_HZ : cycle.pitch, false);
      vTaskDelay(pdMS_TO_TICKS(WARBLE_PERIOD_MS));
    } else {
      vTaskDelay(pdMS_TO_TICKS(20));
    }
  }
  audio.noTone();
  vTaskDelay(pdMS_TO_TICKS(TONE_GAP_MS));
}

void playStep(int frequency) {
  if (frequency > 0) {
    audio.tone(frequency, false);
  } else {
    audio.noTone(true);
  }
  vTaskDelay(pdMS_TO_TICKS(MF50_STEP_MS));
}

void sendMf50Sequence(const Settings &cycle) {
  uint32_t startedAt = millis();
  uint32_t toneMs = cycle.toneSeconds * 1000UL;
  audio.tone(MF50_GROUPS[0][0]);
  while (millis() - startedAt < toneMs && !stopRequested) {
    for (const auto &group : MF50_GROUPS) {
      for (int pass = 0; pass < 2 && !stopRequested; pass++) {
        for (int frequency : {0, group[0], 0, group[0], 0, group[1], group[1], group[1]}) {
          playStep(frequency);
        }
      }
    }
  }
  audio.noTone();
  vTaskDelay(pdMS_TO_TICKS(TONE_GAP_MS));
}

void sendRepeatingPattern(const Settings &cycle, const String &pattern) {
  uint32_t startedAt = millis();
  uint32_t toneMs = cycle.toneSeconds * 1000UL;
  while (millis() - startedAt < toneMs && !stopRequested) {
    sendMorse(pattern + " ", true);
  }
}

std::vector<String> listClips() {
  std::vector<String> names;
  File folder = SD.open(AUDIO_DIR);
  if (!folder) {
    return names;
  }
  for (File entry = folder.openNextFile(); entry; entry = folder.openNextFile()) {
    String name = entry.name();
    if (!entry.isDirectory() && name.endsWith(".wav")) {
      names.push_back(name);
    }
  }
  std::sort(names.begin(), names.end(), [](const String &a, const String &b) { return a < b; });
  return names;
}

void fillClipBuffer(int index) {
  size_t bytes = clipFile.read(reinterpret_cast<uint8_t *>(clipBuffers[index]), sizeof(clipBuffers[index]));
  clipLengths[index] = bytes / sizeof(int16_t);
}

void clipReaderTask(void *) {
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    fillClipBuffer(clipFillIndex);
    clipReading = false;
  }
}

bool openClip(const String &name) {
  clipFile = SD.open(String(AUDIO_DIR) + "/" + name);
  if (!clipFile) {
    return false;
  }
  uint8_t header[CLIP_HEADER_BYTES];
  bool valid = clipFile.read(header, sizeof(header)) == sizeof(header) && memcmp(header, "RIFF", 4) == 0 &&
               header[22] == 1 && header[34] == 16 &&
               (header[24] | header[25] << 8 | header[26] << 16 | header[27] << 24) == CLIP_RATE;
  if (!valid) {
    clipFile.close();
  }
  return valid;
}

// Retunes the radio for each sample. Fast hop makes a write to the low
// frequency byte take effect at once. Runs at high priority to keep timing
// steady, so the display and status log pause until it finishes.
void playClip(const Settings &cycle, uint32_t &maxLateUs, uint32_t &stalls) {
  Module *mod = &radioModule;
  radio.setFrequencyDeviation(AUDIO_FM_DEVIATION_KHZ);
  radio.transmitDirect();
  pinMode(PIN_RADIO_DIO2, OUTPUT);
  digitalWrite(PIN_RADIO_DIO2, LOW);
  uint8_t pllHop = mod->SPIreadRegister(REG_PLL_HOP);
  mod->SPIwriteRegister(REG_PLL_HOP, pllHop | FAST_HOP_ON);

  setCpuFrequencyMhz(AUDIO_CPU_MHZ);
  fillClipBuffer(0);
  fillClipBuffer(1);
  int32_t baseFrf = lround(cycle.frequency * 1000000.0 / FRF_STEP_HZ);
  int32_t peakSteps = lround(AUDIO_PEAK_HZ / FRF_STEP_HZ);
  UBaseType_t priority = uxTaskPriorityGet(nullptr);
  vTaskPrioritySet(nullptr, configMAX_PRIORITIES - 2);

  int active = 0;
  size_t position = 0;
  uint32_t dueAt = micros();
  while (!stopRequested) {
    if (position >= clipLengths[active]) {
      if (clipLengths[active] < CLIP_BUFFER_SAMPLES) {
        break;
      }
      if (clipReading) {
        stalls++;
        while (clipReading) {
        }
      }
      clipFillIndex = active;
      clipReading = true;
      xTaskNotifyGive(clipReaderHandle);
      active ^= 1;
      position = 0;
      if (clipLengths[active] == 0) {
        break;
      }
    }
    while (static_cast<int32_t>(micros() - dueAt) < 0) {
    }
    maxLateUs = max<uint32_t>(maxLateUs, micros() - dueAt);
    int32_t frf = baseFrf + clipBuffers[active][position++] * peakSteps / 32767;
    uint8_t bytes[3] = {static_cast<uint8_t>(frf >> 16), static_cast<uint8_t>(frf >> 8), static_cast<uint8_t>(frf)};
    mod->SPIwriteRegisterBurst(REG_FRF_MSB, bytes, sizeof(bytes));
    dueAt += CLIP_SAMPLE_US;
  }

  vTaskPrioritySet(nullptr, priority);
  while (clipReading) {
    vTaskDelay(1);
  }
  setCpuFrequencyMhz(IDLE_CPU_MHZ);
  radio.standby();
  mod->SPIwriteRegister(REG_PLL_HOP, pllHop);
  radio.setFrequencyDeviation(NORMAL_DEVIATION_KHZ);
  radio.setFrequency(cycle.frequency);
}

void sendNextClip(const Settings &cycle) {
  std::vector<String> names = listClips();
  if (names.empty()) {
    snprintf(audioResult, sizeof(audioResult), "No clips on the SD card");
    return;
  }
  if (nextClip >= names.size()) {
    nextClip = 0;
  }
  String name = names[nextClip++];
  if (!openClip(name)) {
    snprintf(audioResult, sizeof(audioResult), "Skipped %s: not 8 kHz mono 16-bit", name.c_str());
    Serial.println(audioResult);
    return;
  }
  beaconState = BeaconState::Audio;
  clipNumber = nextClip;
  clipCount = names.size();

  // loop() can't run during playback, so the OLED has to update first. The
  // counters stop a draw already in progress from counting as this one.
  displayRefreshRequested = displayRefreshRequested + 1;
  uint32_t refreshStartedAt = millis();
  while (displayRefreshDone != displayRefreshRequested && millis() - refreshStartedAt < DISPLAY_REFRESH_WAIT_MS) {
    vTaskDelay(pdMS_TO_TICKS(10));
  }
  uint32_t refreshMs = millis() - refreshStartedAt;

  uint32_t maxLateUs = 0;
  uint32_t stalls = 0;
  playClip(cycle, maxLateUs, stalls);
  clipFile.close();
  snprintf(audioResult, sizeof(audioResult), "%s: worst %lu us late, %lu stalls", name.c_str(), maxLateUs, stalls);
  Serial.println(audioResult);
  Serial.printf("OLED took %lu ms before the clip, showing %s\n", refreshMs, lastDrawnState);
  vTaskDelay(pdMS_TO_TICKS(TONE_GAP_MS));
}

// Always ends with the ID, even when stopped part way through, so every
// transmission is identified.
void runCycle() {
  Settings cycle = currentSettings();
  uint32_t startedAt = millis();
  Serial.println("Cycle start");
  radio.setFrequency(cycle.frequency);
  radio.setOutputPower(cycle.power);
  if (cycle.voiceClips && sdReadable && !stopRequested) {
    sendNextClip(cycle);
  }
  morse.begin(cycle.tracking == TRACK_MF50 ? MF50_MORSE_HZ : cycle.pitch, cycle.wpm);

  String pattern = moPattern(cycle.foxId);
  if (!pattern.isEmpty() && !stopRequested) {
    beaconState = BeaconState::Pattern;
    sendMorse(pattern + " ", true);
  }
  if (cycle.toneSeconds > 0 && !stopRequested) {
    beaconState = BeaconState::Tone;
    if (cycle.tracking == TRACK_REPEAT_MO && !pattern.isEmpty()) {
      sendRepeatingPattern(cycle, pattern);
    } else if (cycle.tracking == TRACK_MF50) {
      sendMf50Sequence(cycle);
    } else {
      sendTrackingTone(cycle);
    }
  }
  if (!cycle.message.isEmpty() && !stopRequested) {
    beaconState = BeaconState::Message;
    sendMorse(cycle.message + " ", true);
  }
  beaconState = BeaconState::Id;
  sendMorse("DE " + cycle.callsign + " FOX " + cycle.foxId, false);
  audio.noTone();
  radio.standby();
  lastCycleMs = millis() - startedAt;
  Serial.printf("Cycle done in %lu ms\n", lastCycleMs);
}

void stopBeacon(const char *reason) {
  beaconArmed = false;
  stopRequested = false;
  stopReason = reason;
  beaconState = BeaconState::Stopped;
  Serial.printf("Beacon stopped%s\n", reason);
}

void armBeacon() {
  Settings current = currentSettings();
  uint32_t now = millis();
  uint32_t delayMs = current.startDelaySeconds * 1000UL;
  nextCycleAt = now + delayMs;
  stopAt = current.runLimitMinutes > 0 ? now + delayMs + current.runLimitMinutes * 60000UL : 0;
  stopReason = "";
  beaconArmed = true;
  beaconState = BeaconState::Waiting;
  Serial.println("Beacon started");
}

// Owns the radio. Morse sending blocks, so it runs apart from the OLED and web
// server in loop().
void beaconTask(void *) {
  for (;;) {
    if (radioState != RADIOLIB_ERR_NONE) {
      vTaskDelay(pdMS_TO_TICKS(1000));
      continue;
    }
    if (startRequested) {
      startRequested = false;
      armBeacon();
    }
    if (stopRequested) {
      stopBeacon("");
    } else if (testRequested) {
      testRequested = false;
      if (lowBattery) {
        Serial.println("Test skipped: low battery");
      } else {
        runCycle();
        if (beaconArmed) {
          beaconState = BeaconState::Waiting;
        } else {
          stopBeacon("");
        }
      }
    } else if (beaconArmed && lowBattery) {
      stopBeacon(" LOW BATT");
    } else if (beaconArmed && stopAt != 0 && static_cast<int32_t>(millis() - stopAt) >= 0) {
      stopBeacon(" TIME UP");
    } else if (beaconArmed && static_cast<int32_t>(millis() - nextCycleAt) >= 0) {
      Settings current = currentSettings();
      uint32_t cycleStart = nextCycleAt;
      runCycle();
      if (stopRequested) {
        stopBeacon("");
      } else {
        uint32_t next = cycleStart + current.intervalSeconds * 1000UL;
        uint32_t earliest = millis() + MIN_GAP_MS;
        nextCycleAt = static_cast<int32_t>(next - earliest) > 0 ? next : earliest;
        beaconState = BeaconState::Waiting;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(50));
  }
}

String pageHeader(const char *title) {
  return String("<!doctype html><meta charset=utf-8>"
                "<meta name=viewport content='width=device-width,initial-scale=1'>"
                "<title>Wi-F0X - ") +
         title +
         "</title><style>body{font:18px sans-serif;margin:20px;max-width:480px}"
         "label{display:block;margin:12px 0 4px}input,select,button{font:inherit;padding:8px;box-sizing:border-box}"
         "input[type=text],input[type=number],select{width:100%}"
         "button{width:100%;margin:6px 0}form{margin:0}"
         ".brand{display:flex;justify-content:space-between;align-items:baseline;border-bottom:2px solid;"
         "padding-bottom:6px;margin-bottom:8px}.brand b{font-size:24px}.brand a{font-size:14px}</style><body>"
         "<div class=brand><span>KC9MNE &middot; <b>Wi-F0X</b></span>"
         "<a href='https://kc9mne.com'>kc9mne.com</a></div><h1>" +
         title + "</h1>";
}

String postButton(const char *path, const char *label) {
  return String("<form method=post action=") + path + "><button>" + label + "</button></form>";
}

void handleNotFound(AsyncWebServerRequest *request) {
  Serial.printf("HTTP 404 %s from %s\n", request->url().c_str(), request->client()->remoteIP().toString().c_str());
  request->send(404, "text/plain", "Not found");
}

void handleRoot(AsyncWebServerRequest *request) {
  Serial.printf("HTTP GET / from %s\n", request->client()->remoteIP().toString().c_str());
  Settings current = currentSettings();
  Battery battery = latestBattery;
  String page = pageHeader("Fox status");
  page += "<meta http-equiv=refresh content=5>";
  page += "<p><b>ID:" + current.foxId + " " + current.callsign + "</b></p>";
  page += "<p><b>" + beaconText() + "</b></p>";
  page += "<p>" + String(current.frequency, 3) + " MHz, " + String(current.power) + " dBm</p>";
  page += "<p>Battery: " + batteryText(battery) + "</p>";
  if (battery.charging) {
    page += "<p>Charging</p>";
  }
  page += "<p>" + String(TRACKING_NAMES[current.tracking]) + " at " + String(current.pitch) + " Hz, " +
          String(current.toneSeconds) + " s, every " + String(current.intervalSeconds) + " s</p>";
  if (lastCycleMs > 0) {
    page += "<p>Last cycle: " + String(lastCycleMs / 1000.0, 1) + " s on air</p>";
  }
  page += beaconArmed ? postButton("/stop", "Stop") : postButton("/start", "Start");
  page += postButton("/test", "Send test cycle");
  if (audioResult[0] != '\0') {
    page += String("<p>Last clip: ") + audioResult + "</p>";
  }
  page += String("<p>SD card: ") + sdInfo + "</p>";
  if (sdReadable) {
    page += "<p><a href=/audio>Voice clips</a></p>";
  } else {
    page += "<p><a href=/sdformat>Format SD card</a></p>";
  }
  page += "<p><a href=/settings>Settings</a></p>";
  request->send(200, "text/html", page);
}

String textField(const char *name, const char *label, const String &value, const char *extra) {
  return String("<label>") + label + "</label><input type=text name=" + name + " value='" + value + "' " + extra + ">";
}

String numberField(const char *name, const char *label, int value, int low, int high) {
  return String("<label>") + label + "</label><input type=number name=" + name + " value=" + value + " min=" + low +
         " max=" + high + ">";
}

void handleSettings(AsyncWebServerRequest *request) {
  Settings current = currentSettings();
  String page = pageHeader("Fox settings");
  page += "<form method=post action=/save>";
  page += textField("foxId", "Fox ID (1-5 sends MOE, MOI, MOS, MOH, MO5)", current.foxId, "maxlength=6 required");
  page += textField("callsign", "Callsign", current.callsign, "maxlength=10 required");
  page += textField("message", "Morse message (optional)", current.message, "maxlength=40");
  page += textField("frequency", "Frequency MHz (144-148)", String(current.frequency, 3), "inputmode=decimal required");
  page += "<label>Power</label><select name=power>";
  for (int option : POWER_OPTIONS) {
    page += "<option value=" + String(option) + (option == current.power ? " selected>" : ">") + String(option) +
            " dBm</option>";
  }
  page += "</select>";
  page += "<label>Tracking signal</label><select name=tracking>";
  for (int i = TRACK_STEADY; i <= TRACK_MF50; i++) {
    page += "<option value=" + String(i) + (i == current.tracking ? " selected>" : ">") + TRACKING_NAMES[i] +
            "</option>";
  }
  page += "</select>";
  page += "<label>Tone pitch</label><select name=pitch>";
  for (int option : PITCH_OPTIONS) {
    page += "<option value=" + String(option) + (option == current.pitch ? " selected>" : ">") + String(option) +
            " Hz</option>";
  }
  page += "</select>";
  page += numberField("tone", "Tracking seconds (0 for none)", current.toneSeconds, 0, 120);
  page += numberField("interval", "Seconds between cycle starts", current.intervalSeconds, 15, 3600);
  page += numberField("wpm", "Morse speed wpm", current.wpm, 5, 20);
  page += numberField("delay", "Start delay seconds", current.startDelaySeconds, 0, 86400);
  page += numberField("limit", "Stop after minutes (0 for never)", current.runLimitMinutes, 0, 1440);
  page += numberField("mah", "Battery capacity mAh", current.batteryMah, 500, 10000);
  page += String("<label><input type=checkbox name=auto") + (current.autoStart ? " checked" : "") +
          "> Auto start at power on</label>";
  page += String("<label><input type=checkbox name=voice") + (current.voiceClips ? " checked" : "") +
          "> Play a voice clip each cycle</label>";
  page += "<button>Save</button></form><p><a href=/>Back</a></p>";
  request->send(200, "text/html", page);
}

void handleSave(AsyncWebServerRequest *request) {
  Settings updated = currentSettings();
  String foxId = cleanText(request->arg("foxId"), 6);
  String callsign = cleanText(request->arg("callsign"), 10);
  float frequency = request->arg("frequency").toFloat();
  if (foxId.isEmpty() || callsign.isEmpty() || frequency < 144.0 || frequency > 148.0) {
    request->send(400, "text/html", pageHeader("Not saved") + "<p>Check the ID, callsign and frequency.</p>"
                                                              "<p><a href=/settings>Back</a></p>");
    return;
  }
  updated.foxId = foxId;
  updated.callsign = callsign;
  updated.message = cleanText(request->arg("message"), 40);
  updated.frequency = frequency;
  int power = request->arg("power").toInt();
  for (int option : POWER_OPTIONS) {
    if (option == power) {
      updated.power = power;
    }
  }
  int pitch = request->arg("pitch").toInt();
  for (int option : PITCH_OPTIONS) {
    if (option == pitch) {
      updated.pitch = pitch;
    }
  }
  updated.tracking = constrain(request->arg("tracking").toInt(), TRACK_STEADY, TRACK_MF50);
  updated.toneSeconds = constrain(request->arg("tone").toInt(), 0, 120);
  updated.intervalSeconds = constrain(request->arg("interval").toInt(), 15, 3600);
  updated.wpm = constrain(request->arg("wpm").toInt(), 5, 20);
  updated.startDelaySeconds = constrain(request->arg("delay").toInt(), 0, 86400);
  updated.runLimitMinutes = constrain(request->arg("limit").toInt(), 0, 1440);
  updated.batteryMah = constrain(request->arg("mah").toInt(), 500, 10000);
  updated.autoStart = request->hasArg("auto");
  updated.voiceClips = request->hasArg("voice");
  saveSettings(updated);
  request->redirect("/");
}

void handleStart(AsyncWebServerRequest *request) {
  startRequested = true;
  request->redirect("/");
}

void handleStop(AsyncWebServerRequest *request) {
  stopRequested = true;
  request->redirect("/");
}

// Ignored during a cycle so a repeated tap can't queue another transmission.
void handleTest(AsyncWebServerRequest *request) {
  if (beaconState == BeaconState::Stopped || beaconState == BeaconState::Waiting) {
    testRequested = true;
  }
  request->redirect("/");
}

bool radioIdle() {
  return beaconState == BeaconState::Stopped || beaconState == BeaconState::Waiting;
}

void handleSdFormatPage(AsyncWebServerRequest *request) {
  String page = pageHeader("Format SD card");
  if (sdReadable) {
    page += "<p>This card is already readable, so it won't be formatted here. Use a PC to erase it.</p>";
  } else if (beaconArmed || !radioIdle()) {
    page += "<p>Stop the beacon first.</p>";
  } else {
    page += "<p>This erases everything on the card.</p>"
            "<form method=post action=/sdformat>"
            "<label>Type ERASE to confirm</label><input type=text name=confirm autocomplete=off required>"
            "<button>Format</button></form>";
  }
  page += "<p><a href=/>Back</a></p>";
  request->send(200, "text/html", page);
}

void handleSdFormat(AsyncWebServerRequest *request) {
  if (request->arg("confirm") == "ERASE" && !sdReadable && !beaconArmed && radioIdle()) {
    sdFormatRequested = true;
    snprintf(sdInfo, sizeof(sdInfo), "formatting, refresh shortly");
  }
  request->redirect("/");
}

bool clipsEditable() {
  return sdReadable && !beaconArmed && radioIdle();
}

// Keeps file names plain so they are safe in paths and HTML.
String clipFileName(String name) {
  name.toLowerCase();
  name.replace(".wav", "");
  String cleaned;
  for (size_t i = 0; i < name.length() && cleaned.length() < 24; i++) {
    char c = name[i];
    if (isAlphaNumeric(c) || c == '-' || c == '_') {
      cleaned += c;
    }
  }
  return (cleaned.isEmpty() ? String("clip") : cleaned) + ".wav";
}

// The browser decodes the picked file, converts it to 8 kHz mono 16-bit WAV,
// and uploads that, so no PC or MP3 decoding on the board is needed.
const char CLIP_UPLOAD_SCRIPT[] = R"JS(<script>
async function upload(){
  const files=[...document.getElementById('files').files];
  const status=document.getElementById('status');
  const context=new (window.AudioContext||window.webkitAudioContext)();
  for(const file of files){
    status.textContent='Converting '+file.name;
    const decoded=await context.decodeAudioData(await file.arrayBuffer());
    const count=Math.ceil(decoded.duration*8000);
    const offline=new OfflineAudioContext(1,count,8000);
    const source=offline.createBufferSource();
    source.buffer=decoded;
    source.connect(offline.destination);
    source.start();
    const samples=(await offline.startRendering()).getChannelData(0);
    let peak=0;
    for(let i=0;i<count;i++)peak=Math.max(peak,Math.abs(samples[i]));
    const gain=peak>0?0.95/peak:1;
    const view=new DataView(new ArrayBuffer(44+count*2));
    const text=(at,value)=>{for(let i=0;i<4;i++)view.setUint8(at+i,value.charCodeAt(i));};
    text(0,'RIFF');view.setUint32(4,36+count*2,true);text(8,'WAVE');text(12,'fmt ');
    view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);
    view.setUint32(24,8000,true);view.setUint32(28,16000,true);view.setUint16(32,2,true);
    view.setUint16(34,16,true);text(36,'data');view.setUint32(40,count*2,true);
    for(let i=0;i<count;i++)view.setInt16(44+i*2,Math.round(samples[i]*gain*32767),true);
    status.textContent='Uploading '+file.name;
    const name=encodeURIComponent(file.name.replace(/\.[^.]*$/,''));
    const reply=await fetch('/upload?name='+name,{method:'POST',body:view.buffer});
    if(!reply.ok){status.textContent='Upload failed: '+file.name;return;}
  }
  location.reload();
}
function start(){upload().catch(e=>{document.getElementById('status').textContent='Failed: '+e;});}
</script>)JS";

void handleAudioPage(AsyncWebServerRequest *request) {
  String page = pageHeader("Voice clips");
  page += CLIP_UPLOAD_SCRIPT;
  std::vector<String> names = listClips();
  page += "<p>" + String(names.size()) + " clips. They play in name order, one per cycle.</p>";
  if (clipsEditable()) {
    page += "<input type=file id=files accept='audio/*' multiple>"
            "<button onclick=start()>Convert and upload</button><p id=status></p>";
  } else {
    page += "<p>Stop the beacon to upload or delete clips.</p>";
  }
  for (const String &name : names) {
    page += "<p>" + name + "</p>";
    if (clipsEditable()) {
      page += "<form method=post action=/audio/delete><input type=hidden name=name value='" + name +
              "'><button>Delete</button></form>";
    }
  }
  page += "<p><a href=/>Back</a></p>";
  request->send(200, "text/html", page);
}

void handleUploadBody(AsyncWebServerRequest *request, uint8_t *data, size_t length, size_t index, size_t total) {
  if (index == 0) {
    uploadOk = clipsEditable() && total <= MAX_UPLOAD_BYTES;
    if (uploadOk) {
      uploadFile = SD.open(String(AUDIO_DIR) + "/" + clipFileName(request->arg("name")), FILE_WRITE);
      uploadOk = static_cast<bool>(uploadFile);
    }
  }
  if (uploadOk && uploadFile.write(data, length) != length) {
    uploadOk = false;
  }
  if (index + length == total && uploadFile) {
    uploadFile.close();
  }
}

void handleUploadDone(AsyncWebServerRequest *request) {
  request->send(uploadOk ? 200 : 409, "text/plain", uploadOk ? "Saved" : "Not saved");
}

void handleAudioDelete(AsyncWebServerRequest *request) {
  if (clipsEditable()) {
    SD.remove(String(AUDIO_DIR) + "/" + clipFileName(request->arg("name")));
  }
  request->redirect("/audio");
}

void startWifi() {
  WiFi.mode(WIFI_AP);
  wifiOn = WiFi.softAP(wifiSsid.c_str(), wifiPassword.c_str());
  wifiStartedAt = millis();
  Serial.printf("WiFi %s: %s at %s\n", wifiOn ? "started" : "FAILED", wifiSsid.c_str(),
                WiFi.softAPIP().toString().c_str());
}

void stopWifi() {
  WiFi.softAPdisconnect(true);
  WiFi.mode(WIFI_OFF);
  wifiOn = false;
  Serial.println("WiFi off: window ended with no clients");
}

// True for a password in the current format: two lowercase letters then six digits.
bool quickPassword(const String &pw) {
  if (pw.length() != 8) {
    return false;
  }
  for (int i = 0; i < 8; i++) {
    if (i < 2 ? !islower(pw[i]) : !isdigit(pw[i])) {
      return false;
    }
  }
  return true;
}

void setupWifi() {
  uint64_t mac = ESP.getEfuseMac();
  char suffix[5];
  snprintf(suffix, sizeof(suffix), "%04X", static_cast<uint16_t>(mac >> 32));
  wifiSsid = String("FOX-") + suffix;
  // Each fox makes its own random password on first boot and keeps it; the OLED shows it and
  // a QR code for it. (A password built from the SSID would let anyone who has read this code
  // join any fox.) Two lowercase letters then six digits: quick to type on a phone, and still
  // about 500 million combinations, plenty for a network that is up only a few minutes at a time.
  preferences.begin("fox", false);
  wifiPassword = preferences.getString("wifiPw", "");
  if (!quickPassword(wifiPassword)) {
    const char *letters = "abcdefghjkmnpqrstuvwxyz";  // no i, l or o to misread as 1 or 0
    wifiPassword = "";
    for (int i = 0; i < 8; i++) {
      wifiPassword += i < 2 ? letters[esp_random() % strlen(letters)] : char('0' + esp_random() % 10);
    }
    preferences.putString("wifiPw", wifiPassword);
  }
  preferences.end();

  server.on("/", HTTP_GET, handleRoot);
  server.on("/settings", HTTP_GET, handleSettings);
  server.on("/save", HTTP_POST, handleSave);
  server.on("/start", HTTP_POST, handleStart);
  server.on("/stop", HTTP_POST, handleStop);
  server.on("/test", HTTP_POST, handleTest);
  server.on("/audio", HTTP_GET, handleAudioPage);
  server.on("/audio/delete", HTTP_POST, handleAudioDelete);
  server.on("/upload", HTTP_POST, handleUploadDone, nullptr, handleUploadBody);
  server.on("/sdformat", HTTP_GET, handleSdFormatPage);
  server.on("/sdformat", HTTP_POST, handleSdFormat);
  server.onNotFound(handleNotFound);
  startWifi();
  server.begin();
}

void setupPmu() {
  pmuReady = pmu.begin(Wire1, PMU_ADDRESS, PIN_PMU_SDA, PIN_PMU_SCL);
  if (!pmuReady) {
    Serial.println("AXP2101 not found");
    return;
  }
  // ALDO1 feeds the OLED and ALDO3 feeds the radio. Both must be up before
  // those parts are touched.
  pmu.setALDO1Voltage(3300);
  pmu.enableALDO1();
  pmu.setALDO3Voltage(3300);
  pmu.enableALDO3();
  // BLDO1 feeds the SD card.
  pmu.setBLDO1Voltage(3300);
  pmu.enableBLDO1();
  // GPS is unused in this build.
  pmu.disableALDO4();

  pmu.disableTSPinMeasure();
  pmu.enableBattDetection();
  pmu.enableBattVoltageMeasure();
  pmu.enableVbusVoltageMeasure();
  pmu.enableSystemVoltageMeasure();
  // Standard 18650 charge settings, matching LilyGO's examples for current.
  pmu.setChargeTargetVoltage(XPOWERS_AXP2101_CHG_VOL_4V2);
  pmu.setChargerConstantCurr(XPOWERS_AXP2101_CHG_CUR_500MA);
  // The Samsung 35E has no protection circuit and is rated down to 2.65 V.
  pmu.setSysPowerDownVoltage(2900);
  delay(50);
}

void setupDisplay() {
  Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
  Wire.setTimeOut(20);
  for (uint8_t address : {0x3C, 0x3D}) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Wire.setClock(400000);
      u8g2_Setup_sh1106_i2c_128x64_noname_f(display.getU8g2(), U8G2_R0, oledByteCallback, oledDelayCallback);
      display.setI2CAddress(address << 1);
      displayReady = display.begin();
      Serial.printf("OLED at 0x%02X\n", address);
      return;
    }
  }
  Serial.println("OLED not found");
}

// Boot screen: callsign on one line, the project name in bold below it.
void drawSplash() {
  if (!displayReady) {
    return;
  }
  display.clearBuffer();
  display.drawRFrame(0, 0, 128, 64, 5);
  display.drawRFrame(2, 2, 124, 60, 4);
  display.setFont(u8g2_font_9x15B_tr);
  display.drawStr((128 - display.getStrWidth("KC9MNE")) / 2, 22, "KC9MNE");
  display.setFont(u8g2_font_helvB18_tr);
  display.drawStr((128 - display.getStrWidth("Wi-F0X")) / 2, 52, "Wi-F0X");
  display.sendBuffer();
}

// With format set, the library formats the card only if it has no valid FAT
// volume, so a readable card is never erased here.
void mountSd(bool format) {
  sdReadable = SD.begin(PIN_SD_CS, sdSpi, 4000000U, "/sd", 5, format);
  if (!sdReadable) {
    snprintf(sdInfo, sizeof(sdInfo), "not readable (no card, or not FAT)");
    Serial.printf("SD: %s\n", sdInfo);
    return;
  }
  if (!SD.exists(AUDIO_DIR)) {
    SD.mkdir(AUDIO_DIR);
  }
  const char *cardTypes[] = {"none", "MMC", "SD", "SDHC", "unknown"};
  uint8_t cardType = min<uint8_t>(SD.cardType(), 4);
  snprintf(sdInfo, sizeof(sdInfo), "%s, %lu MB", cardTypes[cardType],
           static_cast<uint32_t>(SD.cardSize() / (1024 * 1024)));
  Serial.printf("SD: %s\n", sdInfo);
}

void setupSd() {
  pinMode(PIN_IMU_CS, OUTPUT);
  digitalWrite(PIN_IMU_CS, HIGH);
  sdSpi.begin(PIN_SD_SCK, PIN_SD_MISO, PIN_SD_MOSI, PIN_SD_CS);
  mountSd(false);
}

void setupRadio() {
  Settings current = currentSettings();
  radioSpi.begin(PIN_RADIO_SCK, PIN_RADIO_MISO, PIN_RADIO_MOSI, PIN_RADIO_CS);
  // 4.8 kbps, 5 kHz deviation, 125 kHz RX bandwidth, 16 bit preamble.
  radioState = radio.beginFSK(current.frequency, 4.8, 5.0, 125.0, current.power, 16, false);
  if (radioState == RADIOLIB_ERR_NONE) {
    radioState = audio.begin();
  }
  if (radioState == RADIOLIB_ERR_NONE) {
    radioState = morse.begin(current.pitch, current.wpm);
  }
  if (radioState == RADIOLIB_ERR_NONE) {
    radioState = radio.standby();
  }
  Serial.printf("SX1278 state %d\n", radioState);
}

void drawWifiQr();

void drawDisplay() {
  if (!displayReady) {
    return;
  }
  static uint32_t tick = 0;
  tick++;
  // While Wi-Fi is up and no phone has joined yet, alternate 4 s of status with 4 s of QR.
  if (wifiOn && WiFi.softAPgetStationNum() == 0 && tick % 8 >= 4) {
    drawWifiQr();
    return;
  }
  Settings current = currentSettings();
  String idLine = "ID:" + current.foxId + " " + current.callsign;
  String radioLine = String(current.frequency, 3) + " MHz " + String(current.power) + "dBm";
  String wifiLine = wifiOn && tick % 6 >= 3 ? "PW " + wifiPassword : wifiText();

  String stateLine = beaconText();
  snprintf(lastDrawnState, sizeof(lastDrawnState), "%s", stateLine.c_str());

  display.clearBuffer();
  display.setFont(u8g2_font_9x15B_tr);
  display.drawStr(0, 12, idLine.c_str());
  display.drawStr(0, 40, stateLine.c_str());
  display.setFont(u8g2_font_6x12_tr);
  display.drawStr(0, 24, radioLine.c_str());
  display.drawStr(0, 52, batteryText(latestBattery).c_str());
  display.drawStr(0, 63, wifiLine.c_str());
  display.sendBuffer();
}

// Wi-Fi join screen: a QR code a phone camera can scan to join the fox's network, with the
// network name and password beside it for typing in by hand. Version 3 (29 x 29 modules) at
// 2 pixels a module is 58 pixels, drawn dark on a lit square so phones read it like print.
void drawWifiQr() {
  String join = "WIFI:T:WPA;S:" + wifiSsid + ";P:" + wifiPassword + ";;";
  QRCode qr;
  uint8_t data[qrcode_getBufferSize(3)];
  qrcode_initText(&qr, data, 3, ECC_LOW, join.c_str());
  display.clearBuffer();
  display.setDrawColor(1);
  display.drawBox(0, 0, 64, 64);
  display.setDrawColor(0);
  int origin = (64 - qr.size * 2) / 2;
  for (uint8_t y = 0; y < qr.size; y++) {
    for (uint8_t x = 0; x < qr.size; x++) {
      if (qrcode_getModule(&qr, x, y)) {
        display.drawBox(origin + x * 2, origin + y * 2, 2, 2);
      }
    }
  }
  display.setDrawColor(1);
  display.setFont(u8g2_font_6x12_tr);
  display.drawStr(68, 10, "Scan to");
  display.drawStr(68, 21, "join:");
  display.drawStr(68, 36, wifiSsid.c_str());
  display.setFont(u8g2_font_7x13B_tr);
  display.drawStr(68, 50, wifiPassword.c_str());
  display.setFont(u8g2_font_5x8_tr);
  display.drawStr(68, 62, "192.168.4.1");
  display.sendBuffer();
}

void logStatus() {
  Settings current = currentSettings();
  Battery battery = latestBattery;
  Serial.printf("ID:%s %s | %.3f MHz %d dBm | %s | %s | %s\n", current.foxId.c_str(), current.callsign.c_str(),
                current.frequency, current.power, beaconText().c_str(), batteryText(battery).c_str(),
                wifiText().c_str());
}

void setup() {
  Serial.setTxTimeoutMs(0);
  Serial.begin(115200);
  xTaskCreatePinnedToCore(serialCommandTask, "serialCommand", 4096, nullptr, 1, nullptr, 0);
  delay(1500);
  Serial.println("KC9MNE Wi-F0X starting");
  Serial.printf("Last reset reason %d\n", static_cast<int>(esp_reset_reason()));
  pinMode(PIN_BOOT_BUTTON, INPUT_PULLUP);
  settingsLock = xSemaphoreCreateMutex();
  loadSettings();
  setupPmu();
  setupDisplay();
  drawSplash();
  uint32_t splashShown = millis();
  setupSd();
  setupRadio();
  setupWifi();
  xTaskCreatePinnedToCore(clipReaderTask, "clipReader", 4096, nullptr, 5, &clipReaderHandle, 0);
  xTaskCreatePinnedToCore(beaconTask, "beacon", 8192, nullptr, 1, nullptr, 1);
  if (currentSettings().autoStart) {
    startRequested = true;
  }
  setCpuFrequencyMhz(IDLE_CPU_MHZ);
  Serial.printf("CPU %lu MHz\n", getCpuFrequencyMhz());
  // Keep the boot screen up for 3 s in all, counting the time setup already took.
  while (millis() - splashShown < SPLASH_MS) {
    delay(10);
  }
}

void loop() {
  static uint32_t lastDisplay = 0;
  static uint32_t lastSerial = 0;
  uint32_t now = millis();

  if (!wifiOn && digitalRead(PIN_BOOT_BUTTON) == LOW) {
    startWifi();
  }
  if (sdFormatRequested) {
    sdFormatRequested = false;
    Serial.println("Formatting SD card");
    SD.end();
    mountSd(true);
  }
  if (wifiOn && wifiSecondsLeft() == 0 && WiFi.softAPgetStationNum() == 0) {
    stopWifi();
  }
  uint32_t refreshRequest = displayRefreshRequested;
  if (refreshRequest != displayRefreshDone || now - lastDisplay >= DISPLAY_INTERVAL_MS) {
    lastDisplay = now;
    latestBattery = readBattery();
    drawDisplay();
    displayRefreshDone = refreshRequest;
  }
  if (now - lastSerial >= SERIAL_INTERVAL_MS) {
    lastSerial = now;
    logStatus();
  }
  delay(5);
}
