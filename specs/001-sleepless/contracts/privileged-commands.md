# Contract: Privileged Command Boundary

Security-critical surface. Must stay exactly this narrow (Constitution II, v1.1.0). Revised post adversarial review.

## Privileged executions (the complete set)
1. **Runtime toggle (as the user, via sudo)** — only these two argv, absolute path:
   ```
   /usr/bin/pmset -a disablesleep 1
   /usr/bin/pmset -a disablesleep 0
   ```
2. **Boot reset (as root, via LaunchDaemon)** — exactly:
   ```
   /usr/bin/pmset -a disablesleep 0
   ```
Nothing else is ever run with privilege.

## sudoers drop-in (`/etc/sudoers.d/sleepless`, mode 0440 root:wheel)
```sudoers
Defaults!PMSET_SLEEP !requiretty
Cmnd_Alias PMSET_SLEEP = /usr/bin/pmset -a disablesleep 0, /usr/bin/pmset -a disablesleep 1
alancho ALL=(root) NOPASSWD: PMSET_SLEEP
```
- Install via `sudo visudo -f /etc/sudoers.d/sleepless` (validates before save).
- `install.sh` substitutes the actual `$(id -un)` for the user — the literal `alancho` is this machine's user; a mismatched username yields a non-functional grant, so every revert would alarm (FR-017).
- No wildcards — the alias enumerates exact command lines, so the grant cannot run any other `pmset` subcommand or binary.
- `!requiretty` ensures `sudo -n` works from the TTY-less LaunchAgent context (macOS default is already no-requiretty; this is defensive for MDM-hardened machines).
- **Not digest-pinned by design.** A `sha256:` pin would break on every macOS update that changes `pmset`, silently disabling the safety revert (worse than the threat it prevents — `pmset` is SIP-protected). Documented trade-off.

## Boot reset LaunchDaemon (`/Library/LaunchDaemons/ai.pressw.sleepless.reset.plist`, root:wheel 0644)
`RunAtLoad` + `KeepAlive{SuccessfulExit:false}` (short retry so a late/failed run re-fires), `ProgramArguments = [/usr/bin/pmset, -a, disablesleep, 0]`. Runs as root at boot → no sudo needed. Loaded with `sudo launchctl bootstrap system <plist>`. This is the FR-018/SC-009 backstop. launchd does not guarantee strict pre-login ordering for `RunAtLoad`, so it clears the flag "as early as launchd permits," not provably before every possible session.

## Invocation contract — `SystemAdapter.set_disablesleep(value: int, *, allow_prompt: bool) -> bool`
- Precondition: `value` is the **int** `0` or `1` (assert; else return `False`). The string is selected, never formatted: `arg = "1" if value == 1 else "0"`.
- Step 1 (always): run the constant argv `[SUDO, "-n", PMSET, "-a", "disablesleep", arg]` with a ~3s timeout. `-n` never prompts.
- Step 2 (**only if** `allow_prompt` is True — i.e. the interactive enable): one of **two literal constant** osascript strings (no interpolation):
  ```
  do shell script "/usr/bin/pmset -a disablesleep 1" with administrator privileges
  do shell script "/usr/bin/pmset -a disablesleep 0" with administrator privileges
  ```
  Distinguish user-cancel (`-128`) from other failures; never retry a cancel in a loop.
- Automatic reverts (poll / `before_quit` / SIGTERM / launch baseline) call with `allow_prompt=False` — **never** a GUI prompt that no one can answer.
- Returns `True` iff a permitted step exited 0; **never raises**. Callers MUST then `read_sleep_disabled()` and treat the read as authoritative — a `True` that the read contradicts ⇒ ALARM (FR-017).

## Unprivileged reads (no sudo, absolute path, no shell)
| Method | Command / API | Parse |
|--------|---------------|-------|
| `read_sleep_disabled()` | `[/usr/bin/pmset, -g]` | **`SleepDisabled 1` ⇒ True; line absent or `0` ⇒ False (confirmed off)** |
| `read_low_power_mode()` | `[/usr/bin/pmset, -g]` | `lowpowermode 1` ⇒ True; absent/`0` ⇒ False |
| `read_thermal_state()` | `NSProcessInfo.processInfo().thermalState()` | int 0–3 |
| `read_battery()` | `psutil.sensors_battery()` | `percent`, `power_plugged` (tri-state) |

Reads use absolute `/usr/bin/pmset` and an argv list (never PATH lookup, never `shell=True`) so a hijacked `pmset` on `PATH` can't spoof guardrail inputs. Reads are **tri-state**: (success, off) / (success, on) / (read-failed: non-zero exit or empty output). Guardrail *inputs* degrade a read-failure to the safe default. The **post-revert confirm** does NOT — a read-failure is "unconfirmed" → the caller stays in/enters ALARM and never concludes "off" from it.

## Threat model (honest)
- NOPASSWD grants **any code running as `alancho`** the ability to toggle `disablesleep` without authentication. We accept this because (a) the argv is pinned to exactly two strings (no broader `pmset` power: no `hibernatefile`, `destroyfvkeyonstandby`, etc.) and (b) the only outcome is sleep on/off, whose worst case is exactly what the app's own guardrails already bound. Nothing authenticates that the caller *is* Sleepless — that is inherent to NOPASSWD and is the accepted limit.
- Running as a standard (non-admin) user would shrink exposure but is impractical for the owner's primary account; documented, not adopted.
- The boot daemon runs as root but with a single fixed argv and no input, so it has no injection surface.

## Acceptance
- `sudo -K; sudo -n /usr/bin/pmset -a disablesleep 1` succeeds with **no password** once installed (kill the timestamp first so it tests the grant, not a cached credential); reset to 0.
- `sudo -n /usr/bin/pmset -g` (or any other arg) is **denied** — proves scope.
- The app's actual constructed argv equals the alias literals character-for-character (unit-tested).
- The osascript fallback string is one of exactly two constants (unit-tested).
- With the drop-in absent: an automatic revert returns `False` → the app enters ALARM (never prompts); an interactive enable shows the GUI prompt.
- Boot: after `sudo launchctl bootstrap system <reset plist>` and a reboot, `pmset -g | grep -i SleepDisabled` shows the flag cleared (absent/0).
