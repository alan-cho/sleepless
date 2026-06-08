# Implementation Plan: Sleepless — lid-closed keep-awake with safety guardrails

**Branch**: `001-sleepless` | **Date**: 2026-06-08 (rev. post adversarial review) | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/001-sleepless/spec.md`

## Summary

A menu-bar-only macOS app that toggles the OS "disable sleep" setting so the Mac stays awake with the lid closed, wrapped in guardrails that make it safe. Single, well-commented `sleepless.py` on `rumps`, structured as a **pure decision core** (one shared `unsafe_conditions()` predicate driving both enable-gate and revert-gate, plus timer math, config I/O, output parsing — all unit-tested) + a thin **`SystemAdapter`** isolating side effects (the privileged toggle and unprivileged reads). One `rumps.Timer` (≤60s) polls to evaluate guardrails, fire **non-interactive** auto-reverts, **confirm** each change by reading back state, and reconcile the glyph. Safety is layered: revert in the `before_quit` hook + SIGTERM/atexit, a force-OFF baseline on every launch, a persistent **alarm** state when a revert can't be confirmed, and a **root boot LaunchDaemon** that clears the flag at every startup. Least privilege: exactly two `pmset` commands via a scoped sudoers entry (absolute paths), GUI prompt only for the interactive enable.

## Technical Context

**Language/Version**: Python 3.14.3 (python.org framework build). Verified: cp314 wheels exist for `pyobjc-core`/`pyobjc-framework-Cocoa` 12.2 and `psutil`; `rumps` is pure-Python — so `python3 sleepless.py` **runs on 3.14**. Only `py2app` *bundling* on 3.14 is unverified (Risk R1).

**Primary Dependencies**: `rumps` (menu bar + `Timer` + `before_quit` event), `psutil` (battery), PyObjC `Foundation.NSProcessInfo` (thermal) + `AppKit.NSApplication` (accessory policy). Stdlib: `subprocess`, `json`, `signal`, `atexit`, `os`, `time`, `pathlib`, `dataclasses`, `logging`, `fcntl` (single-instance lock).

**Storage**: JSON config `~/.config/sleepless/config.json` (0600, atomic+symlink-safe). Event log `~/Library/Logs/sleepless.log` (rotating `FileHandler`).

**Testing**: `pytest` + `unittest.mock`; pure core tested directly, `SystemAdapter` mocked. ≥85% of the core.

**Target Platform**: macOS (Apple Silicon, 26.5.x). Menu-bar accessory app; runs unbundled and bundled.

**Project Type**: Single desktop utility — one app file + tests + packaging + two launchd plists.

**Performance Goals**: Idle. One ≤60s poll of cheap reads. Negligible CPU/memory.

**Constraints**: Fail-safe; least privilege (two `pmset` commands + one fixed root boot command); unprivileged reads; absolute binary paths; works with/without sudoers (degrading to alarm, not silent failure).

**Scale/Scope**: ~1 app file (~600 LOC incl. comments) + one test module; single local user.

## Constitution Check

*GATE: re-checked after the post-review revision — PASS against constitution v1.1.0.*

| Principle | Status | How |
|-----------|--------|-----|
| **I. Fail-Safe Safety** | ✅ | Revert on disable/`before_quit`/SIGTERM, launch baseline, **root boot daemon**, **alarm** state when a revert can't be confirmed, retry each poll. `unsafe_conditions` fails toward revert on missing readings. Every write confirmed by read-back. |
| **II. Least Privilege** | ✅ | Two `pmset` argv (absolute path) via scoped `Cmnd_Alias`; one sanctioned root boot command (LaunchDaemon, fixed argv); GUI prompt only for interactive enable; auto-revert non-interactive only. Matches amended Principle II (v1.1.0). |
| **III. Deterministic, Explicit State** | ✅ | Single pure `unsafe_conditions` predicate; reconcile to real flag each poll (absent ⇒ off); explicit JSON config + event log. |
| **IV. Single File, Tweakable, Commented** | ✅ | One `sleepless.py`; TUNABLES block (glyphs, durations, hard floor, paths); commented *why*. |
| **V. Tested Where It Matters** | ✅ | Pure core unit-tested incl. tri-state power, absent-`SleepDisabled`, hysteresis, config corruption; ≥85%. OS behaviors in [quickstart.md](./quickstart.md). |

## Project Structure

```text
sleepless/
├── sleepless.py                       # the app — single, well-commented file
├── Makefile                           # task runner: run / test / cov / package / install
├── setup.py                           # py2app packaging
├── requirements.txt  requirements-dev.txt   # runtime / test deps
├── pyproject.toml                     # pytest + coverage config
├── README.md  LICENSE
├── tests/test_sleepless.py            # unit tests for the decision core
├── packaging/                         # distribution + launchd
│   ├── com.alancho.sleepless.plist        # LaunchAgent (user) — login start
│   ├── com.alancho.sleepless.reset.plist  # LaunchDaemon (root) — boot pmset reset
│   └── install.sh / uninstall.sh
└── .github/workflows/ci.yml           # run tests on push
```
Docs: `specs/001-sleepless/{plan,spec,research,data-model,quickstart}.md`, `contracts/`, `checklists/`.

**Structure Decision**: Single-file app (Constitution IV); testability via the pure-core/`SystemAdapter` split. Two small launchd plists (user agent + root reset daemon) are the lifecycle/safety backstop.

## Architecture & Key Design Decisions (post-review)

Inside `sleepless.py` (top→bottom): **TUNABLES** → **Config** (load/validate/atomic-save) → **pure core** (`unsafe_conditions`, `can_enable`, `decide_revert`, `resolve_default_timer`, `compute_remaining`, `glyph_for`, `parse_sleep_disabled`, `parse_low_power_mode`) → **`SystemAdapter`** (side effects) → **`SleeplessApp(rumps.App)`** → **`main()`**.

Key decisions and the review findings they close:
- **Confirm-by-read everywhere** — every `set_disablesleep` is followed by `read_sleep_disabled()`; `parse_sleep_disabled` treats an **absent** `SleepDisabled` line as confirmed-off (C1). Unit-tested against real `pmset -g` text (both states). The confirm read is **tri-state** (success+off / success+on / read-failed): only a *successful* read showing off clears/withholds ALARM; a failed or empty read is **unconfirmed** → enter/stay ALARM, never recover to OFF on it.
- **Alarm state** — if a revert read-back still shows the flag set, enter `ALARM` (⚠ glyph, repeat notify+log, retry each poll); never display "off" while on (C2/C3, FR-017).
- **Non-interactive auto-revert** — the poll/quit/signal paths call `sudo -n` **only**; the `osascript` GUI prompt is reserved for the interactive enable (H5; Constitution II). Short subprocess timeout (~3s). The privileged write runs on a **dedicated worker thread** (not the rumps main run loop), guarded by a **single-flight lock** so overlapping requests (poll revert vs. user toggle vs. next-poll retry) coalesce to one in-flight `pmset` call; the result is marshaled back to update state/glyph.
- **`before_quit` + ⌘Q** — revert runs in the rumps `before_quit` subscriber (the hook that actually fires under AppKit termination), the custom Quit item has `key='q'`, and reverts before `quit_application()` (H6). `atexit`+SIGTERM remain as extra layers.
- **Privileged argv as one constant** — `["/usr/bin/sudo","-n","/usr/bin/pmset","-a","disablesleep", "0"|"1"]`, byte-identical to the sudoers `Cmnd_Alias`; `value` validated to int 0/1; the `osascript` fallback selects between **two literal constant strings**, never interpolates (H1/H2).
- **Absolute paths for all binaries** incl. reads (`/usr/bin/pmset -g`), no `shell=True` (H3).
- **Tri-state power** — `on_battery = power_plugged is not True`, so plugged-but-not-charging still honors the floor; a non-overridable `HARD_FLOOR` and Critical-thermal revert can't be disabled by config (H4, MEDIUM hardening).
- **Thermal hysteresis** — after a thermal revert, `awaiting_nominal` blocks re-enable until thermal returns to Nominal (H7); your **Serious** trigger is kept.
- **Single shared `unsafe_conditions`** predicate for both enable and revert (H8) — no drift.
- **Monotonic timer** — duration uses `time.monotonic()`; wall-clock only for the display label.
- **Config hardening** — bounds clamped at load (incl. `poll_seconds` max 60 so SC-003/005 hold); 0700 dir / 0600 file, `O_NOFOLLOW`, size cap, symlink-safe atomic write; corrupt → back up + defaults.
- **Single-instance lock** — exclusive `flock` on `~/.config/sleepless/sleepless.lock`; a second launch exits.
- **No-Dock** — `NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)` called first thing in `main()` (avoids a Dock flash); `LSUIElement: True` in the bundle.
- **Boot daemon** — `packaging/com.alancho.sleepless.reset.plist` is a **root LaunchDaemon** (`/Library/LaunchDaemons/`, `RunAtLoad`) whose `ProgramArguments` are exactly `/usr/bin/pmset -a disablesleep 0`. Runs as root so no sudo needed; closes the boot/login-window/post-uninstall window (C4, FR-018, SC-009). Add `KeepAlive{SuccessfulExit:false}` (short retry) so a late/failed one-shot re-fires — launchd does not guarantee strict pre-login ordering for `RunAtLoad`, so SC-009 is "as early as launchd permits," not absolute. See [contracts/privileged-commands.md](./contracts/privileged-commands.md).
- **User LaunchAgent** — `RunAtLoad` + `KeepAlive{Crashed:true}` so only an abnormal/crash exit relaunches (re-asserting the baseline); a clean Quit MUST exit 0 (`os._exit(0)` after the best-effort revert) and `before_quit` MUST NOT raise, so launchd does not relaunch it (H2 — avoids a quit→relaunch loop). The launch baseline forces OFF before any toggle is possible, so even a crash-loop (throttled by launchd to ~10s) can't leave the flag set between respawns. `StandardErrorPath`/`StandardOutPath` set; `EnvironmentVariables.PATH` set defensively; `ExitTimeOut` raised so a teardown revert can finish.

### Risks
- **R1 — py2app on Python 3.14 (downgraded).** The *run* path works on 3.14 (wheels verified); only *bundling* is unverified. Fallback: LaunchAgent runs `python3 sleepless.py` directly (no bundle); or package under a 3.13 venv. Unbundled loses banner notifications — acceptable because the event log is authoritative. **Decision:** the supported delivery is the LaunchAgent running `python3 sleepless.py` directly; the `.app` bundle is an optional convenience (attempted in T036), explicitly NOT on the MVP critical path.
- **R2 — Unattended revert without sudoers → now an explicit alarm**, not a silent stuck state (FR-017). README leads with sudoers + boot-daemon install.
- **R3 — Notifications best-effort / dead on macOS 26 unbundled.** Mitigated by the durable event log + glyph; do not count banners toward FR-014 acceptance unbundled.
- **R4 — Hardened machines with `Defaults requiretty`** would break `sudo -n`; the sudoers drop-in adds `Defaults!PMSET_SLEEP !requiretty` defensively and docs note it.

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|----------|------------|--------------------------------------|
| Root boot LaunchDaemon | Only way to clear a stuck flag at boot / login-window / post-uninstall (SC-009, fail-safe) | App-only baseline can't run before GUI login or after uninstall → leaves a real stuck-awake window. Sanctioned in constitution v1.1.0; argv is a single fixed command. |
