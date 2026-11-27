"""Vector's energy reflex (#513): go home after too long off the dock, then ask for help."""
from __future__ import annotations

from types import SimpleNamespace as NS

from adapters.base import Item
from adapters.vector_energy import ASK_HELP, GO_HOME, HELP_TEXT, EnergyReflex
from adapters.vector_sdk import VectorSDK, run_command
from sidecar import Sidecar

MIN = 60.0


def records(got, tag=None):
    return [g for g in got if g.kind == "record" and (tag is None or tag in g.tags)]


def energy_events(got):
    return [g for g in records(got, "energy") if g.type == "event" and "energy-outcome" not in g.tags]


def poll(r, got, t, on=False, level=2, blocked=False, frame="vector-odom:1"):
    return r.poll(got.append, on, level, blocked, frame, t)


# -- the decision part -------------------------------------------------------------

def test_off_the_dock_45_minutes_sends_him_home_and_docking_closes_the_episode():
    r, got = EnergyReflex(45 * MIN), []
    assert poll(r, got, 0.0, on=True) is None          # docked: nothing
    assert poll(r, got, 10.0) is None                  # left the dock
    assert poll(r, got, 10.0 + 44 * MIN) is None       # not yet
    assert poll(r, got, 10.0 + 45 * MIN) == GO_HOME
    r.done(got.append, GO_HOME, True, "BEHAVIOR_COMPLETE_STATE")
    assert poll(r, got, 10.0 + 46 * MIN, on=True) is None  # docked by the reflex

    ev = energy_events(got)
    assert [e.payload["state"] for e in ev] == ["ok", "low-soon"]
    assert ev[0].key == "energy:1:start" and ev[0].refs is None
    assert ev[1].refs == {"start": "energy:1:start"} and ev[1].payload["step"] == "go-home"
    assert ev[1].payload["frame"] == "vector-odom:1" and ev[1].payload["battery_level"] == 2
    (cmd,) = [g for g in got if g.type == "action"]
    assert cmd.outcome == "success" and cmd.payload["command"] == GO_HOME
    assert cmd.tags == ["body", "energy", "command"]
    (out,) = records(got, "energy-outcome")
    assert out.payload["outcome"] == "docked" and out.payload["go_home_attempts"] == 1
    assert out.refs == {"start": "energy:1:start"}


def test_one_retry_then_a_help_request_then_nothing_more():
    r, got = EnergyReflex(45 * MIN), []
    poll(r, got, 0.0)
    t = 45 * MIN
    assert poll(r, got, t) == GO_HOME
    assert poll(r, got, t + 30) is None                # still running: no second command
    r.done(got.append, GO_HOME, True)
    assert poll(r, got, t + 60) == GO_HOME             # still off the dock: the one retry
    r.done(got.append, GO_HOME, False, "VectorControlTimeoutException")
    assert poll(r, got, t + 90) == ASK_HELP
    r.done(got.append, ASK_HELP, True)
    for i in range(10):                                # no third drive, no second request
        assert poll(r, got, t + 120 + 30 * i) is None

    (q,) = [g for g in records(got, "help")]
    assert q.type == "communication" and q.summary == HELP_TEXT
    assert q.tags == ["question", "energy", "help"] and q.peers is None   # the whole swarm
    assert q.payload["reason"] == "low-soon" and q.payload["spoken"] is True
    assert [e.payload["step"] for e in energy_events(got)][1:] == ["go-home", "retry", "help"]
    assert [a.outcome for a in got if a.type == "action"] == ["success", "failed", "success"]

    poll(r, got, t + 600, on=True)                     # a person docked him
    assert records(got, "energy-outcome")[-1].payload["outcome"] == "rescued"


def test_level_1_starts_the_ladder_before_the_clock():
    r, got = EnergyReflex(45 * MIN), []
    poll(r, got, 0.0)
    assert poll(r, got, 10 * MIN, level=1) == GO_HOME
    assert energy_events(got)[-1].payload["state"] == "low"


def test_no_command_while_held_or_falling():
    r, got = EnergyReflex(45 * MIN), []
    poll(r, got, 0.0)
    assert poll(r, got, 50 * MIN, blocked=True) is None
    assert poll(r, got, 51 * MIN, blocked=True) is None
    assert poll(r, got, 52 * MIN) == GO_HOME           # put down: now


def test_back_on_the_dock_early_needs_nothing():
    r, got = EnergyReflex(45 * MIN), []
    poll(r, got, 0.0)
    poll(r, got, 20 * MIN, on=True)
    assert not [g for g in got if g.type in ("action", "communication")]
    assert records(got, "energy-outcome")[0].payload["outcome"] == "not-needed"


def test_a_stale_stream_off_the_dock_ends_as_powered_off():
    r, got = EnergyReflex(45 * MIN), []
    poll(r, got, 0.0)
    r.stream_stale()                                   # 25 Nov: a dead battery looks like this
    poll(r, got, 90 * MIN, on=True)
    assert records(got, "energy-outcome")[0].payload["outcome"] == "powered-off"


def test_each_episode_starts_fresh():
    r, got = EnergyReflex(45 * MIN), []
    poll(r, got, 0.0)
    assert poll(r, got, 45 * MIN) == GO_HOME
    r.done(got.append, GO_HOME, True)
    poll(r, got, 46 * MIN, on=True)
    poll(r, got, 60 * MIN)                             # off again
    assert poll(r, got, 60 * MIN + 45 * MIN) == GO_HOME
    assert energy_events(got)[-1].refs == {"start": "energy:2:start"}


def test_zero_turns_it_off():
    r, got = EnergyReflex(0), []
    for t in (0.0, 60 * MIN, 120 * MIN):
        assert poll(r, got, t, level=1) is None
    poll(r, got, 130 * MIN, on=True)
    assert got == []


# -- the adapter: commands under behavior control ------------------------------------

class FakeRobot:
    def __init__(self, fail_control=False):
        self.calls = []
        self.fail_control = fail_control
        self.conn = NS(request_control=self.request, release_control=self.release)
        self.behavior = NS(drive_on_charger=self.drive, say_text=self.say)

    def request(self, timeout=10.0):
        self.calls.append("request_control")
        if self.fail_control:
            raise TimeoutError("Surpassed timeout of 10.0s")

    def release(self, timeout=10.0):
        self.calls.append("release_control")

    def drive(self):
        self.calls.append("drive_on_charger")
        return NS(result="BEHAVIOR_COMPLETE_STATE")

    def say(self, text):
        self.calls.append(f"say:{text}")
        return NS(result=None)


def test_run_command_takes_control_and_always_gives_it_back():
    r, got, robot = EnergyReflex(45 * MIN), [], FakeRobot()
    poll(r, got, 0.0)
    assert poll(r, got, 45 * MIN) == GO_HOME
    run_command(r, got.append, robot, GO_HOME)
    assert robot.calls == ["request_control", "drive_on_charger", "release_control"]
    action = [g for g in got if g.type == "action"][-1]
    assert action.outcome == "success" and action.payload["detail"] == "BEHAVIOR_COMPLETE_STATE"
    assert r.busy is False


def test_run_command_without_control_is_a_failed_attempt():
    r, got, robot = EnergyReflex(45 * MIN), [], FakeRobot(fail_control=True)
    poll(r, got, 0.0)
    poll(r, got, 45 * MIN)
    run_command(r, got.append, robot, GO_HOME)
    assert robot.calls == ["request_control"]          # nothing ran without control
    action = [g for g in got if g.type == "action"][-1]
    assert action.outcome == "failed" and "TimeoutError" in action.payload["detail"]
    assert poll(r, got, 45 * MIN + 30) == GO_HOME      # the retry still comes


def test_ask_help_speaks():
    r, got, robot = EnergyReflex(45 * MIN), [], FakeRobot()
    run_command(r, got.append, robot, ASK_HELP)
    assert robot.calls == ["request_control", f"say:{HELP_TEXT}", "release_control"]


def battery(level=2, on=False):
    return NS(battery_volts=3.9, battery_level=level, is_charging=on, is_on_charger_platform=on)


def test_adapter_runs_the_reflex_from_the_battery_poll():
    v, got, robot = VectorSDK(home_after_s=45 * MIN), [], FakeRobot()
    v.on_battery(got.append, battery(on=True), now=0.0)
    assert v.on_energy(got.append, robot, now=0.0) is None
    v.on_battery(got.append, battery(), now=10.0)
    v.on_energy(got.append, robot, now=10.0)
    v.on_battery(got.append, battery(), now=10.0 + 45 * MIN)
    assert v.on_energy(got.append, robot, now=10.0 + 45 * MIN) == GO_HOME
    v._command.join(5)
    assert robot.calls == ["request_control", "drive_on_charger", "release_control"]


def test_adapter_holds_off_while_vector_is_held():
    v, got, robot = VectorSDK(home_after_s=45 * MIN), [], FakeRobot()
    v.on_battery(got.append, battery(), now=0.0)
    v.on_energy(got.append, robot, now=0.0)
    v.on_flags(got.append, {"is_picked_up": True}, None, 100.0)  # already held at the first reading
    assert v.episodes.on["held"] is True
    v.on_battery(got.append, battery(), now=50 * MIN)
    assert v.on_energy(got.append, robot, now=50 * MIN) is None
    assert robot.calls == []
    v.on_flags(got.append, {"is_picked_up": False}, None, 200.0)
    v.on_flags(got.append, {"is_picked_up": False}, None, 201.0)   # put down, past the debounce
    v.on_battery(got.append, battery(), now=51 * MIN)
    assert v.on_energy(got.append, robot, now=51 * MIN) == GO_HOME
    v._command.join(5)


def test_adapter_needs_a_battery_poll_first():
    v, got = VectorSDK(home_after_s=45 * MIN), []
    assert v.on_energy(got.append, FakeRobot(), now=99 * MIN) is None
    assert got == []


def test_a_stale_stream_marks_the_episode():
    v, got = VectorSDK(home_after_s=45 * MIN), []
    v.on_battery(got.append, battery(), now=0.0)
    v.on_energy(got.append, FakeRobot(), now=0.0)
    v.mark_stale(got.append, 10.0)
    assert v.energy.stale is True


def test_home_after_minutes_from_env():
    assert VectorSDK.from_env({"VECTOR_HOME_AFTER_MINUTES": "2"}).energy.home_after_s == 120.0
    assert VectorSDK.from_env({}).energy.home_after_s == 45 * MIN
    assert VectorSDK.from_env({"VECTOR_HOME_AFTER_MINUTES": "0"}).energy.enabled is False


def test_the_sidecar_signs_an_action_with_its_outcome():
    from test_robot_sidecar import FakeLink
    sc, link = Sidecar([], body_seconds=30, now=0), FakeLink()
    sc.emit(Item("record", "action", "energy: go home", ["body", "energy", "command"],
                 {"command": GO_HOME}, outcome="failed"), at=1)
    sc.poll(link, 1)
    (rec,) = link.records
    assert rec["type"] == "action" and rec["outcome"] == "failed"
