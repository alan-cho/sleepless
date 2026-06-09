# Sleepless

A lightweight macOS menu-bar utility that keeps a MacBook awake **with the lid closed**
by toggling `pmset -a disablesleep`, wrapped in safety guardrails so it can never drain or
overheat the machine — or be left stuck awake.

- **Menu-bar only** (no Dock icon, no window). Monochrome glyphs: `⏾` off · `☀` awake on
  power · `▲` awake on battery · `⚠` revert failed.
- **Guardrails:** battery-floor auto-revert · power-conditional auto-off timer
  (plugged → indefinite, battery → 1h) · Low Power Mode revert · thermal revert at Serious+
  (with hysteresis; Critical always reverts).
- **Never stuck awake:** reverts on disable/quit/termination, forces a safe baseline on
  every launch, confirms every change by reading the flag back (loud **ALARM** if it can't),
  and a tiny root daemon clears the flag at every boot.

Full design lives in [`specs/sleepless/`](specs/sleepless/) (spec, plan, contracts,
data model). Built with the spec-kit blueprint flow.

## Layout
- `sleepless.py` — the app (single, well-commented file) · `tests/` — unit tests
- `packaging/` — launchd plists + `install.sh` / `uninstall.sh`
- `Makefile` — task runner (`make help`) · `setup.py` — py2app packaging
- `specs/sleepless/` — design docs (spec, plan, contracts, …)

## Requirements
- macOS (Apple Silicon), Python 3 (a python.org framework build recommended for the menu bar).
- `make deps` (installs `rumps`, `psutil`). The run path works on Python 3.14; only the
  optional `py2app` bundle is unverified there — see Packaging.

## Run
```bash
make run            # or: python3 sleepless.py
```

## Install (privilege + login start + boot backstop)
```bash
make install        # or: bash packaging/install.sh
```
This installs a **scoped passwordless sudoers** entry for *exactly* the two
`pmset -a disablesleep {0,1}` commands, the **root boot-reset LaunchDaemon**, and the
**login LaunchAgent**. Edit `packaging/com.alancho.sleepless.plist` first if your repo path
or `python3` location differ.

Without the sudoers entry, manual enabling shows a GUI admin prompt, and unattended reverts
can't complete silently — the app then shows a persistent **⚠ ALARM** rather than failing
quietly. Events are also logged to `~/Library/Logs/sleepless.log`.

## Test
```bash
make test           # or: make cov  (enforces the >=85% coverage gate)
```
Unit tests cover the decision core, config persistence/corruption, the privileged-command
construction, and the controller's confirm-by-read / ALARM / hysteresis logic.

## Package as a standalone .app (optional)
```bash
make package        # python3 setup.py py2app  ->  dist/Sleepless.app
xattr -dr com.apple.quarantine dist/Sleepless.app
```
If bundling fails on Python 3.14, the supported delivery is the LaunchAgent running the
script directly (no bundle), or build under a Python 3.13 venv.

## Uninstall
```bash
make uninstall                     # or: bash packaging/uninstall.sh — restores sleep first
```

## Tweaking
Everything adjustable is in the `TUNABLES` block at the top of
[`sleepless.py`](sleepless.py): glyphs, durations, the hard battery floor, thermal
thresholds, poll interval, and paths. The file is heavily commented for exactly this.

## Security note
The NOPASSWD grant lets any process running as your user toggle `disablesleep` without a
password — accepted because it is pinned to two exact argv strings (no broader `pmset`
power) and the only effect is sleep on/off, which the guardrails already bound. The binary
is intentionally **not** digest-pinned (a macOS update would change the hash and silently
break the safety revert). See [`packaging/`](packaging/) and
[`contracts/privileged-commands.md`](specs/sleepless/contracts/privileged-commands.md).
