<!--
Sync Impact Report
- Version change: (template) → 1.0.0 → 1.1.0
- Amendment 1.1.0 (2026-06-08): Principle II amended (from adversarial review) to sanction
  the boot-time root reset daemon, require non-interactive-only automatic reverts, and
  mandate absolute binary paths.
- Ratification: initial adoption for the Sleepless project
- Principles defined:
  I. Fail-Safe Safety (NON-NEGOTIABLE)
  II. Least Privilege
  III. Deterministic, Explicit State
  IV. Single File, Tweakable & Well-Commented
  V. Tested Where It Matters
- Added sections: Technology Constraints; Development Workflow & Quality Gates; Governance
- Templates reviewed for alignment:
  ✅ .specify/templates/plan-template.md (Constitution Check gate compatible)
  ✅ .specify/templates/spec-template.md (no conflicts)
  ✅ .specify/templates/tasks-template.md (test-discipline tasks compatible)
- Deferred TODOs: none
-->

# Sleepless Constitution

A personal macOS menu-bar utility that keeps a MacBook awake with the lid closed by
toggling the OS "disable sleep" setting, guarded so it can never drain or overheat the
machine or be left stuck awake.

## Core Principles

### I. Fail-Safe Safety (NON-NEGOTIABLE)
The machine MUST never be left unable to sleep against the user's intent.
- Normal sleep MUST be restored on every disable, every quit, every process termination
  path, and re-asserted to a safe baseline (Stay Awake OFF) on every launch.
- Every guardrail (battery floor, auto-off timer, Low Power Mode, thermal pressure) MUST
  fail toward restoring normal sleep. On any error, ambiguity, or unreadable signal, the
  safe action is to revert.
- A privileged command failure MUST NOT be reported as success; displayed state MUST match
  the real OS state, never an optimistic assumption.
Rationale: the whole reason this tool may exist is that disabling sleep is dangerous; the
guardrails are the product, not an add-on.

### II. Least Privilege
Root is used ONLY for the two exact sleep-toggle commands, via a narrowly-scoped
passwordless entry. The app MUST NOT request, require, or be capable of broader privilege.
All other reads (sleep state, battery, Low Power Mode, thermal) MUST use unprivileged APIs.
A GUI administrator prompt is the only fallback, and ONLY for the interactive enable path —
automatic reverts MUST use the non-interactive passwordless path exclusively (never a prompt
that nobody is present to answer). One additional sanctioned root component is permitted: a
boot-time LaunchDaemon whose ProgramArguments are exactly `pmset -a disablesleep 0`, as the
fail-safe that clears a stuck flag at every startup (justified by Principle I). No other
privileged execution is allowed. All privileged invocations use absolute binary paths.
Rationale: a tool that escalates privilege must give an attacker (or a bug) the smallest
possible surface.

### III. Deterministic, Explicit State
Same inputs, same behavior. The menu-bar glyph and menu MUST reflect the *actual* OS state
(reconciled from the system on each poll), not merely the intended state. No hidden global
state; all user settings live in one explicit, human-readable config file. Guardrail
decisions MUST be pure functions of (state, config, readings) so they can be reasoned about
and tested.

### IV. Single File, Tweakable & Well-Commented
The app ships as a single Python file the owner can read end-to-end and modify.
- All tunables (battery floor, timer durations, glyphs, poll interval, paths) MUST live in
  one obvious place.
- Comments MUST explain non-obvious *why* (OS quirks, safety reasoning). This project
  EXPLICITLY favors generous comments to support owner tweaking — overriding the usual
  "no comments" default.
- Prefer small, single-purpose functions and guard clauses over cleverness.

### V. Tested Where It Matters
Logic that can be wrong silently MUST be unit-tested: guardrail decisions, timer math,
config load/save/migration, and parsing of system readings. Target ≥85% coverage of this
testable logic. Side effects (sleep toggles, AppKit, subprocess) MUST sit behind thin
wrappers and be mocked in tests. OS-level behaviors that cannot be unit-tested (lid-closed
wake, real auto-reverts) MUST have a written, repeatable manual test plan.

## Technology Constraints
Owner-mandated and fixed for this project:
- Python 3 with `rumps` (menu bar), `psutil` (battery), and PyObjC (`Foundation`/`AppKit`)
  for thermal state and accessory-app behavior. NO Xcode/Swift project.
- Runnable directly via `python3 sleepless.py`; packageable as a standalone app via
  `py2app`; startable at login via a LaunchAgent.
- macOS on Apple Silicon. The lid-closed mechanism is the OS "disable sleep" setting
  (`pmset … disablesleep`), which requires root.
- Settings persisted as JSON under `~/.config/sleepless/`.

## Development Workflow & Quality Gates
- Plan-first on non-trivial work; keep changes in scope and flag risky/out-of-scope items.
- Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`); work on
  `feat/…` or the feature branch — never commit to `main`/`master` directly.
- Commits and pushes happen ONLY when the owner asks.
- All tests pass before a change is considered done; new logic ships with tests.
- Every change is checked against Principle I (Fail-Safe Safety) before completion: "can
  this path leave the Mac stuck awake?" If yes, it is not done.

## Governance
This constitution supersedes ad-hoc preferences for this project. Amendments are made by
editing this file with a version bump and a dated note in the Sync Impact Report.
- Versioning: MAJOR = principle removed/redefined; MINOR = principle/section added or
  materially expanded; PATCH = clarifications/wording.
- Compliance: the spec, plan, and tasks artifacts MUST remain consistent with these
  principles; the `/speckit-analyze` gate verifies this before implementation.

**Version**: 1.1.0 | **Ratified**: 2026-06-08 | **Last Amended**: 2026-06-08
