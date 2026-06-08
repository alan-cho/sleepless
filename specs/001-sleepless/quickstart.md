# Quickstart & Validation: Sleepless

Runnable validation mapped to the spec's Success Criteria (SC-00x). Revised post adversarial review.

## Prerequisites
```bash
cd /Users/alancho/Developer/sleepless
python3 -m pip install --user rumps psutil          # runtime (cp314 wheels exist — runs on 3.14)
python3 -m pip install --user pytest                # dev/tests
python3 -c "import rumps, psutil; from Foundation import NSProcessInfo; print('deps ok')"
```

## Run (development)
```bash
python3 sleepless.py
```
A monochrome glyph appears in the menu bar (⏾ off). No Dock icon, no window. (A second launch exits — single-instance lock.)

## One-time install (privilege + login start + boot backstop)
```bash
# 1) Passwordless privilege for exactly the two pmset commands (see contracts/privileged-commands.md)
sudo visudo -f /etc/sudoers.d/sleepless        # paste the Defaults + Cmnd_Alias + user line; save
sudo chmod 0440 /etc/sudoers.d/sleepless
sudo -K; sudo -n /usr/bin/pmset -a disablesleep 1 && sudo -n /usr/bin/pmset -a disablesleep 0 && echo "passwordless OK"

# 2) Root boot daemon — clears a stuck flag at every startup (FR-018 / SC-009)
sudo cp packaging/com.alancho.sleepless.reset.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/com.alancho.sleepless.reset.plist
sudo launchctl bootstrap system /Library/LaunchDaemons/com.alancho.sleepless.reset.plist

# 3) Login start (user agent)
cp packaging/com.alancho.sleepless.plist ~/Library/LaunchAgents/
launchctl bootout gui/$(id -u)/com.alancho.sleepless 2>/dev/null    # idempotent: bootout before bootstrap (upgrades)
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.alancho.sleepless.plist
```
Without step 1, manual enabling shows a GUI admin prompt and **unattended reverts cannot complete — the app shows a persistent ⚠ alarm instead of silently failing** (FR-017).

## Unit tests (decision core)
```bash
python3 -m pytest tests/ -v
```
Cover: `unsafe_conditions`/`decide_revert` (incl. tri-state `power_plugged` True/False/None, hard floor, Critical-thermal override, hysteresis), timer math (monotonic), config load/clamp/corruption, `parse_sleep_disabled` against **real `pmset -g` text with the line absent** and with `SleepDisabled 1`, and the exact privileged argv / two osascript constants. Target ≥85%.

## Manual validation (OS-level)
Watch: `while sleep 1; do pmset -g | grep -i SleepDisabled || echo "(absent ⇒ off)"; done`

1. **SC-001/002 flip + restore**: enable → `SleepDisabled 1`, glyph ☀/▲; Quit → flag clears **and `launchctl print gui/$(id -u)/com.alancho.sleepless` shows it is NOT relaunched** (clean exit 0, `KeepAlive{Crashed:true}`). `kill -9 <pid>` while on → launchd relaunches (≤~10s throttle) and the launch baseline clears the flag.
2. **SC-009 boot backstop**: `sudo pmset -a disablesleep 1`, reboot, before logging in confirm (via SSH or after login) the flag is cleared by the daemon.
3. **SC-001 lid-closed**: enable, `( while true; do date +%s >> /tmp/awake.log; sleep 10; done ) &`, close lid 2 min, reopen → continuous timestamps. `kill %1`.
4. **SC-003 battery floor**: unplug; set floor 95% in menu; enable → reverts within one poll, "battery floor" (notify + a line in `~/Library/Logs/sleepless.log`). Also test plugged-into-a-weak-source if available (should still revert). Restore floor 20%.
5. **SC-010 alarm**: temporarily remove the sudoers file; on battery below floor, enable → revert can't be confirmed → glyph ⚠, repeated notify, log entries; restore sudoers → next poll clears the alarm.
6. **SC-004 timer**: temporarily set `DURATIONS["1h"]=60`; unplug; enable → reverts ~60s later, "timer". Revert the edit.
7. **SC-005 Low Power Mode / thermal**: enable; `sudo pmset -a lowpowermode 1` → reverts; `…0`. Thermal: saturate cores until macOS reports Serious (watch `python3 -c "from Foundation import NSProcessInfo; print(NSProcessInfo.processInfo().thermalState())"`) → reverts; confirm re-enable is refused until Nominal (hysteresis).
8. **SC-006/007 menu + persistence**: menu shows battery %, charging, remaining, last revert reason; change floor, restart, confirm persisted; delete/corrupt config → recreated/defaults, no crash.
9. **FR-021 auto stay-awake on AC**: enable "Stay awake while plugged in"; on AC confirm `SleepDisabled 1` within one poll; unplug → reverts to normal sleep; re-plug → re-engages; restart the app → the option persists and re-engages on AC.

## Package as a standalone .app (py2app)
```bash
python3 -m pip install --user py2app
python3 setup.py py2app -A          # dev alias build
python3 setup.py py2app             # standalone -> dist/Sleepless.app
xattr -dr com.apple.quarantine dist/Sleepless.app   # avoid Gatekeeper translocation for a local unsigned app
open dist/Sleepless.app
```
If bundling fails on Python 3.14 (R1): run the LaunchAgent against the script directly (it already does), or build under a 3.13 venv. Unbundled = no banner notifications (log remains authoritative).

## Uninstall (FR-020)
```bash
sudo pmset -a disablesleep 0                                   # restore sleep FIRST
launchctl bootout gui/$(id -u)/com.alancho.sleepless 2>/dev/null
rm -f ~/Library/LaunchAgents/com.alancho.sleepless.plist
sudo launchctl bootout system/com.alancho.sleepless.reset 2>/dev/null
sudo rm -f /Library/LaunchDaemons/com.alancho.sleepless.reset.plist /etc/sudoers.d/sleepless
rm -rf ~/.config/sleepless            # optional: also remove settings
```
