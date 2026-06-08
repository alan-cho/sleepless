# Phase 0 Research: Sleepless

All decisions verified against environment probing on the target machine (Apple Silicon, macOS 26.5.1, Python 3.14.3) and current library/OS documentation.

## D1 — Lid-closed mechanism: `pmset disablesleep`
- **Decision**: Toggle `sudo /usr/bin/pmset -a disablesleep 1|0`. State read via `pmset -g` field `SleepDisabled`.
- **Rationale**: `disablesleep` is the only setting that prevents *clamshell* (lid-closed) sleep. Confirmed available at `/usr/bin/pmset`; `pmset -g` is readable without sudo.
- **Alternatives**: `caffeinate` — rejected: does **not** prevent clamshell sleep. `IOPMAssertion` APIs — rejected: same limitation, more complexity, still no clamshell override.

## D2 — Overheat signal: `NSProcessInfo.thermalState`
- **Decision**: Poll `Foundation.NSProcessInfo.processInfo().thermalState()` → `0` nominal / `1` fair / `2` serious / `3` critical; auto-revert at ≥ 2.
- **Rationale**: Reliable, no sudo, architecture-independent, already available via the PyObjC we depend on for rumps. It is the same OS thermal-pressure level macOS uses to begin throttling.
- **Alternatives**: `pmset -g therm` `CPU_Speed_Limit` — rejected: Intel-era field; on this Apple Silicon machine `pmset -g therm` reports only "No thermal warning level…", no usable number. `powermetrics` — rejected: requires root. SMC temperature reads — rejected: brittle, undocumented keys on Apple Silicon.

## D3 — Privilege: scoped passwordless sudoers + GUI fallback
- **Decision**: `Cmnd_Alias` granting NOPASSWD for exactly the two `pmset … disablesleep 0|1` commands; the app runs `sudo -n …` first, falling back to `osascript … with administrator privileges`. See [contracts/privileged-commands.md](./contracts/privileged-commands.md).
- **Rationale**: Least privilege (Constitution II). `sudo -n` fails fast (no hang) when not configured; the GUI prompt covers first-run before setup. `/etc/sudoers.d/` is empty/writable on this machine.
- **Alternatives**: setuid helper — rejected: more attack surface, harder to audit. Broad `pmset *` NOPASSWD — rejected: violates least privilege. Always-`osascript` — rejected: can't run unattended (breaks auto-revert).

## D4 — Quit/exit safety (the "never stuck awake" guarantee)
- **Decision**: Layered revert: (1) custom Quit menu item (`quit_button = None`) that reverts then calls `rumps.quit_application()`; (2) `atexit` handler; (3) `signal.signal(SIGTERM/SIGINT, …)`; (4) **force `disablesleep 0` on every launch** as the backstop.
- **Rationale**: rumps' `NSApplication.terminate_` does not reliably run Python `atexit`/signal handlers, so no single hook is sufficient. The launch-time baseline guarantees recovery even if every in-process hook is missed (crash, `kill -9`, reboot).
- **Alternatives**: `atexit` only — rejected: unreliable under AppKit termination. Persisting+restoring prior "on" state across restarts — rejected: unsafe; safety beats convenience.

## D5 — No Dock icon (menu-bar accessory)
- **Decision**: At runtime, before `app.run()`, call `AppKit.NSApplication.sharedApplication().setActivationPolicy_(1)` (`1` = Accessory). For the bundle, set `LSUIElement: True` in the Info.plist.
- **Rationale**: Covers both the unbundled script and the packaged app. `1` used directly to avoid relying on a possibly-absent named constant.

## D6 — Battery: `psutil.sensors_battery()`
- **Decision**: `percent` and `power_plugged`. Treat `None`/unavailable as "cannot confirm safe."
- **Rationale**: Simple, dependency already mandated, no native code. Degrades safe if absent.

## D7 — Monochrome glyphs
- **Decision**: Text glyphs forced to monochrome with the U+FE0E variation selector, e.g. `"⏾︎"` (off), `"☀︎"` (awake/AC), `"▲︎"` (awake/battery), set via `self.title`.
- **Rationale**: User chose monochrome; U+FE0E forces text (not emoji/color) presentation. All three live in the `GLYPHS` tunable for one-line tweaking.
- **Alternatives**: Color emoji — rejected by user. Template NSImage icons — rejected: heavier, not "single-file tweakable."

## D8 — Packaging: py2app (with fallback)
- **Decision**: `setup.py` with `argv_emulation: False`, `packages: ['rumps','psutil']`, `plist.LSUIElement: True`. Build `python3 setup.py py2app` (`-A` for dev alias mode).
- **Rationale**: Standard path to a standalone `.app`.
- **Risk/Alternative**: Python 3.14 is new; if the bundle build fails, the LaunchAgent runs `python3 sleepless.py` directly (no bundle), or package under a 3.13 venv. Documented in [quickstart.md](./quickstart.md).

## D9 — Single 60 s poll
- **Decision**: One `rumps.Timer(self.tick, poll_seconds=60)` drives battery floor, LPM, thermal, timer expiry, and state reconcile.
- **Rationale**: Simplest correct design; 60 s granularity is fine for these guardrails (timers are in hours; battery moves slowly). Tunable.

## D10 — Notifications best-effort
- **Decision**: `rumps.notification(...)` wrapped in try/except; never load-bearing.
- **Rationale**: Banners are reliable only from a bundled app with a bundle id. The glyph + menu labels are the authoritative state indicator (Constitution III).

## D11 — Config: JSON at `~/.config/sleepless/config.json`
- **Decision**: Atomic write (temp file + `os.replace`); on missing/corrupt, load defaults and back up the bad file. Schema in [config-schema.md](./contracts/config-schema.md).
- **Rationale**: Explicit, human-readable, matches the spec's persistence requirement; atomic write avoids partial-write corruption.
