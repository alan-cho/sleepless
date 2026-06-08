#!/usr/bin/env python3
"""
Sleepless — a menu-bar utility that keeps a MacBook awake with the lid closed,
with safety guardrails so it can never drain or overheat the machine, or be
left stuck awake.

Mechanism: toggles the macOS "disable sleep" setting via
    sudo pmset -a disablesleep 1   (stay awake, even lid-closed)
    sudo pmset -a disablesleep 0   (normal sleep)
This is the ONLY thing that prevents clamshell (lid-closed) sleep — `caffeinate`
does not. Because that flag overrides the lid switch, the whole point of this
tool is the guardrails around it (see Constitution / spec).

Safety model (never leave the Mac stuck awake):
  - Revert to normal sleep on disable, on quit (rumps before_quit), on
    SIGTERM/SIGINT, and force a known-safe OFF baseline on every launch.
  - A separate root LaunchDaemon clears the flag at every boot (see the
    *.reset.plist) — the backstop the app process itself cannot guarantee.
  - Every privileged write is CONFIRMED by reading the flag back; if a revert
    cannot be confirmed we enter a loud ALARM state and keep retrying — we never
    display "off" while the Mac is actually awake.

Guardrails (checked every poll while enabled):
  - Battery floor: revert when not charging and below a configurable % (with a
    non-overridable hard floor).
  - Auto-off timer: 1h/2h/4h/indefinite; default is power-conditional
    (plugged -> indefinite, on battery -> 1h).
  - Low Power Mode: revert when active.
  - Thermal: revert at Serious+ (configurable), with hysteresis; Critical always
    reverts regardless of config.

Architecture (single file, but testable):
  - Pure functions (unsafe_conditions / decide_revert / ... ) — no I/O.
  - SystemAdapter — every side effect (pmset, AppKit, psutil, notifications).
  - Controller — orchestration (enable/disable/revert/tick + confirm/ALARM),
    testable with a fake adapter.
  - SleeplessApp(rumps.App) — thin menu-bar view; only runs under __main__.

To tweak: almost everything lives in the TUNABLES block just below.

Run:     python3 sleepless.py
Test:    python3 -m pytest tests/ -v
"""

from __future__ import annotations

import atexit
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Callable, Optional, Tuple

# --- Optional GUI / system deps -------------------------------------------
# Guarded so the pure decision core stays importable (and unit-testable) even
# when rumps / psutil / pyobjc are not installed. The actual app needs them.
try:
    import rumps
except ImportError:  # pragma: no cover - exercised only when rumps is absent
    rumps = None
try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None


# ============================================================================
# TUNABLES  — almost everything you'd want to change lives here.
# ============================================================================

# Monochrome menu-bar glyphs. U+FE0E (VARIATION SELECTOR-15) forces *text*
# (single-color) presentation instead of a color emoji. Swap freely.
_TEXT = "︎"
GLYPHS = {
    "off": "⏾" + _TEXT,    # ⏾  sleep allowed
    "ac": "☀" + _TEXT,     # ☀  awake, on power
    "batt": "▲" + _TEXT,   # ▲  awake, on battery
    "alarm": "⚠" + _TEXT,  # ⚠  revert failed — Mac may still be awake
}

# Auto-off durations, in seconds. None == indefinite (no timer).
DURATIONS = {"1h": 3600, "2h": 7200, "4h": 14400, "indefinite": None}
TIMER_LABELS = {  # menu label -> DURATIONS key (plus the power-based "auto")
    "Auto (by power)": "auto",
    "1 hour": "1h",
    "2 hours": "2h",
    "4 hours": "4h",
    "Indefinite": "indefinite",
}
FLOOR_PRESETS = [10, 15, 20, 25, 30]          # battery-floor menu choices (%)
THERMAL_LABELS = {"Auto-revert (Serious+)": "auto", "Warn only": "warn", "Off": "off"}

HARD_FLOOR = 5            # battery % the floor can never be configured below
THERMAL_REVERT = 2        # NSProcessInfoThermalStateSerious
THERMAL_CRITICAL = 3      # NSProcessInfoThermalStateCritical (always reverts)
POLL_MIN, POLL_MAX = 5, 60  # clamp range for poll_seconds (max 60 so reverts fire "within one poll")
UI_REFRESH_SECONDS = 1      # menu/glyph re-render cadence (memory-only; readings stay cached)

# Absolute paths — never rely on $PATH for privileged or state-reading calls.
PMSET = "/usr/bin/pmset"
SUDO = "/usr/bin/sudo"

# The two — and only two — privileged AppleScript fallback strings, fully
# literal (the toggle value is NEVER interpolated from input -> no injection).
_OSASCRIPT = {
    1: 'do shell script "/usr/bin/pmset -a disablesleep 1" with administrator privileges',
    0: 'do shell script "/usr/bin/pmset -a disablesleep 0" with administrator privileges',
}

CONFIG_DIR = Path.home() / ".config" / "sleepless"
CONFIG_PATH = CONFIG_DIR / "config.json"
LOCK_PATH = CONFIG_DIR / "sleepless.lock"
LOG_PATH = Path.home() / "Library" / "Logs" / "sleepless.log"

APP_NAME = "Sleepless"

log = logging.getLogger("sleepless")


# ============================================================================
# Logging / durable event record (the authoritative trail; banners are best-effort)
# ============================================================================

def setup_logging() -> None:  # pragma: no cover
    """Send logs to ~/Library/Logs/sleepless.log (rotating). Best-effort."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            LOG_PATH, maxBytes=512_000, backupCount=2
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
    except OSError:  # pragma: no cover - logging must never crash the app
        pass


def log_event(kind: str, reason: str = "", observed: object = "") -> None:
    """One durable line per enable / disable / revert / alarm."""
    log.info("event=%s reason=%s sleep_disabled=%s", kind, reason, observed)


# ============================================================================
# Config (persisted JSON, validated/clamped on load)
# ============================================================================

@dataclass
class Config:
    battery_floor_percent: int = 20
    default_timer_plugged: str = "indefinite"
    default_timer_unplugged: str = "1h"
    poll_seconds: int = 60
    thermal_guard: str = "auto"  # auto | warn | off

    def sanitized(self) -> "Config":
        """Clamp every field to a safe value (never trust the file on disk)."""
        floor = _as_int(self.battery_floor_percent, 20)
        floor = max(HARD_FLOOR, min(99, floor))
        poll = _as_int(self.poll_seconds, 60)
        poll = max(POLL_MIN, min(POLL_MAX, poll))
        tp = self.default_timer_plugged if self.default_timer_plugged in DURATIONS else "indefinite"
        tu = self.default_timer_unplugged if self.default_timer_unplugged in DURATIONS else "1h"
        tg = self.thermal_guard if self.thermal_guard in ("auto", "warn", "off") else "auto"
        return Config(floor, tp, tu, poll, tg)


def _as_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load + sanitize config. Missing -> defaults (written). Corrupt -> defaults
    (bad file preserved as .bad). Never raises."""
    _ensure_private_dir(path.parent)
    if not _is_safe_regular_file(path):
        cfg = Config().sanitized()
        save_config(cfg, path)
        return cfg
    try:
        raw = path.read_text(encoding="utf-8")
        if len(raw) > 64_000:  # bound input before parsing
            raise ValueError("config file unreasonably large")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("config root is not an object")
    except (OSError, ValueError) as exc:
        log.warning("config unreadable (%s); using defaults", exc)
        try:
            path.replace(path.with_suffix(".json.bad"))
        except OSError:
            pass
        cfg = Config().sanitized()
        save_config(cfg, path)
        return cfg
    known = {f.name for f in fields(Config)}
    return Config(**{k: v for k, v in data.items() if k in known}).sanitized()


def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None:
    """Atomic, symlink-safe write (write temp with O_NOFOLLOW|O_EXCL, then replace)."""
    try:
        _ensure_private_dir(path.parent)
        tmp = path.with_suffix(".json.tmp")
        try:
            os.unlink(tmp)
        except OSError:
            pass
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        fd = os.open(tmp, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg.__dict__, fh, indent=2)
        os.replace(tmp, path)
    except OSError as exc:  # pragma: no cover - persistence must not crash app
        log.warning("could not save config: %s", exc)


def _ensure_private_dir(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
    except OSError:  # pragma: no cover
        pass


def _is_safe_regular_file(path: Path) -> bool:
    """True only if path exists as a regular file owned by us and not a symlink."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    import stat as _stat
    if _stat.S_ISLNK(st.st_mode) or not _stat.S_ISREG(st.st_mode):
        return False
    return st.st_uid == os.getuid()


# ============================================================================
# Readings + parsers (pure)
# ============================================================================

@dataclass
class Readings:
    sleep_disabled: bool
    sleep_read_ok: bool          # False -> the pmset read itself failed (tri-state)
    low_power_mode: bool
    thermal_state: int
    battery_percent: Optional[float]
    power_plugged: Optional[bool]


def parse_sleep_disabled(text: str) -> bool:
    """`pmset -g` prints `SleepDisabled 1` only when the flag is ON; the line is
    ABSENT when it is off. So absent (or 0) => False; `SleepDisabled 1` => True."""
    for line in text.splitlines():
        if "SleepDisabled" in line:
            return line.split()[-1] == "1"
    return False


def parse_low_power_mode(text: str) -> bool:
    for line in text.splitlines():
        if "lowpowermode" in line:
            return line.split()[-1] == "1"
    return False


# ============================================================================
# Pure decision core — the single source of truth for "is this safe?"
# ============================================================================

def unsafe_conditions(r: Readings, cfg: Config) -> Optional[str]:
    """Return a revert/refuse reason if any guardrail condition holds, else None.
    Fails safe: an unreadable battery on battery power counts as unsafe."""
    on_battery = (r.power_plugged is not True)  # False OR None => treat as battery
    if r.battery_percent is None and on_battery:
        return "unsafe (no battery reading)"
    if r.battery_percent is not None:
        if r.battery_percent <= HARD_FLOOR:
            return "hard floor"
        if on_battery and r.battery_percent < cfg.battery_floor_percent:
            return "battery floor"
    if r.low_power_mode:
        return "low power mode"
    if r.thermal_state >= THERMAL_CRITICAL:
        return "thermal (critical)"
    if cfg.thermal_guard == "auto" and r.thermal_state >= THERMAL_REVERT:
        return "thermal"
    return None


def can_enable(r: Readings, cfg: Config, state: "State") -> Optional[str]:
    """Reason Stay Awake must be refused right now, or None if it may be enabled."""
    reason = unsafe_conditions(r, cfg)
    if reason:
        return reason
    # Hysteresis: after a thermal revert, block re-enable until fully Nominal.
    if state.awaiting_nominal and r.thermal_state != 0:
        return "cooling down"
    return None


def decide_revert(state: "State", r: Readings, cfg: Config, now: float) -> Optional[str]:
    """While enabled, return the reason to revert (or None). Same predicate as
    can_enable, plus timer expiry — so enable-gate and revert-gate never diverge."""
    if not state.enabled:
        return None
    reason = unsafe_conditions(r, cfg)
    if reason:
        return reason
    if state.awake_until is not None and now >= state.awake_until:
        return "timer"
    return None


def resolve_default_timer(power_plugged: Optional[bool], cfg: Config) -> str:
    """Power-conditional default: plugged -> plugged default, else unplugged default."""
    return cfg.default_timer_plugged if power_plugged is True else cfg.default_timer_unplugged


def resolve_timer_key(timer_choice: str, power_plugged: Optional[bool], cfg: Config) -> str:
    """Duration key to arm: an explicit choice as-is, else the power-conditional
    default when on 'auto'. Used at enable and to re-arm on a power-source change."""
    return (resolve_default_timer(power_plugged, cfg)
            if timer_choice == "auto" else timer_choice)


def compute_remaining(awake_until: Optional[float], now: float) -> Optional[int]:
    """Seconds left on the timer, or None for indefinite."""
    if awake_until is None:
        return None
    return max(0, int(awake_until - now))


def glyph_for(state: "State", r: Readings) -> str:
    if state.alarm:
        return GLYPHS["alarm"]
    if not state.enabled:
        return GLYPHS["off"]
    return GLYPHS["ac"] if r.power_plugged is True else GLYPHS["batt"]


def format_remaining(secs: Optional[int]) -> str:
    if secs is None:
        return "Indefinite"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


# ============================================================================
# Runtime state
# ============================================================================

@dataclass
class State:
    enabled: bool = False
    awake_until: Optional[float] = None  # monotonic deadline; None = indefinite
    timer_choice: str = "auto"           # auto | 1h | 2h | 4h | indefinite
    alarm: bool = False                  # a revert could not be confirmed
    awaiting_nominal: bool = False       # thermal hysteresis lockout
    thermal_warned: bool = False         # de-dupe "warn"-mode notifications
    last_revert_reason: Optional[str] = None
    last_power_plugged: Optional[bool] = None  # prior poll's power, to detect a source change


# ============================================================================
# SystemAdapter — every side effect lives here (mocked in tests)
# ============================================================================

class SystemAdapter:
    THERMAL_NAMES = {0: "Nominal", 1: "Fair", 2: "Serious", 3: "Critical"}

    def set_disablesleep(self, value: int, *, allow_prompt: bool) -> bool:
        """Run exactly `pmset -a disablesleep {0,1}` as root. Tries passwordless
        sudo first; only the interactive enable path may fall back to a GUI
        admin prompt. Returns True on a zero exit. Never raises.

        NOTE: the boolean is "the command exited 0" — callers MUST still confirm
        via read_sleep_disabled(); success is never assumed."""
        if value not in (0, 1):
            return False
        arg = "1" if value == 1 else "0"
        argv = [SUDO, "-n", PMSET, "-a", "disablesleep", arg]
        try:
            if subprocess.run(argv, capture_output=True, timeout=5).returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
        if not allow_prompt:
            return False  # auto-revert paths NEVER pop a dialog nobody can answer
        try:
            r = subprocess.run(
                ["/usr/bin/osascript", "-e", _OSASCRIPT[value]],
                capture_output=True, timeout=120,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _pmset_g(self) -> Tuple[bool, str]:
        """Return (ok, text) from `pmset -g`. ok=False => the read itself failed."""
        try:
            r = subprocess.run([PMSET, "-g"], capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return False, ""
            return True, r.stdout
        except (OSError, subprocess.SubprocessError):
            return False, ""

    def read_sleep_disabled(self) -> Tuple[bool, bool]:
        """Return (read_ok, disabled). For the post-write CONFIRM, a read_ok of
        False means 'unconfirmed' (do NOT assume off)."""
        ok, text = self._pmset_g()
        return ok, parse_sleep_disabled(text)

    def read_low_power_mode(self) -> bool:
        ok, text = self._pmset_g()
        return parse_low_power_mode(text) if ok else False

    def read_thermal_state(self) -> int:
        """OS thermal-pressure level 0-3 via NSProcessInfo (no sudo, Apple-Silicon
        safe). Imported lazily so the core stays importable without pyobjc."""
        try:
            from Foundation import NSProcessInfo
            return int(NSProcessInfo.processInfo().thermalState())
        except Exception:  # pragma: no cover - depends on pyobjc at runtime
            return 0

    def read_battery(self) -> Tuple[Optional[float], Optional[bool]]:
        if psutil is None:
            return None, None
        try:
            b = psutil.sensors_battery()
        except Exception:  # pragma: no cover
            return None, None
        if b is None:
            return None, None
        return b.percent, b.power_plugged

    def notify(self, title: str, subtitle: str, message: str) -> None:
        """Best-effort banner (3 positional args required by rumps; may be a
        no-op on recent macOS / unbundled). The glyph + log are authoritative."""
        if rumps is None:
            return
        try:
            rumps.notification(title, subtitle, message)
        except Exception:  # pragma: no cover - notifications are never load-bearing
            log.info("notify (banner unavailable): %s — %s", subtitle, message)


# ============================================================================
# Controller — orchestration with confirm-by-read + ALARM (testable)
# ============================================================================

class Controller:
    """Owns adapter + config + state. All the safety-critical orchestration lives
    here so it can be unit-tested with a fake adapter. `sync=True` runs privileged
    writes inline (tests + launch baseline); otherwise they run on a worker thread
    so the menu-bar run loop is never blocked, single-flighted by a lock."""

    def __init__(self, adapter: SystemAdapter, config: Config, *,
                 now: Callable[[], float] = time.monotonic, sync: bool = False):
        self.adapter = adapter
        self.config = config
        self.state = State()
        self._now = now
        self._sync = sync
        self._write_lock = threading.Lock()

    # --- reads ---
    def readings(self) -> Readings:
        ok, disabled = self.adapter.read_sleep_disabled()
        lpm = self.adapter.read_low_power_mode()
        thermal = self.adapter.read_thermal_state()
        pct, plugged = self.adapter.read_battery()
        return Readings(disabled, ok, lpm, thermal, pct, plugged)

    # --- privileged writes (confirmed) ---
    def _perform(self, value: int, *, allow_prompt: bool) -> bool:
        """Set the flag and CONFIRM by reading it back. Returns True only if the
        read positively shows the intended value (a failed read => unconfirmed)."""
        self.adapter.set_disablesleep(value, allow_prompt=allow_prompt)
        read_ok, disabled = self.adapter.read_sleep_disabled()
        return read_ok and (disabled == bool(value))

    def _dispatch(self, fn: Callable[[], None], *, critical: bool = False) -> None:
        """Run a privileged action on a worker, single-flight. Reverts are
        `critical`: they block until any in-flight write finishes rather than being
        dropped — a revert is never skipped. Enables are best-effort: if a write is
        already in flight the round is dropped and retried on the next poll."""
        if self._sync:
            if self._write_lock.acquire(blocking=False):
                try:
                    fn()
                finally:
                    self._write_lock.release()
            return
        if critical:
            def critical_runner():
                with self._write_lock:
                    fn()
            threading.Thread(target=critical_runner, daemon=True).start()
            return
        if not self._write_lock.acquire(blocking=False):
            return  # a write is already in flight; this round is dropped
        def runner():
            try:
                fn()
            finally:
                self._write_lock.release()
        threading.Thread(target=runner, daemon=True).start()

    # --- intents ---
    def set_baseline_off(self) -> None:
        """Launch baseline: force normal sleep, synchronously, before the UI starts."""
        confirmed = self._perform(0, allow_prompt=False)
        self.state = State()  # always start OFF
        self.state.alarm = not confirmed and self._flag_is_on()
        log_event("baseline", "launch", observed=self.state.alarm and "ALARM" or "off")

    def request_enable(self) -> Optional[str]:
        """User asked to enable. Returns a refusal reason, or None if accepted
        (the actual privileged write is dispatched)."""
        r = self.readings()
        reason = can_enable(r, self.config, self.state)
        if reason:
            self.adapter.notify(APP_NAME, "Not enabled", reason)
            log_event("enable-refused", reason)
            return reason
        key = resolve_timer_key(self.state.timer_choice, r.power_plugged, self.config)
        self._dispatch(lambda: self._apply_enable(key))
        return None

    def _apply_enable(self, duration_key: str) -> None:
        if self._perform(1, allow_prompt=True):
            self.state.enabled = True
            self.state.alarm = False
            self.state.awaiting_nominal = False
            self.state.thermal_warned = False
            secs = DURATIONS[duration_key]
            self.state.awake_until = None if secs is None else self._now() + secs
            log_event("enabled", duration_key, observed="on")
        else:
            self.state.enabled = False
            self.adapter.notify(APP_NAME, "Could not enable",
                                "pmset refused — is passwordless sudo configured?")
            log_event("enable-failed", "unconfirmed")

    def request_disable(self) -> None:
        self._dispatch(lambda: self._apply_off("user", thermal=False), critical=True)

    def request_revert(self, reason: str) -> None:
        self._dispatch(lambda: self._apply_off(reason, thermal=reason.startswith("thermal")),
                       critical=True)

    def _apply_off(self, reason: str, *, thermal: bool) -> None:
        confirmed = self._perform(0, allow_prompt=False)
        self.state.last_revert_reason = reason
        if confirmed:
            self.state.enabled = False
            self.state.awake_until = None
            self.state.alarm = False
            self.state.awaiting_nominal = thermal  # block re-enable until Nominal
            if reason != "user":
                self.adapter.notify(APP_NAME, "Reverted to normal sleep", reason)
            log_event("revert", reason, observed="off")
        else:
            # Could not confirm the flag is off -> the Mac may still be awake.
            self.state.enabled = False
            self.state.alarm = True
            self.adapter.notify(APP_NAME, "REVERT FAILED — still awake?", reason)
            log_event("revert-unconfirmed", reason, observed="ALARM")

    def _flag_is_on(self) -> bool:
        ok, disabled = self.adapter.read_sleep_disabled()
        return ok and disabled

    def _rearm_on_power_change(self, r: Readings) -> None:
        """When the power source flips mid-session, re-arm the auto-off timer for
        the new state: plugging in clears the countdown (indefinite, on 'auto');
        unplugging starts the selected duration (default 1h)."""
        prev = self.state.last_power_plugged
        if prev is None or (prev is True) == (r.power_plugged is True):
            return
        key = resolve_timer_key(self.state.timer_choice, r.power_plugged, self.config)
        secs = DURATIONS[key]
        self.state.awake_until = None if secs is None else self._now() + secs

    # --- periodic tick (called by the UI timer) ---
    def tick(self) -> Readings:
        """Evaluate guardrails / reconcile. Returns the latest readings so the
        view can refresh labels + glyph. Privileged writes are dispatched."""
        r = self.readings()
        if self.state.alarm:
            self._dispatch(lambda: self._retry_alarm(), critical=True)
            self.state.last_power_plugged = r.power_plugged
            return r
        # Clear the hysteresis lockout once the system is fully Nominal again.
        if self.state.awaiting_nominal and r.thermal_state == 0:
            self.state.awaiting_nominal = False
        if self.state.enabled:
            self._rearm_on_power_change(r)
            reason = decide_revert(self.state, r, self.config, self._now())
            if reason:
                self.request_revert(reason)
            elif (self.config.thermal_guard == "warn"
                  and r.thermal_state >= THERMAL_REVERT and not self.state.thermal_warned):
                self.state.thermal_warned = True
                self.adapter.notify(APP_NAME, "Thermal pressure (warn only)",
                                    "Staying awake — consider turning off.")
            elif r.thermal_state < THERMAL_REVERT:
                self.state.thermal_warned = False
        self.state.last_power_plugged = r.power_plugged
        return r

    def _retry_alarm(self) -> None:
        """While alarmed, keep trying to revert; recover only on a positive off-read."""
        confirmed = self._perform(0, allow_prompt=False)
        if confirmed:
            self.state.alarm = False
            self.state.enabled = False
            self.state.awake_until = None
            log_event("alarm-cleared", self.state.last_revert_reason or "", observed="off")
        else:
            self.adapter.notify(APP_NAME, "STILL AWAKE",
                                "Could not restore sleep — configure passwordless sudo.")
            log_event("alarm-retry", self.state.last_revert_reason or "", observed="ALARM")

    def shutdown_revert(self) -> None:
        """Best-effort synchronous revert for quit / signal / atexit paths."""
        try:
            self.adapter.set_disablesleep(0, allow_prompt=False)
            log_event("shutdown-revert", "exit", observed="off")
        except Exception:  # pragma: no cover - teardown must never raise
            pass


# ============================================================================
# Single-instance lock
# ============================================================================

def acquire_single_instance_lock() -> Optional[object]:  # pragma: no cover
    """Hold an exclusive advisory lock so only one menu-bar instance runs.
    Returns the held fd (keep it alive) or None if another instance holds it.
    The lock auto-releases if the process dies."""
    import fcntl
    _ensure_private_dir(LOCK_PATH.parent)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


# ============================================================================
# View — thin rumps menu-bar app (only defined when rumps is available)
# ============================================================================

if rumps is not None:  # pragma: no cover

    class SleeplessApp(rumps.App):
        def __init__(self, controller: "Controller"):
            super().__init__(APP_NAME, title=GLYPHS["off"], quit_button=None)
            self.controller = controller
            self._readings = None

            self.toggle_item = rumps.MenuItem("Stay Awake", callback=self.on_toggle)
            self.info_battery = rumps.MenuItem("Battery: …")
            self.info_timer = rumps.MenuItem("Auto-off: —")
            self.info_thermal = rumps.MenuItem("Thermal: …")
            self.info_state = rumps.MenuItem("State: off")

            self._timer_items = {label: rumps.MenuItem(label, callback=self.on_pick_timer)
                                 for label in TIMER_LABELS}
            self._floor_items = {f"{p}%": rumps.MenuItem(f"{p}%", callback=self.on_pick_floor)
                                 for p in FLOOR_PRESETS}
            self._thermal_items = {label: rumps.MenuItem(label, callback=self.on_pick_thermal)
                                   for label in THERMAL_LABELS}

            self.menu = [
                self.toggle_item,
                rumps.separator,
                self.info_battery, self.info_timer, self.info_thermal, self.info_state,
                rumps.separator,
                ("Auto-off", list(self._timer_items.values())),
                ("Battery floor", list(self._floor_items.values())),
                ("Thermal guard", list(self._thermal_items.values())),
                rumps.separator,
                rumps.MenuItem("Quit (restores sleep)", callback=self.on_quit, key="q"),
            ]
            self._sync_choice_checks()
            rumps.Timer(self.on_tick, self.controller.config.poll_seconds).start()
            rumps.Timer(self.on_ui_refresh, UI_REFRESH_SECONDS).start()

        # --- callbacks ---
        def on_toggle(self, _sender):
            if self.controller.state.enabled or self.controller.state.alarm:
                self.controller.request_disable()
            else:
                self.controller.request_enable()
            self.refresh()

        def on_pick_timer(self, sender):
            self.controller.state.timer_choice = TIMER_LABELS[sender.title]
            self._sync_choice_checks()
            if self.controller.state.enabled:  # re-arm immediately
                self.controller.request_enable()
            self.refresh()

        def on_pick_floor(self, sender):
            self.controller.config.battery_floor_percent = int(sender.title.rstrip("%"))
            self.controller.config = self.controller.config.sanitized()
            save_config(self.controller.config)
            self._sync_choice_checks()
            self.refresh()

        def on_pick_thermal(self, sender):
            self.controller.config.thermal_guard = THERMAL_LABELS[sender.title]
            self.controller.config = self.controller.config.sanitized()
            save_config(self.controller.config)
            self._sync_choice_checks()
            self.refresh()

        def on_tick(self, _timer):
            self._readings = self.controller.tick()
            self.refresh()

        def on_ui_refresh(self, _timer):
            self.refresh()

        def on_quit(self, _sender):
            self.controller.shutdown_revert()
            rumps.quit_application()

        # --- view refresh ---
        def refresh(self):
            if self._readings is None:
                self._readings = self.controller.readings()
            r = self._readings
            st = self.controller.state
            self.title = glyph_for(st, r)
            if st.alarm:
                self.toggle_item.title = "Turn Off (ALARM)"
            elif st.enabled:
                self.toggle_item.title = "Turn Off"
            else:
                self.toggle_item.title = "Stay Awake"
            pct = "—" if r.battery_percent is None else f"{int(r.battery_percent)}%"
            power = "charging" if r.power_plugged is True else "on battery"
            self.info_battery.title = f"Battery: {pct} ({power})"
            remaining = compute_remaining(st.awake_until, self.controller._now())
            self.info_timer.title = ("Auto-off: " + (format_remaining(remaining)
                                     if st.enabled else "—"))
            self.info_thermal.title = "Thermal: " + SystemAdapter.THERMAL_NAMES.get(
                r.thermal_state, "?")
            if st.alarm:
                state_txt = "ALARM — may still be awake"
            elif st.enabled:
                state_txt = "awake (on power)" if r.power_plugged is True else "awake (on battery)"
            else:
                state_txt = "off"
                if st.last_revert_reason:
                    state_txt += f"  (last: {st.last_revert_reason})"
            self.info_state.title = "State: " + state_txt

        def _sync_choice_checks(self):
            chosen_timer = next(l for l, k in TIMER_LABELS.items()
                                if k == self.controller.state.timer_choice)
            for label, item in self._timer_items.items():
                item.state = 1 if label == chosen_timer else 0
            for label, item in self._floor_items.items():
                item.state = 1 if label == f"{self.controller.config.battery_floor_percent}%" else 0
            for label, item in self._thermal_items.items():
                item.state = 1 if THERMAL_LABELS[label] == self.controller.config.thermal_guard else 0


# ============================================================================
# main
# ============================================================================

def main() -> int:  # pragma: no cover
    setup_logging()

    lock = acquire_single_instance_lock()
    if lock is None:
        print("Sleepless is already running.", file=sys.stderr)
        return 0

    if rumps is None:
        print("Sleepless needs `rumps` (and `psutil`): pip install --user rumps psutil",
              file=sys.stderr)
        return 1

    # Menu-bar accessory: no Dock icon. Do this first to avoid a Dock flash.
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)  # Accessory
    except Exception:  # pragma: no cover
        log.warning("could not set accessory activation policy")

    controller = Controller(SystemAdapter(), load_config())

    # Layered safety: revert on every exit path we can hook, and a guaranteed
    # OFF baseline now (the boot LaunchDaemon covers the paths a process cannot).
    controller.set_baseline_off()
    atexit.register(controller.shutdown_revert)

    def _on_signal(_signum, _frame):  # pragma: no cover - signal-driven
        controller.shutdown_revert()
        os._exit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    app = SleeplessApp(controller)
    # rumps emits before_quit on AppKit termination — the one hook that reliably
    # fires; revert there too (defense in depth alongside the custom Quit item).
    try:
        rumps.events.before_quit.register(lambda *_a: controller.shutdown_revert())
    except Exception:
        log.info("before_quit hook unavailable; relying on Quit item + atexit + signals")
    app.refresh()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
