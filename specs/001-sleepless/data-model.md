# Phase 1 Data Model: Sleepless

All structures are in-memory (`@dataclass`) except `Config` (persisted JSON) and the append-only event log. No database. Revised post adversarial review.

## Config (persisted)
Loaded at launch, validated/clamped, saved on change. Atomic + symlink-safe write; safe defaults on missing/corrupt.

| Field | Type | Default | Valid range | Notes |
|-------|------|---------|-------------|-------|
| `battery_floor_percent` | int | `20` | `HARD_FLOOR`–99 | Revert below this when not charging. Presets 10/15/20/25/30. Clamped to ≥ `HARD_FLOOR`. |
| `default_timer_plugged` | str | `"indefinite"` | key of `DURATIONS` | Default auto-off on AC. |
| `default_timer_unplugged` | str | `"1h"` | key of `DURATIONS` | Default auto-off on battery. |
| `poll_seconds` | int | `60` | 5–60 | Guardrail/reconcile interval. **Max 60** so SC-003/005 ("within one poll") hold. |
| `thermal_guard` | str | `"auto"` | `auto`\|`warn`\|`off` | `auto`=revert at Serious; `warn`=notify only; `off`=no Serious revert. Critical reverts regardless. |

Validation: wrong-type/out-of-range → that field's default (logged). Unknown keys ignored. Bounds enforced **at load**, before the value is used (e.g. before arming the timer). See [contracts/config-schema.md](./contracts/config-schema.md).

## Non-overridable constants (in `sleepless.py` TUNABLES, not user-config)
- `DURATIONS = {"1h":3600, "2h":7200, "4h":14400, "indefinite":None}` (seconds)
- `GLYPHS = {"off":"⏾︎", "ac":"☀︎", "batt":"▲︎", "alarm":"⚠︎"}` (monochrome; U+FE0E forces text presentation)
- `HARD_FLOOR = 5` (%) — battery floor can never be configured below this
- `THERMAL_REVERT = 2` (Serious), `THERMAL_CRITICAL = 3` — Critical always reverts even if `thermal_guard=="off"`; re-enable allowed only at `0` (Nominal)
- `PMSET = "/usr/bin/pmset"`, `SUDO = "/usr/bin/sudo"`, `OSASCRIPT = "/usr/bin/osascript"` (absolute paths)

## RuntimeState (in-memory)
| Field | Type | Initial | Notes |
|-------|------|---------|-------|
| `enabled` | bool | `False` | Intended keep-awake (reconciled vs OS each poll). |
| `awake_until` | float \| None | `None` | Monotonic deadline; `None` = indefinite. |
| `timer_choice` | str | `"auto"` | `auto`\|`1h`\|`2h`\|`4h`\|`indefinite`. |
| `alarm` | bool | `False` | True when a revert could not be confirmed (FR-017). |
| `awaiting_nominal` | bool | `False` | Set after a thermal revert; blocks re-enable until thermal == Nominal (hysteresis). |
| `last_revert_reason` | str \| None | `None` | For menu + log + notification. |

## SystemReadings (produced each poll by SystemAdapter)
| Field | Type | Meaning | If unavailable → |
|-------|------|---------|------------------|
| `sleep_disabled` | bool | Actual flag. **`SleepDisabled` line absent in `pmset -g` ⇒ `False` (confirmed off)**; `SleepDisabled 1` ⇒ True. | `False` |
| `low_power_mode` | bool | `lowpowermode 1` in `pmset -g`. | `False` |
| `thermal_state` | int | 0 Nominal…3 Critical (`NSProcessInfo.thermalState()`). | `0` |
| `battery_percent` | float \| None | Charge %. | `None` |
| `power_plugged` | bool \| None | `psutil` `power_plugged` (tri-state). | `None` |

Derived: **`on_battery = (power_plugged is not True)`** — i.e. `False` *or* `None` count as "on battery" for guardrail purposes (covers plugged-but-not-charging).

**Reads are tri-state for confirms**: `read_sleep_disabled()` distinguishes (success, off) / (success, on) / (read-failed: non-zero exit or empty output). Guardrail *inputs* degrade a read-failure to the safe default (`False`). The **post-revert confirm** does NOT — a read-failure is *unconfirmed* → stay/enter ALARM, never recover to OFF on it. (When the flag is genuinely stuck on, `pmset -g` shows `SleepDisabled 1`; an empty read means the read itself failed, not that sleep is enabled.)

## Event Log (append-only)
`~/Library/Logs/sleepless.log` via a `logging` `FileHandler` (independent of launchd stdio). Every enable, disable, revert (with reason), and alarm is logged with an ISO timestamp and the observed `sleep_disabled` value. This is the durable record FR-014/SC-010 rely on, since banner notifications are unreliable.

## Shared safety predicate (single source of truth)
```
unsafe_conditions(readings, config) -> str | None   # pure
  on_battery = (readings.power_plugged is not True)   # False OR None ⇒ treat as battery
  if readings.battery_percent is None and readings.power_plugged is not True:
      return "unsafe (no battery reading)"
  if readings.battery_percent is not None:
      if readings.battery_percent <= HARD_FLOOR:                     return "hard floor"
      if on_battery and readings.battery_percent < config.floor:     return "battery floor"
  if readings.low_power_mode:                                        return "low power mode"
  if readings.thermal_state >= THERMAL_CRITICAL:                     return "thermal (critical)"
  if config.thermal_guard == "auto" and readings.thermal_state >= THERMAL_REVERT:
                                                                     return "thermal"
  return None
```
- **`can_enable(readings, config, state)`** = `unsafe_conditions(...)` OR (`state.awaiting_nominal and thermal_state != 0` → `"cooling down"`). Refuse if non-None.
- **`decide_revert(state, readings, config, now)`** (only when `state.enabled`): return `unsafe_conditions(...)` if non-None; else if `awake_until is not None and now >= awake_until` → `"timer"`; else `None`. Same predicate as enable ⇒ enable-gate and revert-gate can never diverge (fixes the duplication finding).

## Timer helpers (monotonic)
- `resolve_default_timer(power_plugged, config)` → `default_timer_plugged` if `power_plugged is True` else `default_timer_unplugged`.
- On enable: `secs = DURATIONS[choice]`; `awake_until = None if secs is None else time.monotonic() + secs`. Monotonic so wall-clock/NTP jumps can't shorten/extend a session; a separate wall-clock "ends at HH:MM" is computed only for display.
- `compute_remaining(awake_until, now)` → `None` if indefinite, else `max(0, int(awake_until - now))`.

## State Machine
States: **OFF**, **AWAKE** (glyph AC/batt), **ALARM** (revert failed).

| From | Event | Guard | To | Side effects |
|------|-------|-------|----|--------------|
| OFF | user toggle | `can_enable()` is None | AWAKE | `set_disablesleep(1)`; reconcile-confirm; arm timer; glyph AC/batt; log+notify |
| OFF | user toggle | `can_enable()` reason | OFF | refuse; notify reason; checkbox stays off |
| AWAKE | user toggle | — | OFF | `set_disablesleep(0)`; confirm; clear timer; glyph off; log |
| AWAKE | poll: `decide_revert()` ≠ None | revert confirmed | OFF | `set_disablesleep(0)` (sudo -n only); confirm via read; clear timer; if reason=="thermal*" set `awaiting_nominal`; glyph off; log+notify |
| AWAKE | poll: revert attempted | **not confirmed** (read still shows on) | ALARM | set `alarm`; glyph ⚠; repeat notify+log; retry `set_disablesleep(0)` each poll |
| ALARM | poll | confirm-read **positively** shows off (clean exit, line absent or `0`) | OFF | clear `alarm`; log recovery |
| ALARM | poll | confirm-read failed/empty | ALARM | stay; retry; NEVER conclude "off" from a failed read |
| AWAKE/ALARM | quit (`before_quit`) / SIGTERM | — | OFF | `set_disablesleep(0)` (non-interactive) then exit; log |
| any | **launch** | — | OFF | force `set_disablesleep(0)`; confirm; `enabled=False`; `awaiting_nominal=False` |
| (system) | **boot** | — | OFF | root LaunchDaemon runs `pmset -a disablesleep 0` once (independent of the app) |
| any | poll reconcile | OS flag ≠ intended | glyph follows actual `sleep_disabled` | — |
| AWAKE | poll: `thermal_state>=Serious` and `thermal_guard=="warn"` | — | AWAKE | notify once (dedupe), do not revert |

Notes: every `set_disablesleep` is followed by a `read_sleep_disabled()` confirm; success is never assumed (Constitution I). The automatic-revert path uses the non-interactive privileged call **only** — it never invokes the GUI prompt (which can't be answered when away), so a failure routes to ALARM, not a hung dialog.
