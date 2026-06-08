#!/usr/bin/env bash
# Restart the Sleepless menu-bar LaunchAgent so it reloads sleepless.py.
# Use this after editing the code: a menu "Quit" will NOT relaunch the app
# (KeepAlive only restarts on a crash), so `kickstart -k` is the reliable way.
set -euo pipefail

LABEL="com.alancho.sleepless"
DOMAIN="gui/$(id -u)"
TARGET="${DOMAIN}/${LABEL}"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
APP_LOG="${HOME}/Library/Logs/sleepless.log"
ERR_LOG="/tmp/sleepless.err.log"

if launchctl print "${TARGET}" >/dev/null 2>&1; then
    echo "Restarting ${LABEL} (kickstart -k) ..."
    launchctl kickstart -k "${TARGET}"
elif [ -f "${PLIST}" ]; then
    echo "${LABEL} not loaded - bootstrapping ${PLIST} ..."
    launchctl bootstrap "${DOMAIN}" "${PLIST}"
else
    echo "Error: ${LABEL} is not installed (${PLIST} missing)." >&2
    echo "Run 'make install' first." >&2
    exit 1
fi

# Give launchd a moment, then report the fresh process state.
sleep 1
if launchctl print "${TARGET}" 2>/dev/null | grep -qE 'state = running'; then
    pid="$(launchctl print "${TARGET}" 2>/dev/null \
           | awk -F'= ' '/^[[:space:]]*pid =/{gsub(/[^0-9]/,"",$2); print $2; exit}')"
    echo "OK - ${LABEL} is running (pid ${pid:-?})."
else
    echo "Warning: ${LABEL} did not report 'running'. Check ${ERR_LOG}." >&2
fi

# Show the latest events so you can confirm the new process started.
if [ -f "${APP_LOG}" ]; then
    echo "--- last 3 events (${APP_LOG}) ---"
    tail -n 3 "${APP_LOG}"
fi
