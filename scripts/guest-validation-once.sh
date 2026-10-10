#!/bin/bash
# Temporary offline-installed diagnostic, removed after validation.
# No listener, guest agent, input injection, or remote command execution.
set -u
umask 077
state=/var/lib/whonix-arm64-validation
[[ -f "$state/enabled" && ! -L "$state/enabled" ]] || exit 1
rm -- "$state/enabled"
exec > "$state/report.txt" 2>&1
trap 'sync; systemctl poweroff --no-block' EXIT
date --iso-8601=seconds
uname -a
nproc
systemd-detect-virt
sleep 40
systemd-analyze
printf '\n=== Kloak initial state ===\n'
systemctl show kloak -p ActiveState -p SubState -p Result -p NRestarts -p MainPID -p SystemCallFilter
printf '\n=== Kloak compositor and device connection ===\n'
cat /run/kloak_wl_compositor_data
kloak_pid=$(systemctl show kloak -p MainPID --value)
if [[ "$kloak_pid" =~ ^[1-9][0-9]*$ && -d "/proc/$kloak_pid" ]]; then
    tr '\0' '\n' < "/proc/$kloak_pid/environ" |
        grep -E '^(XDG_RUNTIME_DIR|WAYLAND_DISPLAY|KLOAK_EXIT_ON_WL_CONNECT_FAILURE)='
    ls -l "/proc/$kloak_pid/fd"
fi
printf '\n=== Desktop and memory ===\n'
pgrep -a labwc
free -h
cat /proc/bus/input/devices
if [[ -d /run/user/1000 ]]; then
    runuser -u user -- env XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 wlr-randr
fi
printf '\n=== Idle memory and CPU samples (first row is since boot) ===\n'
vmstat 5 7
printf '\n=== Kloak final state and logs ===\n'
systemctl show kloak -p ActiveState -p SubState -p Result -p NRestarts -p MainPID
systemctl --failed --no-pager
journalctl -b -u kloak --no-pager
printf '\n=== Kernel warnings ===\n'
journalctl -b -k -p warning --no-pager
printf '\n=== Network recovery ===\n'
if [[ -x "$state/runtime-probe.sh" ]]; then
    timeout 110 runuser -u user -- "$state/runtime-probe.sh"
fi
printf '\n=== Validation finished ===\n'
date --iso-8601=seconds
