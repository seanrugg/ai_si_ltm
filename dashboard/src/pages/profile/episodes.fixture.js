// Shared test fixture for the chronograph tests (#403).
// A neutral one-hour fixture in the #401 episodes shape.
export const EPISODES = {
  since: "2026-11-14T12:00:00+00:00", until: "2026-11-14T13:00:00+00:00", duration_s: 3600, count: 9, truncated: false,
  pulses: [
    [100, 0.4, "perception", "self", "battery", "heartbeat"],
    [300, 0.5, "event", "act", "mic", "wake word"],
    [1000, 0.3, "action", "act", "hub", "turned toward the door"],
    [1790, 0.5, "perception", "self", "battery", "heartbeat"],
  ],
  lanes: {
    mind: [[600, 660, "thinking", "LLM request"]],
    voice: [[300, 303, "listening", "wake word"], [660, 672, "replying", "Hello there", { estimated: true }]],
    attention: [[400, 410, "known", "Ada"], [900, 910, "unknown", "face #3"]],
    body: [[1200, 1500, "held", "held"]],
    motion: [[1600, 1630, "nudging", "20 mm/s max"], [1700, 1730, "driving", "120 mm/s max"]],
    power: [[0, 2000, "battery", "on battery"], [2000, 3600, "charger", "on charger", { open: true }]],
    behavior: [],
  },
  series: { volts: [[100, 3.6], [1800, 3.9]], wheel_mmps: [[1630, 20], [1730, 120]] },
  heartbeat: { interval_s: 30, last_t: 1790 },
};
