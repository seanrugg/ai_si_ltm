// LTMS member sketch for Arduino App Lab boards (#299).
//
// The MCU owns safety and the board's indicators:
//   - Every second it notifies Python: mcu_status(uptime_s, loop_max_us, link_lost, reset_cause).
//     reset_cause is -1 (unknown): reading it needs Zephyr's hwinfo driver, which the board
//     variants checked so far don't enable (see README).
//   - Python calls link(state) about once a second. If it stops for 5 s, the sketch shows a
//     fixed "disconnected" state by itself, whatever Python asked for before.
//
// Indicators: generic by default (LED_BUILTIN, if the board has one), and one block per
// board that has more. Link states (keep in step with python/app_member.py):
//   0 starting, 1 waiting (settings, enrollment or approval), 2 online, 3 no hub.
//
//              | generic LED_BUILTIN | VENTUNO Q (RGB LED 2, active-low; LED matrix)
//   starting   | slow blink          | cyan
//   waiting    | slow blink          | yellow
//   online     | on                  | green
//   no hub     | off                 | red (records wait in the outbox)
//   DISCONNECTED (Python silent 5 s) | fast blink          | red fast blink + X on the matrix
//
// On the VENTUNO Q, RGB LED 1 (red is Zephyr's error LED) and LED 4 (red is the CAN state
// LED) are left alone. Handlers use provide_safe, so they run between loop() passes.

#include <Arduino_RouterBridge.h>

#if defined(ARDUINO_VENTUNO_Q)
#include <Arduino_LED_Matrix.h>
#define HAS_RGB_STATUS 1
#define HAS_MATRIX 1
Arduino_LED_Matrix matrix;
#endif

enum { LINK_STARTING = 0, LINK_PENDING = 1, LINK_ONLINE = 2, LINK_OFFLINE = 3 };
const unsigned long LINK_TIMEOUT_MS = 5000;

volatile int linkState = LINK_STARTING;
unsigned long lastLinkMs = 0;
bool everLinked = false;
bool disconnected = false;
uint32_t linkLost = 0;
unsigned long lastStatusMs = 0;
uint32_t loopMaxUs = 0;
bool matrixReady = false;
int shownIcon = -1;

#if defined(HAS_RGB_STATUS)
// bit 0 red, bit 1 green, bit 2 blue
enum { OFF = 0, RED = 1, GREEN = 2, YELLOW = 3, BLUE = 4, CYAN = 6 };
void setStatusRgb(int bits) {
  digitalWrite(LED2_R, (bits & 1) ? LOW : HIGH);  // active-low
  digitalWrite(LED2_G, (bits & 2) ? LOW : HIGH);
  digitalWrite(LED2_B, (bits & 4) ? LOW : HIGH);
}
#endif

#if defined(HAS_MATRIX)
const char *X_ICON[8] = {"  #       #  ", "   #     #   ", "    #   #    ", "     # #     ",
                         "      #      ", "     # #     ", "    #   #    ", "   #     #   "};
void showIcon(int which) {  // 0 blank, 1 X
  if (!matrixReady || which == shownIcon) return;
  uint8_t frame[8][13];
  for (int y = 0; y < 8; y++)
    for (int x = 0; x < 13; x++)
      frame[y][x] = (which == 1 && X_ICON[y][x] == '#') ? 1 : 0;
  matrix.renderBitmap(frame, 8, 13);
  shownIcon = which;
}
#endif

bool onLink(int state) {
  if (state < LINK_STARTING || state > LINK_OFFLINE) return false;
  linkState = state;
  lastLinkMs = millis();
  everLinked = true;
#if defined(HAS_MATRIX)
  if (!matrixReady) {  // first call from Python: Linux is up, so the matrix is safe to use
    matrix.begin();
    matrixReady = true;
  }
#endif
  return true;
}

void setup() {
#if defined(HAS_RGB_STATUS)
  pinMode(LED2_R, OUTPUT); pinMode(LED2_G, OUTPUT); pinMode(LED2_B, OUTPUT);
  setStatusRgb(CYAN);
#elif defined(LED_BUILTIN)
  pinMode(LED_BUILTIN, OUTPUT);
#endif
  Bridge.begin();
  Bridge.provide_safe("link", onLink);
}

void loop() {
  unsigned long startUs = micros();
  unsigned long now = millis();

  bool silent = everLinked && (now - lastLinkMs > LINK_TIMEOUT_MS);
  if (silent && !disconnected) linkLost++;
  disconnected = silent;

  bool fast = (now / 125) % 2;   // 4 Hz
  bool slow = (now / 500) % 2;   // 1 Hz

#if defined(HAS_RGB_STATUS)
  int color;
  if (disconnected) color = fast ? RED : OFF;
  else switch (linkState) {
    case LINK_PENDING: color = YELLOW; break;
    case LINK_ONLINE:  color = GREEN; break;
    case LINK_OFFLINE: color = RED; break;
    default:           color = CYAN; break;
  }
  setStatusRgb(color);
#elif defined(LED_BUILTIN)
  bool on;
  if (disconnected) on = fast;
  else switch (linkState) {
    case LINK_ONLINE:  on = true; break;
    case LINK_OFFLINE: on = false; break;
    default:           on = slow; break;
  }
  digitalWrite(LED_BUILTIN, on ? HIGH : LOW);
#endif
#if defined(HAS_MATRIX)
  showIcon(disconnected ? 1 : 0);
#endif

  if (now - lastStatusMs >= 1000) {
    lastStatusMs = now;
    Bridge.notify("mcu_status", (int)(now / 1000), (int)loopMaxUs, (int)linkLost, -1);
    loopMaxUs = 0;
  }
  uint32_t took = micros() - startUs;
  if (took > loopMaxUs) loopMaxUs = took;
  delay(10);
}
