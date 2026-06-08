#!/usr/bin/env bash
# Install Sleepless: a scoped passwordless sudoers entry for exactly the two
# pmset commands, the root boot-reset LaunchDaemon, and the login LaunchAgent.
# Re-runnable (idempotent). Requires sudo for the system pieces.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="$(id -un)"
UID_NUM="$(id -u)"

echo "==> sudoers (passwordless, scoped to the two pmset commands)"
TMP="$(mktemp)"
cat > "$TMP" <<EOF
Defaults!PMSET_SLEEP !requiretty
Cmnd_Alias PMSET_SLEEP = /usr/bin/pmset -a disablesleep 0, /usr/bin/pmset -a disablesleep 1
${USER_NAME} ALL=(root) NOPASSWD: PMSET_SLEEP
EOF
sudo visudo -cf "$TMP"                      # validate syntax BEFORE installing
sudo install -m 0440 -o root -g wheel "$TMP" /etc/sudoers.d/sleepless
rm -f "$TMP"
sudo -K
sudo -n /usr/bin/pmset -a disablesleep 1 && sudo -n /usr/bin/pmset -a disablesleep 0 \
  && echo "    passwordless OK"

echo "==> root boot-reset LaunchDaemon"
sudo install -m 0644 -o root -g wheel "$HERE/ai.pressw.sleepless.reset.plist" /Library/LaunchDaemons/
sudo launchctl bootout system/ai.pressw.sleepless.reset 2>/dev/null || true
sudo launchctl bootstrap system /Library/LaunchDaemons/ai.pressw.sleepless.reset.plist

echo "==> login LaunchAgent"
install -m 0644 "$HERE/ai.pressw.sleepless.plist" "$HOME/Library/LaunchAgents/"
launchctl bootout "gui/${UID_NUM}/ai.pressw.sleepless" 2>/dev/null || true
launchctl bootstrap "gui/${UID_NUM}" "$HOME/Library/LaunchAgents/ai.pressw.sleepless.plist"

echo "Done. Sleepless is running in the menu bar."
echo "If your repo or python3 path differs, edit ai.pressw.sleepless.plist first."
