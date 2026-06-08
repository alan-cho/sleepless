import sleepless as S


class FakeAdapter:
    def __init__(self):
        self.flag = False
        self.read_ok = True
        self.write_succeeds = True
        self.lpm = False
        self.thermal = 0
        self.percent = 80.0
        self.plugged = True
        self.notifications = []
        self.set_calls = []

    def set_disablesleep(self, value, *, allow_prompt):
        self.set_calls.append((value, allow_prompt))
        if self.write_succeeds:
            self.flag = bool(value)
        return self.write_succeeds

    def read_sleep_disabled(self):
        return (self.read_ok, self.flag)

    def read_low_power_mode(self):
        return self.lpm

    def read_thermal_state(self):
        return self.thermal

    def read_battery(self):
        return (self.percent, self.plugged)

    def notify(self, title, subtitle, message):
        self.notifications.append((title, subtitle, message))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def R(**kw):
    base = dict(sleep_disabled=False, sleep_read_ok=True, low_power_mode=False,
                thermal_state=0, battery_percent=80.0, power_plugged=True)
    base.update(kw)
    return S.Readings(**base)


def cfg(**kw):
    return S.Config(**kw).sanitized()


def make_controller(**adapter_kw):
    a = FakeAdapter()
    for k, v in adapter_kw.items():
        setattr(a, k, v)
    clk = Clock()
    return S.Controller(a, cfg(), now=clk, sync=True), a, clk


def test_parse_sleep_disabled_absent_is_off():
    assert S.parse_sleep_disabled("sleep 1\nhibernatemode 3\n") is False


def test_parse_sleep_disabled_on():
    assert S.parse_sleep_disabled(" SleepDisabled    1\n") is True


def test_parse_sleep_disabled_explicit_zero():
    assert S.parse_sleep_disabled(" SleepDisabled 0\n") is False


def test_parse_low_power_mode():
    assert S.parse_low_power_mode(" lowpowermode 1\n") is True
    assert S.parse_low_power_mode(" lowpowermode 0\n") is False
    assert S.parse_low_power_mode("nothing here") is False


def test_unsafe_none_when_safe_on_ac():
    assert S.unsafe_conditions(R(), cfg()) is None


def test_unsafe_hard_floor():
    assert S.unsafe_conditions(R(battery_percent=4, power_plugged=False), cfg()) == "hard floor"


def test_unsafe_battery_floor_on_battery():
    assert S.unsafe_conditions(
        R(battery_percent=15, power_plugged=False), cfg(battery_floor_percent=20)) == "battery floor"


def test_unsafe_floor_not_applied_when_charging():
    assert S.unsafe_conditions(
        R(battery_percent=15, power_plugged=True), cfg(battery_floor_percent=20)) is None


def test_unsafe_plugged_none_treated_as_battery():
    assert S.unsafe_conditions(
        R(battery_percent=15, power_plugged=None), cfg(battery_floor_percent=20)) == "battery floor"


def test_unsafe_no_battery_reading_on_battery():
    assert S.unsafe_conditions(
        R(battery_percent=None, power_plugged=False), cfg()) == "unsafe (no battery reading)"


def test_unsafe_no_battery_reading_on_ac_ok():
    assert S.unsafe_conditions(R(battery_percent=None, power_plugged=True), cfg()) is None


def test_unsafe_low_power_mode():
    assert S.unsafe_conditions(R(low_power_mode=True), cfg()) == "low power mode"


def test_unsafe_thermal_serious_auto():
    assert S.unsafe_conditions(R(thermal_state=2), cfg(thermal_guard="auto")) == "thermal"


def test_unsafe_thermal_serious_warn_not_reverted():
    assert S.unsafe_conditions(R(thermal_state=2), cfg(thermal_guard="warn")) is None


def test_unsafe_thermal_critical_overrides_off():
    assert S.unsafe_conditions(R(thermal_state=3), cfg(thermal_guard="off")) == "thermal (critical)"


def test_can_enable_hysteresis_blocks_until_nominal():
    st = S.State(awaiting_nominal=True)
    assert S.can_enable(R(thermal_state=1), cfg(), st) == "cooling down"
    assert S.can_enable(R(thermal_state=0), cfg(), st) is None


def test_decide_revert_none_when_disabled():
    assert S.decide_revert(S.State(enabled=False), R(), cfg(), 100.0) is None


def test_decide_revert_timer():
    st = S.State(enabled=True, awake_until=50.0)
    assert S.decide_revert(st, R(), cfg(), 60.0) == "timer"
    assert S.decide_revert(st, R(), cfg(), 40.0) is None


def test_decide_revert_guardrail_first():
    st = S.State(enabled=True, awake_until=None)
    assert S.decide_revert(st, R(low_power_mode=True), cfg(), 0.0) == "low power mode"


def test_resolve_default_timer():
    c = cfg(default_timer_plugged="indefinite", default_timer_unplugged="1h")
    assert S.resolve_default_timer(True, c) == "indefinite"
    assert S.resolve_default_timer(False, c) == "1h"
    assert S.resolve_default_timer(None, c) == "1h"


def test_compute_remaining():
    assert S.compute_remaining(None, 0.0) is None
    assert S.compute_remaining(100.0, 40.0) == 60
    assert S.compute_remaining(100.0, 200.0) == 0


def test_format_remaining():
    assert S.format_remaining(None) == "Indefinite"
    assert S.format_remaining(3660) == "1h01m"


def test_glyph_for():
    assert S.glyph_for(S.State(alarm=True), R()) == S.GLYPHS["alarm"]
    assert S.glyph_for(S.State(enabled=False), R()) == S.GLYPHS["off"]
    assert S.glyph_for(S.State(enabled=True), R(power_plugged=True)) == S.GLYPHS["ac"]
    assert S.glyph_for(S.State(enabled=True), R(power_plugged=False)) == S.GLYPHS["batt"]


def test_config_clamps():
    c = S.Config(battery_floor_percent=1, poll_seconds=999,
                 default_timer_plugged="x", thermal_guard="bogus").sanitized()
    assert c.battery_floor_percent == S.HARD_FLOOR
    assert c.poll_seconds == S.POLL_MAX
    assert c.default_timer_plugged == "indefinite"
    assert c.thermal_guard == "auto"


def test_config_roundtrip(tmp_path):
    p = tmp_path / "config.json"
    S.save_config(S.Config(battery_floor_percent=25, poll_seconds=30), p)
    loaded = S.load_config(p)
    assert loaded.battery_floor_percent == 25
    assert loaded.poll_seconds == 30


def test_config_corrupt_recovers(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("}{ not json")
    loaded = S.load_config(p)
    assert loaded.battery_floor_percent == 20
    assert (tmp_path / "config.json.bad").exists()


def test_config_missing_creates_default(tmp_path):
    p = tmp_path / "config.json"
    loaded = S.load_config(p)
    assert p.exists()
    assert loaded.poll_seconds == 60


def test_set_disablesleep_argv(monkeypatch):
    captured = {}

    class Ok:
        returncode = 0

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return Ok()

    monkeypatch.setattr(S.subprocess, "run", fake_run)
    assert S.SystemAdapter().set_disablesleep(1, allow_prompt=False) is True
    assert captured["argv"] == [S.SUDO, "-n", S.PMSET, "-a", "disablesleep", "1"]


def test_set_disablesleep_rejects_bad_value():
    assert S.SystemAdapter().set_disablesleep(2, allow_prompt=True) is False


def test_set_disablesleep_no_prompt_on_autorevert(monkeypatch):
    calls = []

    class Fail:
        returncode = 1

    def fake_run(argv, **kw):
        calls.append(argv)
        return Fail()

    monkeypatch.setattr(S.subprocess, "run", fake_run)
    assert S.SystemAdapter().set_disablesleep(0, allow_prompt=False) is False
    assert len(calls) == 1


def test_osascript_constants_literal():
    assert set(S._OSASCRIPT.keys()) == {0, 1}
    assert S._OSASCRIPT[1].endswith('disablesleep 1" with administrator privileges')
    assert S._OSASCRIPT[0].endswith('disablesleep 0" with administrator privileges')


def test_read_battery_without_psutil(monkeypatch):
    monkeypatch.setattr(S, "psutil", None)
    assert S.SystemAdapter().read_battery() == (None, None)


def test_read_thermal_state_returns_valid():
    assert S.SystemAdapter().read_thermal_state() in (0, 1, 2, 3)


def test_notify_without_rumps_is_noop(monkeypatch):
    monkeypatch.setattr(S, "rumps", None)
    S.SystemAdapter().notify("a", "b", "c")


def test_pmset_readers(monkeypatch):
    class Ok:
        returncode = 0
        stdout = " SleepDisabled 1\n lowpowermode 1\n"

    monkeypatch.setattr(S.subprocess, "run", lambda *a, **k: Ok())
    ad = S.SystemAdapter()
    assert ad.read_sleep_disabled() == (True, True)
    assert ad.read_low_power_mode() is True


def test_pmset_read_failure(monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")

    monkeypatch.setattr(S.subprocess, "run", boom)
    ok, disabled = S.SystemAdapter().read_sleep_disabled()
    assert ok is False


def test_baseline_off_confirmed():
    c, a, _ = make_controller()
    a.flag = True
    c.set_baseline_off()
    assert a.flag is False
    assert c.state.enabled is False
    assert c.state.alarm is False


def test_baseline_off_unconfirmed_alarms():
    c, a, _ = make_controller()
    a.flag = True
    a.write_succeeds = False
    c.set_baseline_off()
    assert c.state.alarm is True


def test_enable_arms_1h_on_battery():
    c, a, clk = make_controller()
    a.plugged = False
    a.percent = 90.0
    assert c.request_enable() is None
    assert c.state.enabled is True
    assert c.state.awake_until == clk.t + 3600


def test_enable_indefinite_on_ac():
    c, a, _ = make_controller()
    a.plugged = True
    assert c.request_enable() is None
    assert c.state.enabled is True
    assert c.state.awake_until is None


def test_enable_refused_when_lpm():
    c, a, _ = make_controller()
    a.lpm = True
    assert c.request_enable() == "low power mode"
    assert c.state.enabled is False


def test_enable_unconfirmed_not_enabled():
    c, a, _ = make_controller()
    a.write_succeeds = False
    assert c.request_enable() is None
    assert c.state.enabled is False
    assert any("Could not enable" in n[1] for n in a.notifications)


def test_disable_reverts():
    c, a, _ = make_controller()
    c.request_enable()
    c.request_disable()
    assert a.flag is False
    assert c.state.enabled is False
    assert c.state.alarm is False


def test_revert_unconfirmed_alarms():
    c, a, _ = make_controller()
    c.request_enable()
    a.write_succeeds = False
    c.request_revert("battery floor")
    assert c.state.alarm is True
    assert c.state.last_revert_reason == "battery floor"


def test_tick_reverts_below_floor():
    c, a, _ = make_controller()
    a.plugged = False
    a.percent = 90.0
    c.request_enable()
    a.percent = 10.0
    c.tick()
    assert a.flag is False
    assert c.state.enabled is False


def test_tick_thermal_critical_reverts_even_when_off():
    c, a, _ = make_controller()
    c.config = cfg(thermal_guard="off")
    c.request_enable()
    a.thermal = 3
    c.tick()
    assert a.flag is False


def test_tick_warn_notifies_once_no_revert():
    c, a, _ = make_controller()
    c.config = cfg(thermal_guard="warn")
    c.request_enable()
    a.thermal = 2
    c.tick()
    c.tick()
    warns = [n for n in a.notifications if "warn only" in n[1].lower()]
    assert len(warns) == 1
    assert c.state.enabled is True


def test_alarm_recovers_on_positive_off_read():
    c, a, _ = make_controller()
    c.request_enable()
    a.write_succeeds = False
    c.request_revert("timer")
    assert c.state.alarm is True
    a.write_succeeds = True
    c.tick()
    assert c.state.alarm is False
    assert a.flag is False


def test_alarm_stays_on_failed_read():
    c, a, _ = make_controller()
    c.request_enable()
    a.write_succeeds = False
    c.request_revert("timer")
    assert c.state.alarm is True
    a.read_ok = False
    c.tick()
    assert c.state.alarm is True


def test_hysteresis_after_thermal_revert():
    c, a, _ = make_controller()
    c.request_enable()
    a.thermal = 2
    c.tick()
    assert c.state.awaiting_nominal is True
    a.thermal = 1
    assert c.request_enable() == "cooling down"
    a.thermal = 0
    c.tick()
    assert c.state.awaiting_nominal is False


def test_shutdown_revert_sets_off():
    c, a, _ = make_controller()
    a.flag = True
    c.shutdown_revert()
    assert (0, False) in a.set_calls
