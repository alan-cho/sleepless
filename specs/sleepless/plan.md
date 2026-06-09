# Implementation Plan: Sleepless — lid-closed keep-awake with safety guardrails

**Feature**: `sleepless` | **Date**: 2026-06-08 (rev. post adversarial review) | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/sleepless/spec.md`

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
Docs: `specs/sleepless/{plan,spec,research,data-model,quickstart}.md`, `contracts/`, `checklists/`.

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

## Menu-bar UX Redesign (revision — amends FR-003, FR-011; adds FR-021/022, SC-011/012)

**Presentation only.** Manual-toggle behavior, guardrails, confirm-by-read, privilege, and the whole safety model are unchanged. No new `Config` fields (so the spec-drift BLOCKING hook stays green — note that hook inspects `Config` only, not `State`; "no new `State` field" is enforced by review + tests, not the hook). The Overview is derived from existing `State` + `Readings` + `Config`; `data-model.md` unchanged. **research.md D7 is amended in this change** (monochrome text glyph → template image) — not deferred.

*Incorporates adversarial-review findings: rumps `icon` retina mechanism, py2app bundling, icon memoization, pure/impure split, full state matrix, and the user's UX calls (toggle at top; plain-text status; keep grayed rows; "Sleepless is On/Off" wording).*

### A. Per-state template menu-bar icons (FR-003, SC-011)
- **Assets**: `assets/icons/{off,ac,batt,alarm}.png` — single-file monochrome **template** PNGs (black + alpha; macOS tints for light/dark), rendered at **40×40 px**. No separate `@2x` file: rumps' `icon` setter forces a 20-pt logical size and does **not** auto-select `@2x` (verified, rumps 0.4.0 `_nsimage_from_file`), so one 40 px rep at 20 pt logical is crisp on retina (40 px backs 20 pt×2) and downscales cleanly on 1×.
- **Generation** — `scripts/make_icons.py`, committed + deterministic, run on macOS only (output checked in, so CI/end-users need no AppKit). Per state: `AppKit.NSImage.imageWithSystemSymbolName:accessibilityDescription:` (check non-nil — R8), apply `NSImageSymbolConfiguration` (~16 pt), `setTemplate_(True)`, render centered onto a transparent 40×40 `NSBitmapImageRep`, write PNG via `representationUsingType_properties_` at **72 DPI** (so the 40 px → 20 pt logical mapping is exact). Symbol map: off→`moon.fill`, ac→`bolt.fill`, batt→`battery.50`, alarm→`exclamationmark.triangle.fill` (all confirmed to resolve on macOS 26).
- **Load + memoize** — module-level `ICONS = {key: _asset("icons/<key>.png")}`. App constructs with `icon=ICONS["off"], template=True, title=None`; `template` is set **once** at construction (assigning it again triggers a full reload). `refresh()` computes `key = icon_for(st, r)` and, **only when the applied representation changes** (cache `self._icon_applied = ("icon"|"title", key)`), updates the status item — avoids a per-second disk read + NSImage alloc (the 1 Hz UI tick must not churn the status item).
- **Fallback (must short-circuit before touching `self.icon`)** — rumps' icon setter does a bare `open(path)` and raises if the file is missing. So the view resolves `path = ICONS[key]` and checks `os.path.exists(path)` **first**: if present → `self.icon = path; self.title = None`; if missing → `self.title = GLYPHS[key]` and never assign `self.icon`. Always clear `title` when an icon is applied (rumps shows title *and* icon if both are set). The app never breaks over a missing PNG; `glyph_for` is retained as the text fallback.
- **Bundle** — `setup.py` `OPTIONS` gains `"resources": ["assets/icons"]`; `_asset()` resolves via `sys.frozen`/`RESOURCEPATH` when bundled, else `os.path.dirname(os.path.abspath(__file__))` (never cwd — the LaunchAgent runs with cwd `/`).

### B. Menu reorganization (FR-011, FR-021) — toggle at the top
Top→bottom, native `rumps` menu. Order: **status → action → Overview detail → Settings ▸ → Quit** (action near the top, well separated from Quit — fixes the buried-action + quit-overshoot findings). Status + the four detail rows are `MenuItem(callback=None)` ⇒ disabled/grayed (kept by user choice). **Plain text, no glyphs/emoji** (the monochrome bar icon carries the symbol).

```
Sleepless is On — your Mac won't sleep (on power)     ← status (plain text; see matrix)
Turn Off                                              ← action (Stay Awake / Turn Off / Turn Off (ALARM))
──────────────
Battery: 80%, charging
Auto-off: in 0h58m
Battery floor: 20%
Thermal guard: Auto-revert
──────────────
Settings ▸  { Auto-off ▸ · Battery floor ▸ · Thermal guard ▸ }
──────────────
Quit (restores sleep)
```

`("Settings", [("Auto-off", [...]), ("Battery floor", [...]), ("Thermal guard", [...])])` — the three existing pickers nested one level (verified: rumps `parse_menu` recurses uncapped; reused `MenuItem` objects keep callbacks + `.state`, so `_sync_choice_checks` is unchanged).

**`menu_overview` state matrix** (the testable contract — covers every combination):

`menu_overview` **branches on `state.alarm` first** (before any timer math): alarm → fixed rows below, ignoring `awake_until` (which is intentionally left stale on the unconfirmed-revert path, `_apply_off` alarm branch). Otherwise it branches on `state.enabled`, then power.

| Row | OFF | ON (power=True) | ON (power=False) | ON (power=None) | ALARM |
|-----|-----|-----|-----|-----|-----|
| status | `Sleepless is Off — your Mac will sleep` (+ ` (last: <reason>)`) | `Sleepless is On — your Mac won't sleep (on power)` | `… (on battery)` | `… (on battery — power source unknown)` | `Sleepless — alarm: your Mac may still be awake` |
| action | `Stay Awake` | `Turn Off` | `Turn Off` | `Turn Off` | `Turn Off (ALARM)` |
| battery | `Battery: 80%, on battery` (or `Battery: —` if % unknown; `, power source unknown` if power=None) | `Battery: 80%, charging` | `Battery: 80%, on battery` | `Battery: 80%, power source unknown` | from readings (same rules as OFF) |
| auto-off | `Auto-off: Auto (by power)` (the configured choice, via the `TIMER_LABELS` reverse map of `timer_choice`) | `Auto-off: in 0h58m`; `Indefinite`; **`reverting…`** when `enabled and remaining==0` (expiry pending) | same | same | `Auto-off: —` |
| floor | `Battery floor: 20%` | same | same | same | same |
| thermal | `Thermal guard: Auto-revert` (+ ` · now Serious` only if pressure ≥ Serious) | same | same | same | same |

Power source is `True→on power`, else (False/None)→treated as battery; `None` always adds "power source unknown" (one canonical phrase, used identically in status + battery) so it never falsely claims "on power" — the weak-charger case that silently arms the 1 h timer. The **`reverting…`** cell is a transient only observable on the async worker window (between a poll detecting expiry and `_apply_off` flipping `enabled=False`); it is tested by passing a hand-constructed `State(enabled=True, awake_until=now)` to the pure `menu_overview`, not via the sync controller path (which flips `enabled` inline). **The auto-off row re-arms on a power-source change at poll cadence (≤60s), not on the 1 Hz tick** (`_rearm_on_power_change` runs in `tick()`), so just after (un)plugging the countdown may lag up to one poll while the icon/status power suffix update within ~1s — documented, not a bug.

### C. Pure, tested display logic (FR-022, SC-012)
- New **module-level** pure functions (no `rumps`, no I/O, importable under pytest):
  - `icon_for(state, readings) -> str` — returns a **logical key** `"off"|"ac"|"batt"|"alarm"` (NOT a path), so it's pure + testable; the impure path/exists/fallback resolution lives in the view. (`glyph_for` stays for the text fallback; both read from one source.)
  - `menu_overview(state, readings, config, now) -> list[tuple[str, str]]` — ordered `(key, label)` rows per the matrix, **including** the status string and the action label, so the on/off/alarm/power wording is all in the tested layer.
  - Move `THERMAL_NAMES` to a module-level constant (was on `SystemAdapter`) so `menu_overview` stays pure.
- `SleeplessApp` becomes thin wiring: builds the disabled rows once; `refresh()` maps `menu_overview(state, r, cfg, self.controller._now())` labels onto them, sets the action title, and assigns the memoized icon. It passes the **controller's injected clock** (not a fresh `time.monotonic()`), so displayed countdown and tested timer math agree. The old `state_txt` / `toggle_item.title` / inline branching is **deleted** (no duplicate source of truth). Only this thin wiring stays `# pragma: no cover`; all display *logic* is covered → FR-022/SC-012 honestly met.
- Tests: `menu_overview` for every matrix cell (off / on×{power,battery,none} / alarm; status + **action label** per state; last-revert annotation; `reverting…` at expiry via a hand-built `State`; thermal `· now Serious`; alarm rows fixed regardless of stale `awake_until`); `icon_for` key per state incl. `power_plugged is None → batt`; the missing-asset fallback decision (filesystem mocked).

### Files
`sleepless.py` (ICONS / `icon_for` / `menu_overview` / module-level `THERMAL_NAMES` + thin view rebuild with toggle-at-top + icon memoization), `scripts/make_icons.py` *(new)*, `assets/icons/*.png` *(new, committed)*, `tests/test_sleepless.py`, `setup.py` (`resources` + `_asset`), `Makefile` (`icons` target), `specs/sleepless/research.md` (amend D7).

### Risks
- **R6 — retina/template rendering**: resolved in plan (single 40 px template PNG at 20 pt logical via rumps `icon`); still eyeballed on-device in light + dark (SC-011); text-glyph fallback backs it.
- **R7 — bundled asset path**: `setup.py resources` + `sys.frozen`/`RESOURCEPATH`-aware `_asset()`; the unbundled LaunchAgent (supported delivery, R1) uses the `__file__`-based branch.
- **R8 — SF Symbol availability**: a symbol may be absent on older macOS; `make_icons.py` checks each `imageWithSystemSymbolName…` for nil and falls back to drawing the glyph shape; runtime falls back to text.
