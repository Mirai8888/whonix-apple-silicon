#!/usr/bin/python3
"""Temporary, one-boot Linux ARM64 benchmark; no listeners or desktop control.

Install as root with a Type=simple service. Create root-owned regular files
/var/lib/whonix-arm64-benchmark/enabled and variant (control or fixed).
The marker is consumed once. After that point every exit attempts poweroff.
"""

import base64
import json
import math
import os
from pathlib import Path
import platform
import select
import stat
import statistics
import subprocess
import sys
import tempfile
import time


STATE = Path("/var/lib/whonix-arm64-benchmark")
SERIAL = Path("/dev/ttyAMA0")
SYSTEMCTL = "/usr/bin/systemctl"
WARMUP_SECONDS = 40
IDLE_SECONDS = 60
END_GUARD_SECONDS = 5
PROPERTIES = (
    "MainPID", "Result", "NRestarts", "ActiveState", "SubState",
    "ExecMainCode", "ExecMainStatus",
)
CPU_FIELDS = (
    "user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal",
    "guest", "guest_nice",
)
MEMORY_FIELDS = {
    "MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SwapCached",
    "Active", "Inactive", "SwapTotal", "SwapFree", "Dirty", "Writeback",
    "Slab", "SReclaimable", "PageTables", "KernelStack",
}


def require_private_path(path, directory=False):
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError(f"Expected root-owned, non-writable-by-others {'directory' if directory else 'file'}: {path}")
    if not directory and info.st_nlink != 1:
        raise ValueError(f"Unexpected hard links: {path}")
    return info


def claim_run():
    if platform.system() != "Linux" or platform.machine() != "aarch64" or os.geteuid() != 0:
        raise RuntimeError("This temporary benchmark requires root on Linux aarch64.")
    for path in (Path("/var"), Path("/var/lib"), STATE):
        require_private_path(path, directory=True)
    marker = STATE / "enabled"
    if not marker.exists() and not marker.is_symlink():
        return None
    require_private_path(marker)
    variant_path = STATE / "variant"
    if require_private_path(variant_path).st_size > 32:
        raise ValueError("Variant file is too large.")
    variant = variant_path.read_text(encoding="ascii").strip()
    if variant not in ("control", "fixed"):
        raise ValueError("Variant must be control or fixed.")
    marker.unlink()
    return variant


def run_command(argv, timeout=10):
    return subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout, check=False,
        env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"},
    )


def sleep_until(deadline):
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(remaining)


def serial_line(line):
    """Write one bounded, newline-delimited record without opening a console UI."""
    descriptor = os.open(SERIAL, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        if not stat.S_ISCHR(os.fstat(descriptor).st_mode):
            raise ValueError("Serial output is not a character device.")
        pending = memoryview((line + "\n").encode("ascii"))
        deadline = time.monotonic() + 10
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


def write_json(name, document):
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    descriptor, temporary = tempfile.mkstemp(prefix=".benchmark-", dir=STATE)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, STATE / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return payload


def snapshot():
    result = run_command([
        SYSTEMCTL, "show", "kloak.service", "--no-pager",
        "--property=" + ",".join(PROPERTIES),
    ])
    if result.returncode:
        raise RuntimeError(f"Kloak status failed ({result.returncode}): {result.stderr.strip()}")
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if any(name not in properties for name in PROPERTIES):
        raise ValueError("Incomplete Kloak status.")
    for name in ("MainPID", "NRestarts", "ExecMainCode", "ExecMainStatus"):
        properties[name] = int(properties[name])
    cpu = {}
    counters = {}
    for line in Path("/proc/stat").read_text().splitlines():
        fields = line.split()
        if fields[0] == "cpu" or (fields[0].startswith("cpu") and fields[0][3:].isdigit()):
            cpu[fields[0]] = dict(zip(CPU_FIELDS, map(int, fields[1:])))
        elif fields[0] in ("ctxt", "processes", "procs_running", "procs_blocked"):
            counters[fields[0]] = int(fields[1])
    if "cpu" not in cpu:
        raise ValueError("Missing aggregate CPU counters.")
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        fields = value.split()
        if name in MEMORY_FIELDS and len(fields) == 2 and fields[1] == "kB":
            memory[name] = int(fields[0])
    return {"monotonic_seconds": time.monotonic(), "kloak": properties,
            "cpu_ticks": cpu, "proc_counters": counters, "memory_kib": memory}


def expectations(variant, before, after):
    first, last = before["kloak"], after["kloak"]
    if variant == "fixed":
        checks = {
            "running_both_samples": all(s["ActiveState"] == "active" and s["SubState"] == "running" for s in (first, last)),
            "same_nonzero_pid": first["MainPID"] > 0 and first["MainPID"] == last["MainPID"],
            "zero_restarts": first["NRestarts"] == last["NRestarts"] == 0,
        }
    else:
        checks = {"restarts_increased": last["NRestarts"] > first["NRestarts"]}
    return {"passed": all(checks.values()), "checks": checks}


def cpu_delta(before, after):
    first, last = before["cpu_ticks"]["cpu"], after["cpu_ticks"]["cpu"]
    delta = {name: last[name] - first[name] for name in CPU_FIELDS if name in first and name in last}
    if any(value < 0 for value in delta.values()):
        raise ValueError("CPU counters decreased during the sample.")
    # Linux includes guest time in user/nice already; do not count it twice.
    total = sum(delta.get(name, 0) for name in CPU_FIELDS[:8])
    idle = delta.get("idle", 0) + delta.get("iowait", 0)
    return {"ticks": delta, "total_ticks": total,
            "busy_percent": 100 * (total - idle) / total if total else None}


def crypto_benchmark():
    program = STATE / "benchmark-arm64-crypto.py"
    if not program.exists() and not program.is_symlink():
        return None
    require_private_path(program)
    result = run_command(["/usr/bin/python3", "-I", str(program)], timeout=40)
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise ValueError("Crypto benchmark did not return a JSON object.")
    if result.returncode:
        parsed["status"] = "error"
        parsed["runner_exit_code"] = result.returncode
    if result.stderr:
        parsed["runner_stderr"] = result.stderr
    write_json("crypto.json", parsed)
    return parsed


def pixman_benchmark():
    program = STATE / "pixman-composite-benchmark"
    if not program.exists() and not program.is_symlink():
        return None
    require_private_path(program)
    samples = []
    for repetition in range(3):
        modes = ("auto", "neon-disabled") if repetition % 2 == 0 else ("neon-disabled", "auto")
        for mode in modes:
            environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}
            if mode == "neon-disabled":
                environment["PIXMAN_DISABLE"] = "arm-neon"
            result = subprocess.run([str(program)], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=5,
                                    env=environment, check=True)
            sample = json.loads(result.stdout)
            if (sample.get("width") != 1920 or sample.get("height") != 1080 or
                    sample.get("checksum_rgb_fnv1a64") != "457c95b0871b8af3" or
                    sample.get("pixman_version") != "0.44.0"):
                raise ValueError("Pixman benchmark output/version did not match the fixed workload")
            rate = sample.get("pixels_per_second")
            if not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate <= 0:
                raise ValueError("Invalid Pixman throughput")
            sample.update({"mode": mode, "repetition": repetition + 1,
                           "diagnostics": result.stderr})
            samples.append(sample)
    medians = {mode: statistics.median(s["pixels_per_second"] for s in samples if s["mode"] == mode)
               for mode in ("auto", "neon-disabled")}
    report = {"status": "ok", "samples": samples, "median_pixels_per_second": medians,
              "auto_over_neon_disabled_ratio": medians["auto"] / medians["neon-disabled"],
              "limits": "Synthetic 1080p Pixman OVER plus destination reset; not desktop FPS. "
              "Only the dedicated arm-neon backend is disabled in comparison children; other SIMD may remain."}
    write_json("pixman.json", report)
    return report


def main():
    try:
        variant = claim_run()
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    if variant is None:
        return 0

    report = {"schema_version": 2, "variant": variant, "status": "error",
              "warmup_seconds": WARMUP_SECONDS, "requested_idle_seconds": IDLE_SECONDS,
              "end_guard_seconds": END_GUARD_SECONDS,
              "architecture": platform.machine(), "kernel": platform.release(), "errors": []}
    exit_code = 1
    try:
        features = [line.split(":", 1)[1].split() for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("Features") and ":" in line]
        report["cpu_features"] = sorted(set(features[0])) if features else []
        report["cpu_count"] = os.cpu_count()
        report["clock_ticks_per_second"] = os.sysconf("SC_CLK_TCK")
        sleep_until(time.monotonic() + WARMUP_SECONDS)
        report["before"] = snapshot()
        serial_line("WHONIX_BENCH_BEGIN")
        started = time.monotonic()
        sleep_until(started + IDLE_SECONDS)
        elapsed = time.monotonic() - started
        report["elapsed_seconds"] = elapsed
        report["after"] = snapshot()
        serial_line("WHONIX_BENCH_END")
        # Let the host's polling sampler observe END before CPU-heavy work,
        # report serialization or shutdown. This guard is identical in both variants.
        sleep_until(time.monotonic() + END_GUARD_SECONDS)
        report["snapshot_elapsed_seconds"] = report["after"]["monotonic_seconds"] - report["before"]["monotonic_seconds"]
        report["cpu_delta"] = cpu_delta(report["before"], report["after"])
        report["expectations"] = expectations(variant, report["before"], report["after"])
        if variant == "fixed":
            report["crypto"] = crypto_benchmark()
            if report["crypto"] is not None and report["crypto"].get("status") != "ok":
                raise RuntimeError("Crypto benchmark did not pass.")
            report["pixman"] = pixman_benchmark()
        if not report["expectations"]["passed"]:
            raise RuntimeError("Variant expectations did not pass.")
        report["status"] = "ok"
        exit_code = 0
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        try:
            payload = write_json("report.json", report)
            serial_line("WHONIX_BENCH_REPORT=" + base64.b64encode(payload.encode("utf-8")).decode("ascii"))
        except Exception as error:
            print(f"Report delivery failed: {error}", file=sys.stderr)
            exit_code = 1
        finally:
            try:
                result = run_command([SYSTEMCTL, "poweroff"])
                if result.returncode:
                    print(f"Poweroff request failed: {result.stderr.strip()}", file=sys.stderr)
                    exit_code = 1
            except Exception as error:
                print(f"Poweroff request failed: {error}", file=sys.stderr)
                exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
