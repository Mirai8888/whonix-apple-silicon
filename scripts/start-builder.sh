#!/bin/bash
# Start the dedicated Debian ARM64 build VM; guest bundles run in UTM.
set -euo pipefail
umask 077

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
disk="$root/builder/Whonix ARM64 Builder.utm/Data/builder.qcow2"
seed="$root/builder/Whonix ARM64 Builder.utm/Data/seed.iso"
firmware=/opt/homebrew/share/qemu/edk2-aarch64-code.fd
pidfile="$root/builder/qemu.pid"
lockdir="$root/builder/start.lock"

fail() {
  printf 'Builder not started: %s\n' "$*" >&2
  exit 1
}

# The private boundary protects guest files (including SSH host keys) from
# other local accounts, even when an imported disk itself is mode 0644.
check_directory() {
  local path=$1 private=${2:-false} owner mode
  [ -d "$path" ] && [ ! -L "$path" ] || fail "not a real directory: $path"
  owner=$(stat -f '%u' "$path") || fail "cannot inspect directory: $path"
  mode=$(stat -f '%Lp' "$path") || fail "cannot inspect directory: $path"
  [ "$owner" = "$caller_uid" ] || fail "directory must belong to the current user: $path"
  if [ "$private" = true ]; then
    (( (8#$mode & 077) == 0 )) || fail "builder directory must be private (chmod 700): $path"
  else
    (( (8#$mode & 022) == 0 )) || fail "directory must not be writable by other users: $path"
  fi
}

check_file() {
  local path=$1 owner mode links
  [ -f "$path" ] && [ ! -L "$path" ] || fail "not a regular file: $path"
  owner=$(stat -f '%u' "$path") || fail "cannot inspect file: $path"
  mode=$(stat -f '%Lp' "$path") || fail "cannot inspect file: $path"
  links=$(stat -f '%l' "$path") || fail "cannot inspect file: $path"
  [ "$owner" = "$caller_uid" ] && [ "$links" = 1 ] ||
    fail "file must belong to the current user and have one hard link: $path"
  (( (8#$mode & 022) == 0 )) || fail "file must not be writable by other users: $path"
}

check_unused() {
  local description=$1 result status
  shift
  if result=$(lsof "$@" 2>"$lockdir/lsof.err"); then
    status=0
  else
    status=$?
  fi
  [ ! -s "$lockdir/lsof.err" ] || fail "cannot reliably check $description: lsof reported an error."
  if [ "$status" -eq 1 ] && [ -z "$result" ]; then
    return 0
  fi
  [ "$status" -eq 0 ] && [ -n "$result" ] ||
    fail "cannot reliably check $description: unexpected lsof result ($status)."
  fail "$description is already in use."
}

if [ "$#" -ne 0 ]; then
  printf 'Usage: %s\n' "$0" >&2
  exit 2
fi
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = arm64 ] ||
  fail 'this builder requires native ARM64 macOS.'
caller_uid=$(id -u)
check_directory "$root/builder" true
check_directory "$root/builder/Whonix ARM64 Builder.utm"
check_directory "$root/builder/Whonix ARM64 Builder.utm/Data"
check_file "$disk"
check_file "$seed"
mkdir "$lockdir" 2>/dev/null ||
  fail "another start is in progress (lock: $lockdir)."
trap 'rm -f -- "$lockdir/lsof.err"; rmdir "$lockdir" 2>/dev/null || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -e "$pidfile" ] || [ -L "$pidfile" ]; then
  check_file "$pidfile"
  builder_pid=$(cat "$pidfile")
  case "$builder_pid" in
    ''|*[!0-9]*) fail "invalid PID file: $pidfile" ;;
  esac
  [ "$builder_pid" -gt 1 ] || fail "invalid PID in $pidfile"
  if ps -p "$builder_pid" -o pid= >/dev/null 2>&1; then
    process_args=$(ps -ww -p "$builder_pid" -o args=)
    case "$process_args" in
      *qemu-system-aarch64*'Whonix ARM64 Builder.utm/Data/builder.qcow2'*)
        printf 'Builder is already running (PID %s).\n' "$builder_pid"
        exit 0
        ;;
      *) fail "PID $builder_pid is occupied by another process; leaving it untouched." ;;
    esac
  fi
fi

qemu_bin=$(command -v qemu-system-aarch64 || true)
if [ -z "$qemu_bin" ] && [ -x /opt/homebrew/bin/qemu-system-aarch64 ]; then
  qemu_bin=/opt/homebrew/bin/qemu-system-aarch64
fi
[ -n "$qemu_bin" ] || fail 'qemu-system-aarch64 is missing; install Homebrew qemu.'
[ -s "$disk" ] && [ -w "$disk" ] || fail "builder disk is missing or not writable: $disk"
[ -s "$seed" ] && [ -r "$seed" ] || fail "cloud-init seed is missing or unreadable: $seed"
[ -r "$firmware" ] || fail "ARM64 UEFI firmware is missing: $firmware"
command -v lsof >/dev/null || fail 'lsof is required to check disk and port use.'
check_unused 'builder disk' -t -- "$disk"
check_unused 'SSH port 22022' -nP -iTCP:22022 -sTCP:LISTEN -t

# A stale PID file may remain after a shutdown. No live process is removed.
if [ -e "$pidfile" ]; then
  rm -- "$pidfile"
fi
mkdir -p "$root/logs"
check_directory "$root/logs"
if [ -e "$root/logs/builder-console.log" ] || [ -L "$root/logs/builder-console.log" ]; then
  check_file "$root/logs/builder-console.log"
fi
cd "$root"
"$qemu_bin" \
  -machine virt,accel=hvf -cpu host -smp 4 -m 6144 \
  -bios "$firmware" \
  -drive 'if=virtio,format=qcow2,file=builder/Whonix ARM64 Builder.utm/Data/builder.qcow2' \
  -drive 'if=virtio,format=raw,readonly=on,file=builder/Whonix ARM64 Builder.utm/Data/seed.iso' \
  -netdev user,id=builder,hostfwd=tcp:127.0.0.1:22022-:22 \
  -device virtio-net-pci,netdev=builder,mac=52:54:00:ab:01:01 \
  -device virtio-rng-pci -display none \
  -serial file:logs/builder-console.log \
  -daemonize -pidfile builder/qemu.pid
printf 'Builder started (PID %s); SSH will be available on 127.0.0.1:22022.\n' "$(cat "$pidfile")"
printf 'Check build progress with %s/scripts/build-status.sh\n' "$root"
