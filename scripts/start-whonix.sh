#!/bin/bash
# Start only these prepared, already imported UTM guests, Gateway first.
# UTM must reference these artifact bundles directly, not copied imports with
# the same UUID. utmctl cannot inspect the registered bundle's filesystem path.
# --hide forwards UTM's main-window hiding option; UTM 4.7.5 still opens guest consoles.
set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
utmctl=/Applications/UTM.app/Contents/MacOS/utmctl
python=/usr/bin/python3
lsof=/usr/sbin/lsof
gateway="$root/artifacts/utm/Whonix-Gateway-LXQt.utm"
workstation="$root/artifacts/utm/Whonix-Workstation-LXQt.utm"

fail() {
  printf 'Whonix not started: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf 'Usage: %s [--hide]\n' "$0"
  printf 'Start the prepared Gateway before Workstation.\n'
  printf '  --hide  Pass utmctl start --hide: hide the main UTM window.\n'
  printf '          Guest console windows still open and may take focus.\n'
}

hide_main_window=false
case "$#" in
  0) ;;
  1)
    case "$1" in
      --hide) hide_main_window=true ;;
      -h|--help) usage; exit 0 ;;
      *) fail "usage: $0 [--hide]" ;;
    esac
    ;;
  *) fail "usage: $0 [--hide]" ;;
esac

start_guest() {
  if [ "$hide_main_window" = true ]; then
    "$utmctl" start --hide "$1"
  else
    "$utmctl" start "$1"
  fi
}

[ -x "$utmctl" ] || fail 'UTM is missing from /Applications/UTM.app.'
[ -x "$python" ] || fail '/usr/bin/python3 is required to read the bundle UUIDs.'
[ -x "$lsof" ] || fail '/usr/sbin/lsof is required to check port 8010.'
[ -f "$root/scripts/prepare_utm.py" ] || fail 'scripts/prepare_utm.py is missing.'
for bundle in "$gateway" "$workstation"; do
  [ -d "$bundle" ] && [ -f "$bundle/config.plist" ] ||
    fail "bundle or config.plist is missing: $bundle"
done

# Complete both read-only validations before making any VM start request.
"$python" "$root/scripts/prepare_utm.py" --check "$gateway" ||
  fail 'Gateway validation failed; correct the prepared bundle before starting.'
"$python" "$root/scripts/prepare_utm.py" --check "$workstation" ||
  fail 'Workstation validation failed; correct the prepared bundle before starting.'

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

guest_status() {
  local role="$1" identifier="$2" state
  state=$("$utmctl" status "$identifier") ||
    fail "cannot query $role ($identifier); import its bundle into UTM first."
  case "$state" in
    started|stopped) printf '%s\n' "$state" ;;
    *) fail "$role is '$state'; wait for its operation to finish or stop it normally in UTM, then retry." ;;
  esac
}

listener_info() {
  local result code=0
  result=$("$lsof" -nP -iTCP:8010 -sTCP:LISTEN -Fpn 2>/dev/null) || code=$?
  if [ "$code" -eq 1 ] && [ -z "$result" ]; then
    return 0
  fi
  [ "$code" -eq 0 ] || fail 'could not inspect TCP port 8010.'
  printf '%s\n' "$result"
}

gateway_listener_ready() {
  local result line pid='' count=0 endpoints=0 endpoint='' process_args
  result=$(listener_info) || return 2
  [ -n "$result" ] || return 1
  while IFS= read -r line; do
    case "$line" in
      p*) pid=${line#p}; count=$((count + 1)) ;;
      n*) endpoint=${line#n}; endpoints=$((endpoints + 1)) ;;
    esac
  done <<< "$result"
  [ "$count" -eq 1 ] && [ "$endpoints" -eq 1 ] && [ "$endpoint" = '127.0.0.1:8010' ] ||
    fail 'port 8010 is not a single IPv4 loopback listener; inspect it before starting Workstation.'
  case "$pid" in ''|*[!0-9]*) fail 'could not identify the port 8010 listener.' ;; esac
  process_args=$(/bin/ps -ww -p "$pid" -o args=) ||
    fail 'the port 8010 listener disappeared; retry after checking Gateway.'
  case " $process_args " in
    *" -uuid $gateway_id "*) return 0 ;;
    *) fail 'port 8010 belongs to a process other than this Gateway; Workstation will not be started.' ;;
  esac
}

gateway_state=$(guest_status Gateway "$gateway_id") || exit 1
workstation_state=$(guest_status Workstation "$workstation_id") || exit 1
if [ "$gateway_state" = stopped ]; then
  [ "$workstation_state" = stopped ] ||
    fail 'Workstation is already running while Gateway is stopped. Stop Workstation normally first, then retry so its socket reconnects.'
  occupied=$(listener_info) || exit 1
  [ -z "$occupied" ] || fail 'TCP port 8010 is already occupied while Gateway is stopped; inspect the listener before retrying.'
  printf 'Starting Gateway...\n'
  start_guest "$gateway_id" || fail 'UTM could not start Gateway.'
fi

deadline=$((SECONDS + 30))
while true; do
  ready=0
  gateway_listener_ready || ready=$?
  case "$ready" in
    0) break ;;
    1) ;;
    *) fail 'could not verify Gateway listener ownership.' ;;
  esac
  [ "$SECONDS" -lt "$deadline" ] ||
    fail 'Gateway did not open 127.0.0.1:8010 within 30 seconds. Inspect Gateway in UTM; Workstation was not started.'
  /bin/sleep 1
done
[ "$(guest_status Gateway "$gateway_id")" = started ] ||
  fail 'Gateway is no longer running; Workstation was not started.'

if [ "$workstation_state" = stopped ]; then
  printf 'Gateway listener ready. Starting Workstation...\n'
  start_guest "$workstation_id" ||
    fail 'UTM could not start Workstation; Gateway remains running.'
fi
[ "$(guest_status Workstation "$workstation_id")" = started ] ||
  fail 'Workstation is not running; inspect its UTM window.'
printf 'Both Whonix guests are running. Allow Tor to connect before browsing.\n'
printf 'Shut down Workstation before Gateway. After a Gateway restart, restart Workstation.\n'
