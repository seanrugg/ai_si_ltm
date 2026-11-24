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
//
// The on-board agent (#492, off unless AGENT=on) may ask for two things, and the MCU decides:
//   agent_light(state)  0 off, 1 busy, 2 attention, 3 done. Shown on RGB LED 3 if the board
//                       defines LED3_R/G/B; refused (false) on boards without a spare LED.
//   agent_icon(icon)    0 blank, 1 check, 2 question, 3 heart, 4 dot, on the LED matrix;
//                       refused on boards without one.
// Both are refused while "disconnected" is showing (that state always wins), and for any
// value out of range. No actuation: lights and icons only.

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
int shownIcon = -2;  // nothing drawn yet (-1 is the X)
int agentIcon = 0;   // 0 blank
int agentLight = 0;  // 0 off

#if defined(HAS_RGB_STATUS)
// bit 0 red, bit 1 green, bit 2 blue
enum { OFF = 0, RED = 1, GREEN = 2, YELLOW = 3, BLUE = 4, CYAN = 6 };
void setStatusRgb(int bits) {
  digitalWrite(LED2_R, (bits & 1) ? LOW : HIGH);  // active-low
  digitalWrite(LED2_G, (bits & 2) ? LOW : HIGH);
  digitalWrite(LED2_B, (bits & 4) ? LOW : HIGH);
}
#endif

#if defined(LED3_R) && defined(LED3_G) && defined(LED3_B)
#define HAS_AGENT_LIGHT 1
#endif

#if defined(HAS_MATRIX)
const char *X_ICON[8] = {"  #       #  ", "   #     #   ", "    #   #    ", "     # #     ",
                         "      #      ", "     # #     ", "    #   #    ", "   #     #   "};
// Agent icons (index = agent_icon value; 0 is blank)
const char *AGENT_ICONS[5][8] = {
  {"             ", "             ", "             ", "             ",
   "             ", "             ", "             ", "             "},
  {"             ", "          #  ", "         #   ", "        #    ",
   "   #   #     ", "    # #      ", "     #       ", "             "},   // check
  {"     ###     ", "    #   #    ", "        #    ", "       #     ",
   "      #      ", "      #      ", "             ", "      #      "},   // question
  {"             ", "   ## ##     ", "  #######    ", "  #######    ",
   "   #####     ", "    ###      ", "     #       ", "             "},   // heart
  {"             ", "             ", "             ", "     ###     ",
   "     ###     ", "     ###     ", "             ", "             "},   // dot
};
const int AGENT_ICON_COUNT = 5;

// which: -1 = the X (disconnected), otherwise an agent icon index
void showIcon(int which) {
  if (!matrixReady || which == shownIcon) return;
  uint8_t frame[8][13];
  for (int y = 0; y < 8; y++)
    for (int x = 0; x < 13; x++) {
      char c = (which < 0) ? X_ICON[y][x] : AGENT_ICONS[which][y][x];
      frame[y][x] = (c == '#') ? 1 : 0;
    }
  matrix.renderBitmap(frame, 8, 13);
  shownIcon = which;
}
#endif

bool onAgentIcon(int icon) {
#if defined(HAS_MATRIX)
  if (disconnected || !matrixReady || icon < 0 || icon >= AGENT_ICON_COUNT) return false;
  agentIcon = icon;
  return true;
#else
  (void)icon;
  return false;  // no display on this board
#endif
}

bool onAgentLight(int state) {
#if defined(HAS_AGENT_LIGHT)
  if (disconnected || state < 0 || state > 3) return false;
  agentLight = state;
  return true;
#else
  (void)state;
  return false;  // no spare LED on this board
#endif
}

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
  Bridge.provide_safe("agent_light", onAgentLight);
  Bridge.provide_safe("agent_icon", onAgentIcon);
#if defined(HAS_AGENT_LIGHT)
  pinMode(LED3_R, OUTPUT); pinMode(LED3_G, OUTPUT); pinMode(LED3_B, OUTPUT);
#endif
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
  showIcon(disconnected ? -1 : agentIcon);
#endif
#if defined(HAS_AGENT_LIGHT)
  // Agent light (RGB LED 3, active-low like LED 2): busy blue blink, attention yellow, done green.
  int a = disconnected ? 0 : agentLight;
  bool r = a == 2, g = a == 2 || a == 3, b = a == 1 && slow;
  digitalWrite(LED3_R, r ? LOW : HIGH);
  digitalWrite(LED3_G, g ? LOW : HIGH);
  digitalWrite(LED3_B, b ? LOW : HIGH);
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
