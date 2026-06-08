# Contract: Configuration File

**Path**: `~/.config/sleepless/config.json`. Directory created mode **0700**, file **0600**, both verified owned by the current uid and **not symlinks** on every open (`os.lstat` / `O_NOFOLLOW`). Refuse to operate if the directory is writable by others. The single-instance lock `~/.config/sleepless/sleepless.lock` is opened with `O_NOFOLLOW` too; its advisory `flock` auto-releases on crash, so no stale-lock cleanup is needed.

## Schema
```jsonc
{
  "battery_floor_percent": 20,            // int, clamped to HARD_FLOOR(5)–99
  "default_timer_plugged": "indefinite",  // "1h"|"2h"|"4h"|"indefinite"
  "default_timer_unplugged": "1h",        // "1h"|"2h"|"4h"|"indefinite"
  "poll_seconds": 60,                     // int 5–60  (max 60 so SC-003/005 hold)
  "thermal_guard": "auto"                 // "auto"|"warn"|"off"
}
```

## Read contract
- Bound input size **before** `json.load` (reject absurdly large files).
- Missing file → write defaults, continue.
- Parse error / not an object → load defaults in memory, **rename the bad file to `config.json.bad`** (via a symlink-safe path), log a warning, continue.
- Per-field: wrong type / out of range → that field's default (logged). **Bounds are enforced at load**, returning a fully-validated `Config`, before any value is used (e.g. before arming the `rumps.Timer`). Unknown keys ignored.
- **Safety floors are not fully overridable**: `battery_floor_percent` clamps to ≥ `HARD_FLOOR`; `thermal_guard:"off"` disables only the *Serious* revert — *Critical* thermal and the hard battery floor always revert (see data-model). So no valid config can fully neuter the guardrails.

## Write contract
- Atomic + symlink-safe: open `config.json.tmp` with `O_CREAT|O_EXCL|O_NOFOLLOW` (0600), write, `os.replace()` onto `config.json`.
- Triggered only when a persisted setting actually changes (floor, default timer, thermal policy) — never from the poll hot path.

## Acceptance
- Delete the file → recreated with defaults on launch.
- Corrupt it (`}{`) → app starts on defaults, `config.json.bad` appears, no crash (SC-007).
- `poll_seconds: 999` or `0` → clamped into 5–60 (logged); `battery_floor_percent: 1` → clamped to 5.
- Change the floor in the menu, restart → new floor loaded.
- A world-writable config dir or a symlinked `config.json` → refused/last-resort defaults, logged (no write-through-symlink primitive).
