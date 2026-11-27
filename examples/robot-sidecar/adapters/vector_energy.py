"""Vector's energy reflex (#513, part of #506): go home after too long off the dock.

Vector's firmware doesn't take him back to his charger when his battery runs low,
and he gives almost no warning: in every long off-dock episode on record (23–25
Nov 2026) he reached battery level 1 after about 75–115 minutes off the dock and
powered off within minutes of it. So the trigger is **time off the dock**
(``home_after_s``, default 45 minutes), with level 1 as a backstop.

This module is the decision part only: a state machine fed the battery poll
(which stays live when the SDK's event stream freezes, #498). It never touches
the robot. It returns a command for the adapter to run, and is told how that
went. That keeps the sidecar's one command small, testable and on the record.

**One off-dock episode:**

- leaving the dock starts it; returning to the dock ends it;
- an ``event`` tagged ``body,energy`` is signed whenever the state or the step
  changes (never on a timer), with ``payload`` ``{state, step, off_dock_s,
  battery_level, frame}``; every one after the first refs the first
  (``refs.start``);
- states: ``ok`` → ``low-soon`` (off the dock ≥ ``home_after_s``) or ``low``
  (battery level 1, whatever the clock says);
- the ladder, on ``low-soon`` or ``low``, one rung per poll:
  1. ``go_home``: the adapter takes behavior control, calls ``drive_on_charger``
     and releases control;
  2. if the next poll still has him off the dock, ``go_home`` **once** more;
  3. if that fails too, ``ask_help``: Vector says so, and the adapter signs a
     swarm question (``communication`` tagged ``question,energy,help``, no
     ``peers``, so the whole swarm gets it, as ``ltms_mind/agenda.py`` reads it);
  4. then nothing more: no third drive, no second help request;
- no command while he is held or falling (``blocked``), and none while a command
  is still running;
- the end signs an ``event`` tagged ``body,energy,energy-outcome``, refs the
  first energy event, ``payload.outcome``:
  - ``powered-off``: the stream went stale while he was off the dock, before he
    was docked (on 25 Nov a dead battery looked exactly like this, #498);
  - ``rescued``: docked after a help request;
  - ``docked``: docked after a ``go_home``;
  - ``not-needed``: docked before the reflex did anything.

``home_after_s <= 0`` turns the reflex off: no commands and no energy records.
"""
from __future__ import annotations

import threading

from adapters.base import Emit, Item

HOME_AFTER_SECONDS = 45 * 60
HELP_TEXT = "I need my charger."
GO_HOME, ASK_HELP = "go_home", "ask_help"


class EnergyReflex:
    def __init__(self, home_after_s: float = HOME_AFTER_SECONDS):
        self.home_after_s = home_after_s
        self._lock = threading.Lock()  # polls run on the SDK thread, commands on their own
        self._n = 0
        self._reset()

    @property
    def enabled(self) -> bool:
        return self.home_after_s > 0

    def _reset(self) -> None:
        self.off_since: float | None = None  # monotonic time he left the dock; None while docked
        self.state: str | None = None
        self.step: str | None = None
        self.attempts = 0       # go_home commands sent this episode
        self.helped = False     # help requested this episode
        self.busy = False       # a command is running
        self.stale = False      # the stream went stale during this episode
        self.start_key: str | None = None
        self.off_s = 0.0        # seconds off the dock at the last poll
        self._started = False   # the episode's first energy event is signed

    # -- the battery poll ------------------------------------------------------
    def poll(self, emit: Emit, on_charger: bool, level: int | None, blocked: bool,
             frame: str | None, now: float) -> str | None:
        """One battery poll. Returns the command to run now (``go_home``, ``ask_help``) or None."""
        if not self.enabled:
            return None
        with self._lock:
            if on_charger:
                if self.off_since is not None:
                    self._end(emit, level, frame, now)
                return None
            if self.off_since is None:
                self._n += 1
                self.off_since = now
                self.start_key = f"energy:{self._n}:start"
            off_s = self.off_s = now - self.off_since
            state = "low" if level == 1 else "low-soon" if off_s >= self.home_after_s else "ok"
            command = None
            if state != "ok" and not blocked and not self.busy:
                if self.attempts < 2:
                    command = GO_HOME
                    self.attempts += 1
                    self.step = "go-home" if self.attempts == 1 else "retry"
                elif not self.helped:
                    command = ASK_HELP
                    self.helped = True
                    self.step = "help"
                self.busy = command is not None
            if state != self.state or command is not None:
                self.state = state
                self._event(emit, level, frame, off_s)
            return command

    def done(self, emit: Emit, command: str, ok: bool, detail: str | None = None) -> None:
        """The adapter finished a command. A failed ``go_home`` still counts as an attempt."""
        with self._lock:
            self.busy = False
            if command == ASK_HELP and self.off_since is not None:
                emit(Item("record", "communication", HELP_TEXT, ["question", "energy", "help"],
                          {"source": "vector-sdk", "reason": self.state,
                           "off_dock_s": round(self.off_s, 1), "spoken": ok},
                          refs={"start": self.start_key}))
            emit(Item("record", "action", f"energy: {command.replace('_', ' ')}"[:280],
                      ["body", "energy", "command"],
                      {"source": "vector-sdk", "command": command, "ok": ok, "detail": detail},
                      refs={"start": self.start_key} if self.start_key else None,
                      outcome="success" if ok else "failed"))

    def stream_stale(self) -> None:
        """The SDK stream froze. Off the dock, that may mean he powered off."""
        with self._lock:
            if self.off_since is not None:
                self.stale = True

    # -- records ----------------------------------------------------------------
    def _event(self, emit: Emit, level, frame, off_s: float) -> None:
        payload = {"source": "vector-sdk", "state": self.state, "step": self.step,
                   "off_dock_s": round(off_s, 1), "battery_level": level, "frame": frame}
        first = not self._started
        emit(Item("record", "event", f"energy: {self.state}" + (f", {self.step}" if self.step else ""),
                  ["body", "energy"], payload,
                  key=self.start_key if first else None,
                  refs=None if first else {"start": self.start_key}))
        self._started = True

    def _end(self, emit: Emit, level, frame, now: float) -> None:
        off_s = now - self.off_since
        if self.stale:
            outcome = "powered-off"
        elif self.helped:
            outcome = "rescued"
        elif self.attempts:
            outcome = "docked"
        else:
            outcome = "not-needed"
        emit(Item("record", "event", f"energy outcome: {outcome}", ["body", "energy", "energy-outcome"],
                  {"source": "vector-sdk", "outcome": outcome, "off_dock_s": round(off_s, 1),
                   "go_home_attempts": self.attempts, "help_requested": self.helped,
                   "battery_level": level, "frame": frame},
                  refs={"start": self.start_key}))
        self._reset()
