#!/usr/bin/env bash
# Uninstall Sleepless. Restores normal sleep FIRST (so you can never be left
# stuck awake), then removes every component.
set -uo pipefail

UID_NUM="$(id -u)"

echo "==> restoring normal sleep"
sudo /usr/bin/pmset -a disablesleep 0 || true

echo "==> removing login LaunchAgent"
launchctl bootout "gui/${UID_NUM}/ai.pressw.sleepless" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/ai.pressw.sleepless.plist"

echo "==> removing root boot-reset LaunchDaemon"
sudo launchctl bootout system/ai.pressw.sleepless.reset 2>/dev/null || true
sudo rm -f /Library/LaunchDaemons/ai.pressw.sleepless.reset.plist

echo "==> removing sudoers entry"
sudo rm -f /etc/sudoers.d/sleepless

echo "Done. Normal sleep restored. (Settings left in ~/.config/sleepless.)"
