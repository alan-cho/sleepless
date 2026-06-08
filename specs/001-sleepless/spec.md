# Feature Specification: Sleepless — lid-closed keep-awake with safety guardrails

**Feature Branch**: `001-sleepless`

**Created**: 2026-06-08 · **Revised**: 2026-06-08 (post adversarial review)

**Status**: Draft

**Input**: User description: "macOS menu-bar utility that keeps a MacBook awake with the lid closed by toggling pmset disablesleep, with safety guardrails: battery floor, auto-off timer, Low Power Mode and thermal auto-revert, and always restore normal sleep on quit"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Keep the Mac awake with the lid closed, and never get stuck awake (Priority: P1)

The owner closes the lid (dock, long download, headless use) without the Mac sleeping — and is certain that when they're done, or if anything goes wrong, the Mac returns to normal sleep on its own.

**Why this priority**: The entire purpose. The "never stuck awake" guarantee is what makes disabling sleep acceptable.

**Independent Test**: Toggle Stay Awake on, close the lid, confirm it stays awake; quit, confirm normal sleep restored.

**Acceptance Scenarios**:

1. **Given** Stay Awake is off, **When** the owner selects "Stay Awake", **Then** the Mac no longer sleeps when idle or lid-closed, and the glyph shows an awake state.
2. **Given** Stay Awake is on, **When** the owner selects it again, **Then** normal sleep is restored and the glyph shows off.
3. **Given** Stay Awake is on, **When** the owner quits the app, **Then** normal sleep is restored before exit.
4. **Given** Stay Awake was on and the app was killed/crashed, **When** the app is relaunched (immediately by KeepAlive, or at next login), **Then** it starts with normal sleep restored.
5. **Given** the system was shut down or restarted, **When** it next boots, **Then** the disable-sleep flag is cleared at startup before any unattended session can begin.
6. **Given** the owner changed the sleep setting outside the app, **When** the next poll occurs, **Then** the glyph/menu reconcile to the actual system state.

---

### User Story 2 - Don't drain the battery (Priority: P2)

Leaving Stay Awake on while on battery cannot silently flatten the machine, and an unattended session ends on its own.

**Why this priority**: Battery drain is the most likely real-world harm (a closed laptop awake in a bag).

**Independent Test**: On battery, set floor above current charge, enable, confirm revert within one poll. Enable on battery, confirm 1-hour auto-off fires.

**Acceptance Scenarios**:

1. **Given** Stay Awake is on and the Mac is not confirmably charging, **When** charge falls below the configured floor (default 20%), **Then** normal sleep is restored automatically, the owner is notified and it is logged, and the glyph shows off.
2. **Given** the Mac is plugged in and charging, **When** the owner enables Stay Awake, **Then** no auto-off timer is armed by default (indefinite).
3. **Given** the Mac is on battery, **When** the owner enables Stay Awake, **Then** a 1-hour auto-off timer is armed by default.
4. **Given** an auto-off timer is armed, **When** it elapses, **Then** normal sleep is restored automatically and the event is notified + logged.
5. **Given** Stay Awake is on, **When** the owner picks a different auto-off duration, **Then** the timer re-arms and the remaining time updates.
6. **Given** charge is at or below a non-overridable hard floor, **When** the owner tries to enable (or is enabled), **Then** the app refuses/reverts regardless of the configured floor.

---

### User Story 3 - Don't overheat, and respect power-saving intent (Priority: P3)

Stay Awake backs off when the machine gets too hot (a real lid-closed risk) or when Low Power Mode is on.

**Why this priority**: Fulfills "can't overheat" and respects the user's Low Power Mode choice.

**Independent Test**: Enable, turn on Low Power Mode, confirm revert within one poll. Thermal revert exercised via documented load test.

**Acceptance Scenarios**:

1. **Given** Stay Awake is on, **When** Low Power Mode becomes active, **Then** normal sleep is restored automatically (notified + logged).
2. **Given** Stay Awake is on, **When** the OS reports Serious (or higher) thermal pressure, **Then** normal sleep is restored automatically (notified + logged).
3. **Given** a guardrail condition holds (below floor / Low Power Mode / Serious+ thermal), **When** the owner tries to enable, **Then** the app refuses and states the reason.
4. **Given** a thermal revert just happened, **When** the owner tries to re-enable, **Then** it is refused until thermal pressure returns to Nominal (hysteresis prevents thrash).
5. **Given** Critical thermal pressure, **When** it occurs, **Then** the app reverts even if the thermal guardrail is configured off (non-overridable).

---

### User Story 4 - See status at a glance and keep my settings (Priority: P4)

The menu shows what's happening; preferences persist.

**Acceptance Scenarios**:

1. **Given** the app is running, **When** the owner opens the menu, **Then** it shows battery % + charging state, remaining auto-off (or "Indefinite"), current state, and the last revert reason.
2. **Given** the owner changes the floor or auto-off, **When** restarted, **Then** the settings persist.
3. **Given** any state, **When** the owner looks at the menu bar, **Then** the glyph distinguishes off / awake-on-power / awake-on-battery, plus a distinct **alarm** glyph if a revert failed.

---

### Edge Cases

- **Privileged revert fails / unverifiable** (no sudoers, argv mismatch, command error): the app MUST NOT show a safe state; it enters a persistent **alarm** (glyph + repeated notify + log) and keeps retrying the non-interactive revert (FR-017). It never throws a GUI prompt from an automatic revert.
- **`SleepDisabled` line absent from `pmset -g`**: this is the normal OFF state — parsed as confirmed-off, never as "unreadable."
- **Plugged in but not charging** (weak charger / USB-C hub / heavy load): treated as "on battery" for the floor guard, so it can still revert.
- **Battery reading unavailable**: treated as "cannot confirm safe" → refuse enable / revert; never crash.
- **Boot / login-window window**: the flag could persist between an abnormal exit and recovery; the root boot daemon clears it at startup.
- **Two instances launched**: a single-instance guard prevents a second menu-bar icon and racing reverts.
- **Uninstall while on**: the documented uninstall restores normal sleep first, then removes privileged components.
- **Notifications unavailable** (unbundled / macOS 26 backend): the glyph + the durable event log are authoritative.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Menu-bar-only presence — no Dock icon, no main window.
- **FR-002**: Toggle "Stay Awake" mode that prevents sleep even with the lid closed.
- **FR-003**: The glyph reflects state with monochrome symbols: off / awake-on-power / awake-on-battery, plus a distinct **alarm** glyph when a revert could not be confirmed.
- **FR-004**: Automatically restore normal sleep on disable, on quit (via the reliable pre-quit hook), and best-effort on process termination.
- **FR-005**: On launch, establish a known-safe baseline (restore normal sleep, start OFF).
- **FR-006**: While on and **not confirmably charging**, restore normal sleep when charge < configurable floor (default 20%), checked each poll (default 60s). A **non-overridable hard floor** always reverts regardless of configuration.
- **FR-007**: Auto-off timer (1h/2h/4h/indefinite); default is power-conditional (plugged→indefinite, battery→1h); overridable per session.
- **FR-008**: While on, restore normal sleep automatically when Low Power Mode is active.
- **FR-009**: While on, restore normal sleep when thermal pressure ≥ Serious (configurable auto/warn/off), with **hysteresis** (re-enable only after returning to Nominal). **Critical** thermal always reverts regardless of configuration.
- **FR-010**: Refuse to enable when a guardrail condition already holds, stating the reason. (Enable-time counterpart of the while-on revert guardrails FR-006/FR-008/FR-009 — same conditions, checked before enabling rather than during.)
- **FR-011**: The menu shows battery % + charging state, remaining auto-off (or "Indefinite"), current state, and last revert reason.
- **FR-012**: Persist settings in a user-scoped JSON file with safe defaults; validate/clamp on load; handle corruption (back up the bad file, use defaults); restrictive file permissions.
- **FR-013**: On each poll, reconcile displayed state with the actual OS sleep setting (absence of the flag ⇒ off).
- **FR-014**: On each automatic revert, notify (best-effort) **and** append a durable timestamped entry to an event log; the glyph + log are the authoritative record.
- **FR-015**: Perform privileged sleep changes via passwordless privilege scoped to exactly the two required commands, using absolute binary paths. Automatic reverts use the **non-interactive** path only; a GUI administrator prompt is used **only** for the interactive enable. Never request broader privilege.
- **FR-016**: Runnable directly for development, packageable as a standalone app, and startable at login.
- **FR-017**: If a privileged revert cannot be **confirmed**, enter a persistent visible **alarm** state — repeated notification + log entry, retry on each poll — and never display a safe state while the flag is actually set. A revert counts as confirmed only via a *successful* read positively showing off; a failed or empty read is unconfirmed and keeps the alarm.
- **FR-018**: A boot-time privileged safety daemon forces normal sleep at every system startup (closes the abnormal-exit / login-window / post-uninstall window).
- **FR-019**: Only one UI instance runs at a time (single-instance guard).
- **FR-020**: A documented uninstall restores normal sleep and removes the privileged components (sudoers entry, LaunchAgent, boot daemon).

### Key Entities

- **Stay-Awake State**: intended on/off, plus the actual OS flag it is reconciled against, plus an alarm sub-state.
- **Guardrail**: battery floor (incl. hard floor), auto-off timer, Low Power Mode, thermal pressure (with hysteresis) — any forces a revert.
- **Auto-off Timer**: armed deadline or indefinite; remaining time.
- **Configuration**: floor %, default timer (plugged/unplugged), selected timer, poll interval, thermal policy — persisted.
- **Power/Battery Reading**: charge %, charging/connected state (tri-state).
- **Event Log**: durable, timestamped record of enables and reverts (with reason).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With Stay Awake on and the lid closed, the Mac stays awake for the full selected duration (verified by continuous activity).
- **SC-002**: Normal sleep is restored on every user-disable, quit, and automatic revert; an abnormal kill is recovered by KeepAlive relaunch (subject to launchd's ~10s respawn throttle) or at next login, with the launch baseline forcing OFF before any re-enable is possible; and the root boot daemon clears the flag at startup. The flag is therefore never left disabled without either a live, visible Stay-Awake state or imminent clearance — **except** a revert blocked by unconfigured privilege, which surfaces as a persistent alarm (FR-017), never a silent stuck state.
- **SC-003**: When not confirmably charging and below the floor, normal sleep is restored within one poll interval of detection.
- **SC-004**: When the auto-off timer elapses, normal sleep is restored within one poll interval of the deadline.
- **SC-005**: When Low Power Mode turns on, or thermal ≥ Serious, normal sleep is restored within one poll interval.
- **SC-006**: The menu reflects battery %, charging state, and remaining timer matching system readings within one poll interval.
- **SC-007**: Settings persist across restarts with 100% fidelity; a corrupt config never crashes the app.
- **SC-008**: With passwordless privilege configured, enabling requires zero password prompts and all unattended auto-reverts complete without any prompt.
- **SC-009**: On every boot, the root daemon clears the disable-sleep flag at startup, ordered as early as launchd permits (and retried if the one-shot is late or fails); the residual pre-clear window is bounded to early boot, not a full session. Verified per quickstart §2 (set the flag, reboot, confirm it is cleared).
- **SC-010**: A privileged revert that cannot be confirmed produces a persistent alarm state + log entry; the app never displays "off" while the flag is actually on.

## Assumptions

- Target is a MacBook on Apple Silicon, current macOS; a battery is always present.
- The lid-closed mechanism is the OS "disable sleep" setting (root-only); mandated, not a free choice.
- The owner configures a narrowly-scoped passwordless privilege entry for the two sleep commands; if absent, manual enable uses a GUI prompt and unattended reverts cannot complete — surfaced as an alarm (FR-017).
- A boot-time root daemon (single fixed command) is installed for the FR-018 backstop.
- The dev run (`python3 sleepless.py`) installs cleanly on Python 3.14 (cp314 wheels exist for the dependencies); only `py2app` *bundling* on 3.14 is unverified — the LaunchAgent can run the script directly as the supported fallback.
- Banner notifications are best-effort (the macOS 26 backend may not deliver, esp. unbundled); the glyph + event log are authoritative.
- Precise CPU temperature is not read; the OS thermal-pressure level is the overheat signal.
- The account is a single local (likely admin) user; the NOPASSWD grant's exposure is documented in the privileged-commands contract.
- Mandated stack (owner constraint; detail in plan): Python + `rumps`, `psutil`, PyObjC; no Xcode/Swift; `py2app`; LaunchAgent; config under `~/.config/sleepless/`; logs under `~/Library/Logs/`.
