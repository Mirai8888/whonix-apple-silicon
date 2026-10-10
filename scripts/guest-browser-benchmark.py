#!/usr/bin/python3
"""One-boot browser measurement for disposable Linux ARM64 Workstation clones.

Install this script and an empty enabled marker in root:root STATE (0700).
Install benchmarks/browser-local.html as user: user 0600 at PAGE, with its bench
directory 0700. No benchmark output may already exist. No browser preferences,
debugging listeners, network configuration, or sandbox settings are changed.
The trusted fixed command alone skips the launcher's link confirmation dialog.
"""

import base64
import grp
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import select
import signal
import stat
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import zlib


STATE = Path("/var/lib/whonix-arm64-browser-benchmark")
BENCH = Path("/home/user/.tb/tor-browser/Browser/Downloads/bench")
PAGE = BENCH / "local.html"
SERIAL = Path("/dev/ttyAMA0")
WARMUP_SECONDS = 40
LAUNCH_TIMEOUT_SECONDS = 60
SAMPLE_SECONDS = 0.25
MAX_PNG_BYTES = 20 * 1024 * 1024
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_REPORT_BYTES = 128 * 1024
VMSTAT_FIELDS = {"pswpin", "pswpout", "pgfault", "pgmajfault", "oom_kill",
                 "pgscan_kswapd", "pgscan_direct", "pgsteal_kswapd", "pgsteal_direct"}
SHELL = """set -eu
umask 077
. /etc/profile.d/20_torbrowser.sh
export open_link_confirmation_skip=true
exec /usr/bin/torbrowser --headless --screenshot "$1" "$2"
"""


def require_path(path, owner=0, directory=False, private=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    forbidden = 0o077 if private else 0o022
    if not kind(info.st_mode) or info.st_uid != owner or info.st_mode & forbidden:
        raise ValueError(f"Unsafe benchmark path: {path}")
    if not directory and info.st_nlink != 1:
        raise ValueError(f"Unexpected hard links: {path}")
    return info


def claim_run():
    if platform.system() != "Linux" or platform.machine() != "aarch64" or os.geteuid() != 0:
        raise RuntimeError("Requires root on Linux aarch64 in a disposable benchmark clone.")
    for path in (Path("/var"), Path("/var/lib")):
        require_path(path, directory=True)
    require_path(STATE, directory=True, private=True)
    marker = STATE / "enabled"
    if not marker.exists() and not marker.is_symlink():
        return False
    if require_path(marker, private=True).st_size != 0:
        raise ValueError("Enabled marker must be empty.")
    if os.cpu_count() not in (2, 4):
        raise ValueError("Benchmark accepts exactly two or four vCPUs.")
    marker.unlink()
    return True


def browser_user():
    account = pwd.getpwnam("user")
    if account.pw_uid <= 0 or account.pw_dir != "/home/user":
        raise ValueError("Expected an unprivileged user with /home/user.")
    validate_private_group(account)
    require_path(Path("/home"), directory=True)
    for path in reversed(BENCH.parents):
        if str(path).startswith("/home/user"):
            require_browser_directory(path, account)
    require_path(BENCH, owner=account.pw_uid, directory=True, private=True)
    info = require_path(PAGE, owner=account.pw_uid)
    if not 1 <= info.st_size <= 128 * 1024:
        raise ValueError("Unexpected local workload size.")
    # A prior browser could accept the launch remotely and invalidate timing.
    for candidate in Path("/proc").iterdir():
        if candidate.name.isdigit():
            try:
                if candidate.stat().st_uid == account.pw_uid:
                    name = (candidate / "comm").read_text().strip()
                    if name in ("firefox", "firefox-bin", "tor-browser"):
                        raise RuntimeError("A browser is already running before the benchmark.")
            except (FileNotFoundError, ProcessLookupError):
                pass
    return account


def validate_private_group(account):
    group = grp.getgrgid(account.pw_gid)
    if any(member != account.pw_name for member in group.gr_mem):
        raise ValueError("Browser user's primary group has other supplementary members.")
    if any(other.pw_gid == account.pw_gid and other.pw_uid != account.pw_uid
           for other in pwd.getpwall()):
        raise ValueError("Browser user's primary group is shared with another account.")


def require_browser_directory(path, account):
    # Whonix normally makes ~/.tb group-writable to the user's private group.
    # validate_private_group() must pass before accepting that existing mode.
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != account.pw_uid
            or info.st_mode & 0o002
            or (info.st_mode & 0o020 and info.st_gid != account.pw_gid)):
        raise ValueError(f"Unsafe browser directory: {path}")
    return info


def read_counters():
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        fields = value.split()
        if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            if len(fields) != 2 or fields[1] != "kB":
                raise ValueError("Unexpected memory counter unit.")
            memory[key] = int(fields[0])
    if "MemAvailable" not in memory:
        raise ValueError("Missing MemAvailable.")
    vmstat = {}
    for line in Path("/proc/vmstat").read_text().splitlines():
        key, value = line.split()
        if key in VMSTAT_FIELDS:
            vmstat[key] = int(value)
    pressure = {}
    for kind in ("cpu", "memory", "io"):
        path = Path("/proc/pressure") / kind
        if path.exists():
            pressure[kind] = {}
            for line in path.read_text().splitlines():
                fields = line.split()
                totals = dict(field.split("=", 1) for field in fields[1:])
                pressure[kind][fields[0]] = int(totals["total"])
    return {"monotonic_seconds": time.monotonic(), "memory_kib": memory,
            "vmstat": vmstat, "pressure_total_microseconds": pressure}


def counter_delta(before, after):
    def subtract(first, last):
        result = {key: last[key] - first[key] for key in first.keys() & last.keys()}
        if any(value < 0 for value in result.values()):
            raise ValueError("A cumulative counter decreased.")
        return result
    return {"vmstat": subtract(before["vmstat"], after["vmstat"]),
            "pressure_total_microseconds": {
                key: subtract(before["pressure_total_microseconds"][key],
                              after["pressure_total_microseconds"][key])
                for key in before["pressure_total_microseconds"].keys() &
                after["pressure_total_microseconds"].keys()}}


def read_png(path, uid):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or info.st_mode & 0o077 or not 45 <= info.st_size <= MAX_PNG_BYTES):
            raise ValueError("Unsafe or oversized screenshot output.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(MAX_PNG_BYTES + 1)
        if len(data) > MAX_PNG_BYTES or len(data) != info.st_size:
            raise ValueError("Screenshot changed or exceeded the size bound.")
    finally:
        os.close(descriptor)
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Screenshot is not PNG.")
    offset, dimensions, saw_data, saw_end = 8, None, False, False
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("Truncated PNG chunk.")
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        end = offset + 12 + size
        if end > len(data):
            raise ValueError("Truncated PNG payload.")
        kind = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + size]
        crc = struct.unpack(">I", data[end - 4:end])[0]
        if zlib.crc32(kind + body) & 0xffffffff != crc:
            raise ValueError("PNG CRC mismatch.")
        if offset == 8:
            if kind != b"IHDR" or size != 13:
                raise ValueError("Missing PNG IHDR.")
            width, height = struct.unpack(">II", body[:8])
            if not 64 <= width <= 4096 or not 64 <= height <= 4096:
                raise ValueError("Unexpected screenshot dimensions.")
            dimensions = [width, height]
        elif kind == b"IHDR":
            raise ValueError("Duplicate PNG IHDR.")
        if kind == b"IDAT" and size:
            saw_data = True
        if kind == b"IEND":
            if size != 0 or end != len(data):
                raise ValueError("Invalid PNG end.")
            saw_end = True
        offset = end
    if not dimensions or not saw_data or not saw_end:
        raise ValueError("Incomplete PNG screenshot.")
    return {"file": str(path), "bytes": len(data), "dimensions": dimensions,
            "sha256": hashlib.sha256(data).hexdigest(),
            "workload_content_validated": False,
            "content_validation_required": "Inspect PASS checksum=e5f37d05 cards=4000; PNG validity alone is insufficient."}


def workload_hash(uid):
    descriptor = os.open(PAGE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or info.st_mode & 0o022 or not 1 <= info.st_size <= 128 * 1024):
            raise ValueError("Unsafe or oversized workload input.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(128 * 1024 + 1)
        if len(data) != info.st_size or len(data) > 128 * 1024:
            raise ValueError("Workload input changed or exceeded the size bound.")
        return hashlib.sha256(data).hexdigest()
    finally:
        os.close(descriptor)


def log_file(name):
    descriptor = os.open(STATE / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    return os.fdopen(descriptor, "wb", buffering=0)


def terminate_group(process):
    # Only the new session created for this exact benchmark child is signalled.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)


def launch(index, uid):
    label = "cold" if index == 1 else "warm"
    name = f"launch-{index:02d}-{label}"
    output = BENCH / (name + ".png")
    if output.exists() or output.is_symlink():
        raise ValueError("Refusing an existing screenshot path.")
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/home/user",
                   "USER": "user", "LOGNAME": "user", "LANG": "C.UTF-8",
                   "XDG_RUNTIME_DIR": f"/run/user/{uid}",
                   "WAYLAND_DISPLAY": "wayland-0",
                   "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{uid}/bus"}
    argv = ["/usr/sbin/runuser", "-u", "user", "--", "/bin/bash", "--noprofile", "--norc",
            "-c", SHELL, "browser-benchmark", str(output), PAGE.as_uri()]
    before = read_counters()
    minimum = before["memory_kib"]["MemAvailable"]
    samples = 1
    timed_out = False
    log_limit_exceeded = False
    process = None
    pidfd = None
    with log_file(name + ".stdout.log") as stdout, log_file(name + ".stderr.log") as stderr:
        started = time.monotonic()
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                       env=environment, cwd=BENCH, start_new_session=True)
            # Linux pidfds wake on this exact child's exit. Python's POSIX
            # wait(timeout=...) polls internally and adds material quantization
            # to these short workloads; unsupported pidfds fail explicitly.
            pidfd = os.pidfd_open(process.pid, 0)
            while True:
                now = time.monotonic()
                if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > MAX_LOG_BYTES:
                    log_limit_exceeded = True
                    terminate_group(process)
                    break
                if now - started >= LAUNCH_TIMEOUT_SECONDS:
                    timed_out = True
                    terminate_group(process)
                    break
                sample = read_counters()
                minimum = min(minimum, sample["memory_kib"]["MemAvailable"])
                samples += 1
                interval = min(SAMPLE_SECONDS, max(0, started + LAUNCH_TIMEOUT_SECONDS - time.monotonic()))
                if select.select([pidfd], [], [], interval)[0]:
                    # Record readiness before the reap and output validation.
                    ended = time.monotonic()
                    process.wait()
                    break
            if timed_out or log_limit_exceeded:
                ended = time.monotonic()
            elapsed = ended - started
            log_limit_exceeded |= max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > MAX_LOG_BYTES
        finally:
            try:
                if process is not None and process.poll() is None:
                    terminate_group(process)
            finally:
                if pidfd is not None:
                    os.close(pidfd)
    after = read_counters()
    minimum = min(minimum, after["memory_kib"]["MemAvailable"])
    result = {"index": index, "kind": label, "duration_seconds": elapsed,
              "returncode": process.returncode, "timed_out": timed_out,
              "log_limit_exceeded": log_limit_exceeded,
              "minimum_mem_available_kib": minimum, "counter_samples": samples + 1,
              "before": before, "after": after, "counter_delta": counter_delta(before, after),
              "stdout_log": name + ".stdout.log", "stderr_log": name + ".stderr.log",
              "status": "error"}
    if log_limit_exceeded:
        result["error"] = "Browser output log exceeded its size limit."
    elif timed_out:
        result["error"] = "Browser launch exceeded its timeout."
    elif process.returncode:
        result["error"] = "Browser launcher exited unsuccessfully."
    else:
        try:
            result["screenshot"] = read_png(output, uid)
            result["status"] = "captured-unvalidated"
        except (OSError, ValueError) as error:
            result["error"] = str(error)
    return result


def summary(launches):
    if len(launches) != 4 or any(item["status"] != "captured-unvalidated" for item in launches):
        raise ValueError("All four launches must complete with valid PNG output.")
    return {"cold_start_seconds": launches[0]["duration_seconds"],
            "warm_median_seconds": statistics.median(item["duration_seconds"] for item in launches[1:]),
            "minimum_mem_available_kib": min(item["minimum_mem_available_kib"] for item in launches),
            "workload_content_validated": False}


def write_json(document):
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(payload.encode("utf-8")) > MAX_REPORT_BYTES:
        raise ValueError("Report exceeds the serial bound.")
    descriptor, temporary = tempfile.mkstemp(prefix=".browser-report-", dir=STATE)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, STATE / "report.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return payload


def serial_line(line):
    encoded = (line + "\n").encode("ascii")
    if len(encoded) > MAX_REPORT_BYTES * 2:
        raise ValueError("Serial line too large.")
    descriptor = os.open(SERIAL, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISCHR(os.fstat(descriptor).st_mode):
            raise ValueError("Serial output is not a character device.")
        pending, deadline = memoryview(encoded), time.monotonic() + 15
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [descriptor], [], remaining)[1]:
                raise TimeoutError("Serial write timed out.")
            try:
                written = os.write(descriptor, pending)
            except (BlockingIOError, InterruptedError):
                continue
            if written <= 0:
                raise OSError("Serial write made no progress.")
            pending = pending[written:]
    finally:
        os.close(descriptor)


def poweroff():
    result = subprocess.run(["/usr/bin/systemctl", "poweroff"], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}, check=False)
    if result.returncode:
        raise RuntimeError("Poweroff request failed.")


def main():
    try:
        claimed = claim_run()
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    if not claimed:
        return 0
    os.umask(0o077)
    report = {"schema_version": 1, "status": "error", "cpu_count": os.cpu_count(),
              "architecture": platform.machine(), "kernel": platform.release(),
              "warmup_seconds": WARMUP_SECONDS, "per_launch_timeout_seconds": LAUNCH_TIMEOUT_SECONDS,
              "sample_interval_seconds": SAMPLE_SECONDS, "launches": [], "errors": [],
              "completion_timer": "monotonic clock with Linux pidfd/select exit notification",
              "limits": "Headless launch, synchronous local JS/layout and PNG encoding; not UTM GUI or Tor throughput. Content requires screenshot validation. Memory samples at 250ms can miss shorter peaks."}
    exit_code = 1
    try:
        account = browser_user()
        report["workload_sha256"] = workload_hash(account.pw_uid)
        time.sleep(WARMUP_SECONDS)
        for index in range(1, 5):
            serial_line(f"WHONIX_BROWSER_LAUNCH_BEGIN={index}")
            result = launch(index, account.pw_uid)
            report["launches"].append(result)
            serial_line(f"WHONIX_BROWSER_LAUNCH_END={index}")
            if result["status"] != "captured-unvalidated":
                raise RuntimeError(f"Launch {index} failed: {result.get('error', 'unknown failure')}")
        report["summary"] = summary(report["launches"])
        report["status"] = "captured-unvalidated"
        exit_code = 0
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        try:
            payload = write_json(report)
            serial_line("WHONIX_BROWSER_REPORT=" + base64.b64encode(payload.encode("utf-8")).decode("ascii"))
        except Exception as error:
            print(f"Report delivery failed: {error}", file=sys.stderr)
            exit_code = 1
        finally:
            try:
                poweroff()
            except Exception as error:
                print(f"Poweroff failed: {error}", file=sys.stderr)
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
