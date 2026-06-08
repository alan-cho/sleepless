# Tasks: Sleepless — lid-closed keep-awake with safety guardrails

**Input**: Design documents from `specs/001-sleepless/` (plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md)

**Tests**: REQUIRED for this feature (Constitution V — ≥85% coverage of the decision core). Test tasks are included.

**Organization**: By user story (US1–US4) for independent implementation/testing. NOTE: this is a single-file app (`sleepless.py`), so most implementation tasks touch that one file and are therefore **sequential** (no `[P]`). Tasks that touch *separate* files (tests, plists, packaging, docs) are marked `[P]`.

## Format: `[ID] [P?] [Story] Description`

## Path Conventions
Repo root = `sleepless/`. App: `sleepless.py`. Tests: `tests/test_sleepless.py`. Packaging/ops: `setup.py`, `ai.pressw.sleepless.plist`, `ai.pressw.sleepless.reset.plist`, `install.sh`, `uninstall.sh`, `README.md`.

---

## Phase 1: Setup

- [ ] T001 Create project skeleton: `sleepless.py` with a module docstring, the `# ── TUNABLES ──` placeholder, importable pure functions, and an `if __name__ == "__main__": main()` guard (so pytest can import without launching AppKit); create `tests/` dir.
- [ ] T002 [P] Add `requirements.txt` (`rumps`, `psutil`) and `requirements-dev.txt` (`pytest`, `pytest-cov`).
- [ ] T003 [P] Add `pyproject.toml` (or `pytest.ini`) configuring pytest test paths and a coverage gate of 85% on the decision core.

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ The pure decision core + `SystemAdapter` shim. No user story can be built until this is done.** All in `sleepless.py` unless noted (so sequential).

- [ ] T004 TUNABLES block in `sleepless.py`: `GLYPHS` (off/ac/batt/alarm, each suffixed with U+FE0E for monochrome), `DURATIONS`, `HARD_FLOOR=5`, `THERMAL_REVERT=2`, `THERMAL_CRITICAL=3`, absolute `PMSET`/`SUDO`/`OSASCRIPT` paths, config/log paths (data-model.md §Constants).
- [ ] T005 `Config` dataclass + `load_config()`/`save_config()` in `sleepless.py`: clamp/validate at load, `battery_floor_percent` floored at `HARD_FLOOR`, `poll_seconds` 5–60; corrupt → back up `config.json.bad` + defaults; atomic write (`O_CREAT|O_EXCL|O_NOFOLLOW`, `os.replace`); enforce 0700 dir / 0600 file, reject symlinks/foreign-owned (contracts/config-schema.md).
- [ ] T006 Logging setup in `sleepless.py`: rotating `FileHandler` at `~/Library/Logs/sleepless.log`; a `log_event(kind, reason, observed)` helper (data-model.md §Event Log).
- [ ] T007 Pure parsers in `sleepless.py`: `parse_sleep_disabled(text, ok)` (absent line ⇒ off; tri-state for confirm — failed/empty read ⇒ "unconfirmed"), `parse_low_power_mode(text)`.
- [ ] T008 Pure core in `sleepless.py`: `unsafe_conditions(readings, config)` (compute `on_battery = power_plugged is not True` inside; hard floor; battery floor; LPM; critical thermal; auto-thermal), `can_enable(readings, config, state)` (+ `awaiting_nominal` hysteresis), `decide_revert(state, readings, config, now)`, `resolve_default_timer(power_plugged, config)`, `compute_remaining(awake_until, now)` (monotonic), `glyph_for(state, readings)`.
- [ ] T009 `SystemAdapter` in `sleepless.py`: `set_disablesleep(value, *, allow_prompt)` (assert `value in (0,1)`; constant argv `[SUDO,"-n",PMSET,"-a","disablesleep",arg]`; on failure and `allow_prompt`, pick one of TWO literal osascript constants; distinguish cancel −128; never raise); `read_sleep_disabled()`/`read_low_power_mode()` (absolute path, argv list, no shell, tri-state `(ok, value)`); `read_thermal_state()` (`NSProcessInfo.processInfo().thermalState()`); `read_battery()` (`psutil.sensors_battery()`, tri-state `power_plugged`); `notify(title, subtitle, message)` (3-arg, try/except) (contracts/privileged-commands.md).
- [ ] T010 Single-instance guard in `sleepless.py`: exclusive `flock` on `~/.config/sleepless/sleepless.lock` opened `O_NOFOLLOW`; second instance exits cleanly.
- [ ] T011 Privileged-write dispatch in `sleepless.py`: a dedicated worker thread + single-flight lock so overlapping `set_disablesleep` requests coalesce to one in-flight call; result marshaled back to the main thread (plan.md §Architecture).

### Foundational tests (mocked `SystemAdapter`; in `tests/test_sleepless.py`)
- [ ] T012 [P] Tests for parsers: real `pmset -g` text with `SleepDisabled` **absent** ⇒ off, with `SleepDisabled 1` ⇒ on, and read-failed (empty/non-zero) ⇒ unconfirmed.
- [ ] T013 [P] Tests for `unsafe_conditions`/`decide_revert`: `power_plugged` ∈ {True, False, None}; hard floor; battery floor; LPM; thermal Serious vs Critical (Critical reverts even when `thermal_guard="off"`); `awaiting_nominal` hysteresis; `battery_percent is None` ⇒ revert.
- [ ] T014 [P] Tests for `Config`: clamping (floor<5, poll out of range), corrupt-file backup+defaults, atomic/symlink-safe write round-trip (SC-007).
- [ ] T015 [P] Tests for `set_disablesleep`: exact argv equals the sudoers alias literals; osascript fallback is one of exactly two constants; `value` validation; tri-state confirm logic (success+on, success+off, read-failed).

**Checkpoint**: decision core + adapter complete and unit-tested.

---

## Phase 3: User Story 1 — Keep awake lid-closed, never stuck awake (P1) 🎯 MVP

**Goal**: Toggle Stay Awake from the menu bar; it survives nothing-stuck-awake on disable/quit/crash/boot.

**Independent Test**: quickstart §1 (flip + Quit-not-relaunched), §2 (boot backstop), §5 (alarm). SC-001/002/009/010.

- [ ] T016 [US1] `SleeplessApp(rumps.App)` skeleton in `sleepless.py`: menu with "Stay Awake" checkable item, info line(s), and a custom **Quit** item (`key='q'`) that reverts then `os._exit(0)`; no default `quit_button`.
- [ ] T017 [US1] `main()` in `sleepless.py`: `setActivationPolicy_(NSApplicationActivationPolicyAccessory)` first; single-instance guard (T010); load config; register `atexit` + SIGTERM/SIGINT revert; subscribe rumps `before_quit` revert; **force `set_disablesleep(0)` launch baseline** + confirm; start `rumps.Timer(self.tick, poll_seconds)`.
- [ ] T018 [US1] `toggle()` in `sleepless.py`: enable path calls `can_enable`; on allow → `set_disablesleep(1, allow_prompt=True)` + confirm + arm timer (US2 wires durations) + glyph; disable path → `set_disablesleep(0, allow_prompt=False)` + confirm; refuse with reason otherwise.
- [ ] T019 [US1] `tick()` core in `sleepless.py`: read all signals; reconcile glyph to actual `sleep_disabled` (absent⇒off); when enabled run `decide_revert`; on revert use non-interactive `set_disablesleep(0)`; **confirm read** — if unconfirmed enter ALARM (⚠ glyph, repeat notify+log, retry each poll), recover to OFF only on a positive off-read.
- [ ] T020 [P] [US1] `ai.pressw.sleepless.plist` (user LaunchAgent): `RunAtLoad`, `KeepAlive{Crashed:true}`, `LimitLoadToSessionType=Aqua`, `EnvironmentVariables.PATH`, `StandardOut/ErrorPath`, raised `ExitTimeOut`.
- [ ] T021 [P] [US1] `ai.pressw.sleepless.reset.plist` (root LaunchDaemon): `RunAtLoad` + `KeepAlive{SuccessfulExit:false}`, `ProgramArguments=[/usr/bin/pmset,-a,disablesleep,0]` (contracts/privileged-commands.md §Boot reset).
- [ ] T022 [P] [US1] `install.sh`/`uninstall.sh`: install writes the sudoers drop-in (substituting `$(id -un)`), copies both plists (daemon to `/Library/LaunchDaemons` root:wheel 0644), bootstraps them; uninstall reverts `disablesleep 0` FIRST, then removes plists + sudoers (quickstart §Uninstall, FR-020).
- [ ] T023 [P] [US1] Tests in `tests/test_sleepless.py`: enable→disable→quit revert; launch baseline forces off; reconcile follows external change; revert-unconfirmed ⇒ ALARM and no false "off" recovery; clean quit exits 0.

**Checkpoint**: MVP — keep-awake works and is provably never left stuck.

---

## Phase 4: User Story 2 — Don't drain the battery (P2)

**Goal**: Battery-floor auto-revert + power-conditional auto-off timer.

**Independent Test**: quickstart §4 (floor) and §6 (timer). SC-003/004.

- [ ] T024 [US2] Timer arming in `toggle()`/`sleeplessApp` (`sleepless.py`): on enable, `timer_choice=="auto"` → `resolve_default_timer(power_plugged, config)` (plugged→indefinite, battery→1h); set monotonic `awake_until`; expose remaining.
- [ ] T025 [US2] Timer + floor evaluation already in `decide_revert` (T008) — wire into `tick()` reverts with reason "timer"/"battery floor"/"hard floor"; notify + `log_event`.
- [ ] T026 [US2] "Auto-off" submenu in `sleepless.py`: Auto (by power) / 1h / 2h / 4h / Indefinite with radio checkmark; selecting re-arms; persists default when changed.
- [ ] T027 [US2] "Battery floor" submenu in `sleepless.py`: presets 10/15/20/25/30%, radio checkmark, clamps ≥ `HARD_FLOOR`, persists via `save_config`.
- [ ] T028 [P] [US2] Tests in `tests/test_sleepless.py`: power-conditional default selection; timer expiry triggers revert; floor revert across `power_plugged` ∈ {False, None}; hard-floor override of a low configured floor.

**Checkpoint**: US1 + US2 work independently.

---

## Phase 5: User Story 3 — Don't overheat, respect Low Power Mode (P3)

**Goal**: Auto-revert on LPM and Serious+ thermal, with hysteresis and a non-overridable Critical revert.

**Independent Test**: quickstart §7. SC-005.

- [ ] T029 [US3] LPM revert wired in `tick()` (`sleepless.py`) via `decide_revert` reason "low power mode"; notify + log.
- [ ] T030 [US3] Thermal revert in `tick()` (`sleepless.py`): Serious+ when `thermal_guard=="auto"`; `"warn"` notifies once (deduped); Critical always reverts; set `awaiting_nominal` after a thermal revert; `can_enable` refuses re-enable until thermal == Nominal.
- [ ] T031 [US3] "Thermal" submenu in `sleepless.py`: auto / warn / off (persisted); display current thermal state in the menu.
- [ ] T032 [P] [US3] Tests in `tests/test_sleepless.py`: LPM revert; Serious revert under "auto"; "warn" does not revert; Critical reverts under "off"; hysteresis blocks re-enable until Nominal.

**Checkpoint**: all guardrails functional.

---

## Phase 6: User Story 4 — Status at a glance + persistence (P4)

**Goal**: Informative menu + remembered settings.

**Independent Test**: quickstart §8. SC-006/007.

- [ ] T033 [US4] Menu info lines in `sleepless.py`: battery % + charging state, remaining auto-off (or "Indefinite"), current state, last revert reason; refreshed each `tick()`; alarm glyph when in ALARM.
- [ ] T034 [US4] Confirm all setting changes persist via `save_config` and reload on launch (round-trip).
- [ ] T035 [P] [US4] Tests in `tests/test_sleepless.py`: label formatting from mocked readings; settings persistence round-trip across simulated restart.

**Checkpoint**: full UX.

---

## Phase 7: Polish & Cross-Cutting

- [ ] T036 [P] `setup.py` (py2app): `argv_emulation=False`, `packages=['rumps','psutil']`, `plist.LSUIElement=True`, bundle id `ai.pressw.sleepless`. If the bundle build fails on Python 3.14, record the failure and ship via the LaunchAgent-runs-script path (T020) — do NOT block other tasks; optionally retry under a 3.13 venv.
- [ ] T037 [P] `README.md`: install (deps, sudoers, boot daemon, LaunchAgent), run, package, **uninstall**, the threat-model note, and the Python-3.14/py2app caveat.
- [ ] T038 Verify coverage ≥85% on the decision core (`pytest --cov`); fill gaps.
- [ ] T039 Run the quickstart manual validation end-to-end (SC-001…010) on the real machine; record results. SC-001 (lid-closed) and SC-009 (boot clear) are validated HERE ONLY (no automated test) — capture evidence (`/tmp/awake.log` timestamps; the reset-daemon log).
- [ ] T040 Final fail-safe review: walk every exit/error path against Constitution I ("can this leave the Mac stuck awake?"); confirm auto-revert never prompts and ALARM never false-recovers.

---

## Dependencies & Execution Order

- **Setup (P1)** → **Foundational (P2, blocking)** → **US1 (P3)** → US2 → US3 → US4 → **Polish**.
- US1 is the MVP and the safety spine (revert/quit/baseline/alarm/boot daemon); US2–US4 layer guardrails and UX onto it.
- Within the single file, implementation tasks are sequential; `[P]` tasks (tests, plists, `install.sh`, `setup.py`, `README.md`) touch separate files and can run in parallel with each other.

## Parallel Example (after T019)
```text
# Separate files — run together:
T020 ai.pressw.sleepless.plist
T021 ai.pressw.sleepless.reset.plist
T022 install.sh / uninstall.sh
T023 tests/test_sleepless.py (US1 cases)
```

## Implementation Strategy
- **MVP** = Phase 1 + 2 + 3 (US1): a keep-awake toggle that is provably never stuck awake. Stop and validate (quickstart §1/§2/§5) before layering guardrails.
- Then US2 (drain), US3 (overheat/LPM), US4 (UX) incrementally; each independently testable.
- Tests ship with each phase; the decision core (Phase 2) carries the coverage gate.

## Notes
- `[P]` = different files, no dependency. Most US tasks share `sleepless.py` ⇒ sequential by design (single-file constraint, Constitution IV).
- Commit after each phase (only when the owner asks — global rule).
- Every `set_disablesleep` is followed by a confirm read; success is never assumed.
