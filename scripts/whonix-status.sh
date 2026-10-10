#!/bin/bash
# Query only the two guest UUIDs recorded in this project's UTM bundles.
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
utmctl=/Applications/UTM.app/Contents/MacOS/utmctl
python=/usr/bin/python3
gateway="$root/artifacts/utm/Whonix-Gateway-LXQt.utm"
workstation="$root/artifacts/utm/Whonix-Workstation-LXQt.utm"

fail() {
  printf 'Whonix status unavailable: %s\n' "$*" >&2
  exit 1
}

[ "$#" -eq 0 ] || fail "usage: $0"
[ -x "$utmctl" ] || fail 'UTM is missing from /Applications/UTM.app.'
[ -x "$python" ] || fail '/usr/bin/python3 is required to read the bundle UUIDs.'
for bundle in "$gateway" "$workstation"; do
  [ -d "$bundle" ] && [ -f "$bundle/config.plist" ] ||
    fail "bundle or config.plist is missing: $bundle"
done

identifiers=$("$python" - "$gateway/config.plist" "$workstation/config.plist" <<'PY'
import plistlib
import re
import sys
import uuid

try:
    identifiers = []
    for role, path in zip(("Gateway", "Workstation"), sys.argv[1:]):
        with open(path, "rb") as stream:
            info = plistlib.load(stream)["Information"]
        if re.fullmatch(r"Whonix-" + role + r"(?:-[A-Za-z0-9._]+)*", info["Name"]) is None:
            raise ValueError("bundle does not identify the expected " + role)
        if not isinstance(info["UUID"], str):
            raise ValueError(role + " UUID must be a string")
        identifier = uuid.UUID(info["UUID"])
        if not identifier.int:
            raise ValueError(role + " UUID must not be nil")
        identifiers.append(str(identifier).upper())
    if identifiers[0] == identifiers[1]:
        raise ValueError("Gateway and Workstation must have different UUIDs")
    print(" ".join(identifiers))
except (OSError, ValueError, TypeError, KeyError, plistlib.InvalidFileException) as error:
    print("Cannot read guest UUIDs: " + str(error), file=sys.stderr)
    sys.exit(1)
PY
) || fail 'could not identify the two guests.'
read -r gateway_id workstation_id <<< "$identifiers"

gateway_state=$("$utmctl" status "$gateway_id") ||
  fail "cannot query Gateway ($gateway_id); import its bundle into UTM first."
workstation_state=$("$utmctl" status "$workstation_id") ||
  fail "cannot query Workstation ($workstation_id); import its bundle into UTM first."
printf 'Gateway:     %s (%s)\n' "$gateway_state" "$gateway_id"
printf 'Workstation: %s (%s)\n' "$workstation_state" "$workstation_id"

case "$gateway_state/$workstation_state" in
  stopped/started)
    printf 'Stop Workstation normally before restarting Gateway, then run scripts/start-whonix.sh.\n'
    ;;
  stopped/stopped|started/stopped)
    printf 'Run scripts/start-whonix.sh to validate and start Gateway before Workstation.\n'
    ;;
  started/started)
    printf 'Shut down Workstation before Gateway. After a Gateway restart, restart Workstation.\n'
    ;;
  *)
    printf 'A guest is changing state or paused. Finish that operation in UTM before using the start script.\n'
    ;;
esac
