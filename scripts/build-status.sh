#!/bin/bash
# Read the build service status and recent logs through the pinned SSH key.
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ "$#" -ne 0 ]; then
  printf 'Usage: %s\n' "$0" >&2
  exit 2
fi

"$root/scripts/builder-ssh" sh -s <<'REMOTE'
set -u
printf 'Whonix build service\n\n'
systemctl show whonix-build.service \
  -p ActiveState -p SubState -p ExecMainStatus -p ActiveEnterTimestamp || true
for flavor in gateway workstation; do
  build_log="/home/user/build-logs/$flavor.log"
  printf '\n%s log (recent output)\n\n' "$flavor"
  if [ -r "$build_log" ]; then
    tail -n 200 "$build_log" | grep -v '^+' | tail -n 20
  else
    printf 'No log yet: %s\n' "$build_log"
  fi
done
REMOTE
