#!/usr/bin/python3
"""Run a bounded, synthetic OpenSSL buffer benchmark on Linux AArch64.

No arguments are required. One JSON document is written to stdout; no files,
network connections, GUI actions, or persistent environment changes are made.
"""

import argparse
import datetime
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import statistics
import subprocess
import sys
import time


ALGORITHMS = ("aes-256-gcm", "sha256")
MODES = ("auto", "armcap-disabled")
BUFFER_BYTES = 16384
REPETITIONS = 3
CHILD_TIMEOUT_SECONDS = 15
TOTAL_TIMEOUT_SECONDS = 30
LIMITS = (
    "Synthetic 16 KiB in-memory OpenSSL buffer throughput, not Tor throughput, "
    "application performance, or native-performance parity. Auto mode uses "
    "OpenSSL capability detection; armcap-disabled sets OPENSSL_armcap=0 only "
    "in that child. Compiler/library fallback code may still use SIMD. "
    "Three short repetitions do not eliminate scheduling noise, thermal effects, "
    "or workload differences; ratios apply only to this run."
)


class BenchmarkError(RuntimeError):
    """The requested benchmark could not produce a valid measurement."""


def require_platform():
    if platform.system() != "Linux" or platform.machine().lower() != "aarch64":
        raise BenchmarkError("This benchmark requires Linux on aarch64")


def parse_cpu_features(cpuinfo):
    features = []
    for line in cpuinfo.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "Features":
            normalized = " ".join(value.split())
            if normalized and normalized not in features:
                features.append(normalized)
    if not features:
        raise BenchmarkError("/proc/cpuinfo contains no Features entries")
    return features


def child_environment(mode):
    environment = os.environ.copy()
    if mode == "auto":
        environment.pop("OPENSSL_armcap", None)
    elif mode == "armcap-disabled":
        environment["OPENSSL_armcap"] = "0"
    else:
        raise BenchmarkError("Unexpected benchmark mode")
    return environment


def run_openssl(executable, arguments, mode, deadline):
    started = time.monotonic()
    remaining = deadline - started
    if remaining <= 0:
        raise BenchmarkError("Benchmark exceeded its 30-second total time limit")
    try:
        result = subprocess.run(
            [executable, *arguments],
            env=child_environment(mode),
            capture_output=True,
            text=True,
            check=False,
            timeout=min(CHILD_TIMEOUT_SECONDS, remaining),
        )
    except subprocess.TimeoutExpired as error:
        raise BenchmarkError("OpenSSL command timed out within the benchmark time limit") from error
    elapsed = time.monotonic() - started
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[:2000]
        raise BenchmarkError(f"OpenSSL exited {result.returncode}: {detail}")
    if time.monotonic() > deadline:
        raise BenchmarkError("Benchmark exceeded its 30-second total time limit")
    return {"stdout": result.stdout, "stderr": result.stderr, "elapsed_seconds": elapsed}


def check_speed_help(help_text):
    required = ("mr", "elapsed", "seconds", "bytes", "evp")
    missing = [flag for flag in required if not re.search(r"(?m)^\s*-" + flag + r"(?:\s|$)", help_text)]
    if missing:
        raise BenchmarkError("OpenSSL speed lacks required options: " + ", ".join("-" + flag for flag in missing))


def parse_speed_output(stdout, algorithm):
    if algorithm not in ALGORITHMS:
        raise BenchmarkError("Unexpected benchmark algorithm")
    headers = []
    results = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("+H:"):
            headers.append(line.split(":"))
        elif line.startswith("+F:"):
            results.append(line.split(":"))
    if headers != [["+H", str(BUFFER_BYTES)]]:
        raise BenchmarkError("OpenSSL output must have one 16384-byte buffer header")
    if len(results) != 1:
        raise BenchmarkError("OpenSSL output must have exactly one throughput result")
    fields = results[0]
    if len(fields) != 4 or not re.fullmatch(r"\d+", fields[1]) or fields[2].lower() != algorithm:
        raise BenchmarkError("OpenSSL output has an unexpected algorithm or result shape")
    try:
        throughput = float(fields[3])
    except ValueError as error:
        raise BenchmarkError("OpenSSL throughput is not numeric") from error
    if not math.isfinite(throughput) or throughput <= 0:
        raise BenchmarkError("OpenSSL throughput must be finite and positive")
    return throughput


def benchmark_plan():
    for algorithm in ALGORITHMS:
        for repetition in range(1, REPETITIONS + 1):
            order = MODES if repetition % 2 else tuple(reversed(MODES))
            for mode in order:
                yield algorithm, repetition, mode


def benchmark():
    require_platform()
    started = time.monotonic()
    deadline = started + TOTAL_TIMEOUT_SECONDS
    started_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    features = parse_cpu_features(Path("/proc/cpuinfo").read_text())
    executable = shutil.which("openssl")
    if not executable:
        raise BenchmarkError("OpenSSL is not installed")
    version = run_openssl(executable, ["version"], "auto", deadline)
    if not version["stdout"].strip().startswith("OpenSSL "):
        raise BenchmarkError("The installed program is not OpenSSL")
    help_result = run_openssl(executable, ["speed", "-help"], "auto", deadline)
    check_speed_help(help_result["stdout"] + "\n" + help_result["stderr"])
    samples = []
    for algorithm, repetition, mode in benchmark_plan():
        arguments = [
            "speed", "-mr", "-elapsed", "-seconds", "1", "-bytes",
            str(BUFFER_BYTES), "-evp", algorithm,
        ]
        result = run_openssl(executable, arguments, mode, deadline)
        throughput = parse_speed_output(result["stdout"], algorithm)
        samples.append({
            "algorithm": algorithm,
            "repetition": repetition,
            "mode": mode,
            "OPENSSL_armcap": None if mode == "auto" else "0",
            "arguments": arguments,
            "bytes_per_second": throughput,
            **result,
        })
    summary = {}
    for algorithm in ALGORITHMS:
        medians = {
            mode: statistics.median(sample["bytes_per_second"] for sample in samples
                                    if sample["algorithm"] == algorithm and sample["mode"] == mode)
            for mode in MODES
        }
        ratio = medians["auto"] / medians["armcap-disabled"]
        if not math.isfinite(ratio):
            raise BenchmarkError("Throughput ratio is not finite")
        summary[algorithm] = {
            "median_bytes_per_second": medians,
            "auto_over_armcap_disabled_ratio": ratio,
        }
    return {
        "status": "ok",
        "started_utc": started_utc,
        "duration_seconds": time.monotonic() - started,
        "platform": "Linux aarch64",
        "openssl_path": executable,
        "openssl_version": version["stdout"].strip(),
        "openssl_version_stderr": version["stderr"],
        "cpuinfo_features": features,
        "buffer_bytes": BUFFER_BYTES,
        "seconds_per_sample": 1,
        "repetitions_per_mode": REPETITIONS,
        "samples": samples,
        "summary": summary,
        "limits": LIMITS,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        result = benchmark()
    except (BenchmarkError, OSError) as error:
        print(json.dumps({"status": "error", "error": str(error), "limits": LIMITS}, allow_nan=False))
        return 1
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
