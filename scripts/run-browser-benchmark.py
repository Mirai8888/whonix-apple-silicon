#!/usr/bin/python3
"""Compare four versus two vCPUs on disposable native ARM64 browser guests.

The prepared template must contain the one-boot guest benchmark. This runner
never opens UTM or boots either imported disk. --smoke runs one four-vCPU clone.
All disk copies and logs are retained, and existing trial names are refused.
"""
import argparse
import base64
import binascii
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import plistlib
import re
import shlex
import stat
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
DISKS = ROOT / "artifacts/benchmarks/browser-cpu"
LOGS = ROOT / "logs/browser-cpu"
SMOKE_LOGS = ROOT / "logs/browser-cpu-smoke"
UTM = "/Applications/UTM.app/Contents/MacOS/utmctl"
QEMU = "/opt/homebrew/bin/qemu-system-aarch64"
DEADLINE_SECONDS = 360
POLL_SECONDS = 0.5
REPORT_PREFIX = b"WHONIX_BROWSER_REPORT="


def load_helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


HEADLESS = load_helper("browser_headless_helpers", "run-headless-benchmark.py")
METRICS = HEADLESS.METRICS
PREPARE = load_helper("browser_bundle_validation", "prepare_utm.py")
read_console = HEADLESS.read_console


def require_private_directory(path):
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
            stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError("Expected a real caller-owned directory with mode 0700: " + str(path))
    for parent in path.resolve().parents:
        metadata = parent.stat()
        sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
        if metadata.st_uid not in (0, os.geteuid()) or (metadata.st_mode & 0o022 and not sticky_root):
            raise RuntimeError("Unsafe benchmark directory ancestor: " + str(parent))


def require_unused(path):
    PREPARE.trusted_metadata(path, "Benchmark disk")
    result = subprocess.run(["/usr/sbin/lsof", "-t", "--", str(path)],
                            capture_output=True, text=True, timeout=10)
    if result.returncode != 1 or result.stdout or result.stderr:
        raise RuntimeError("Disk is open or its use could not be checked: " + str(path))


def clone_trial_disk(source, destination):
    require_private_directory(DISKS)
    require_unused(source)
    # Reuse the audited APFS-clone/private-mode implementation in an isolated
    # module instance. Its path constant is local to this runner's import.
    HEADLESS.DISKS = DISKS
    HEADLESS.clone_trial_disk(source, destination)


def validate_listener(listener, command, gateway_uuid):
    pids = re.findall(r"^p(\d+)$", listener, re.M)
    endpoints = re.findall(r"^n(.+)$", listener, re.M)
    if len(pids) != 1 or endpoints != ["127.0.0.1:8010"]:
        raise RuntimeError("Expected a single Gateway loopback listener")
    tokens = shlex.split(command)
    if (not tokens or Path(tokens[0]).name != "QEMULauncher" or
            tokens.count("-uuid") != 1):
        raise RuntimeError("Port 8010 is not owned by this UTM Gateway")
    index = tokens.index("-uuid")
    if index + 1 >= len(tokens) or tokens[index + 1].upper() != gateway_uuid.upper():
        raise RuntimeError("Port 8010 is not owned by this Gateway")


def validate_environment():
    if os.uname().sysname != "Darwin" or os.uname().machine != "arm64":
        raise RuntimeError("This runner requires native Apple Silicon macOS")
    require_private_directory(DISKS)
    identifiers = {}
    for role in ("Gateway", "Workstation"):
        bundle = ROOT / "artifacts/utm" / ("Whonix-" + role + "-LXQt.utm")
        PREPARE.process_bundle(bundle, check=True)
        with (bundle / "config.plist").open("rb") as stream:
            identifiers[role] = plistlib.load(stream)["Information"]["UUID"]
    if identifiers["Gateway"].upper() == identifiers["Workstation"].upper():
        raise RuntimeError("Gateway and Workstation UUIDs must differ")
    for role, required in (("Gateway", "started"), ("Workstation", "stopped")):
        state = subprocess.check_output([UTM, "status", identifiers[role]], text=True, timeout=10).strip()
        if state != required:
            raise RuntimeError(role + " must be " + required + "; no VM state was changed")
    listener = subprocess.check_output(
        ["/usr/sbin/lsof", "-nP", "-iTCP:8010", "-sTCP:LISTEN", "-Fpn"], text=True, timeout=10)
    pids = re.findall(r"^p(\d+)$", listener, re.M)
    if len(pids) != 1:
        raise RuntimeError("Expected a single Gateway loopback listener")
    command = subprocess.check_output(["/bin/ps", "-ww", "-p", pids[0], "-o", "args="],
                                      text=True, timeout=10)
    validate_listener(listener, command, identifiers["Gateway"])
    require_unused(ROOT / "builder/Whonix ARM64 Builder.utm/Data/builder.qcow2")
    require_unused(DISKS / "template.raw")


def reject_constant(value):
    raise ValueError("Non-finite JSON number: " + value)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def reject_nonfinite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite JSON number")
    if isinstance(value, dict):
        for item in value.values():
            reject_nonfinite(item)
    elif isinstance(value, list):
        for item in value:
            reject_nonfinite(item)


def parse_report(content):
    # Serial output is untrusted data. Only a complete newline-terminated
    # report is accepted; its contents are never treated as commands or paths.
    lines = [line.rstrip(b"\r") for line in content.split(b"\n")[:-1]
             if line.startswith(REPORT_PREFIX)]
    if not lines:
        return None
    if len(lines) != 1:
        raise RuntimeError("Expected exactly one browser report")
    try:
        report = json.loads(base64.b64decode(lines[0][len(REPORT_PREFIX):], validate=True),
                            parse_constant=reject_constant, object_pairs_hook=unique_object)
        reject_nonfinite(report)
    except (ValueError, UnicodeError, binascii.Error, RecursionError) as error:
        raise RuntimeError("Malformed browser report") from error
    if not isinstance(report, dict):
        raise RuntimeError("Browser report must be an object")
    return report


def validate_report(report, cpu_count, workload_sha256=None):
    if report.get("status") != "captured-unvalidated":
        raise RuntimeError("Guest browser benchmark did not report successful captures")
    if report.get("schema_version") != 1 or report.get("architecture") != "aarch64":
        raise RuntimeError("Unsupported guest browser report schema or architecture")
    if type(report.get("cpu_count")) is not int or report["cpu_count"] != cpu_count:
        raise RuntimeError("Guest CPU count does not match the requested trial")
    if report.get("errors") != []:
        raise RuntimeError("Guest browser benchmark reported errors")
    if workload_sha256 is not None and report.get("workload_sha256") != workload_sha256:
        raise RuntimeError("Guest browser workload does not match the local source")
    launches = report.get("launches")
    if not isinstance(launches, list) or len(launches) != 4:
        raise RuntimeError("Expected four browser launches")
    for index, launch in enumerate(launches, 1):
        if (not isinstance(launch, dict) or launch.get("status") != "captured-unvalidated" or
                type(launch.get("returncode")) is not int or launch["returncode"] != 0 or
                launch.get("timed_out") is not False or launch.get("index") != index or
                launch.get("kind") != ("cold" if index == 1 else "warm")):
            raise RuntimeError("Every browser launch must capture successfully in order")
        duration = launch.get("duration_seconds")
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 65:
            raise RuntimeError("Invalid browser launch duration")
        screenshot = launch.get("screenshot")
        if (not isinstance(screenshot, dict) or
                screenshot.get("workload_content_validated") is not False or
                not isinstance(screenshot.get("sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", screenshot["sha256"]) or
                type(screenshot.get("bytes")) is not int or not 1 <= screenshot["bytes"] <= 20 * 1024 * 1024):
            raise RuntimeError("Invalid browser screenshot metadata")
        dimensions = screenshot.get("dimensions")
        if (not isinstance(dimensions, list) or len(dimensions) != 2 or
                any(type(value) is not int or not 1 <= value <= 8192 for value in dimensions)):
            raise RuntimeError("Invalid browser screenshot dimensions")


def qemu_arguments(disk, console, label, identifier, cpu_count):
    if type(cpu_count) is not int or cpu_count not in (2, 4):
        raise RuntimeError("Browser trials support only two or four vCPUs")
    return [QEMU, "-name", "Whonix-ARM64-browser-" + label, "-uuid", identifier,
            "-machine", "virt,accel=hvf", "-cpu", "host", "-smp", str(cpu_count), "-m", "4096",
            "-bios", "/opt/homebrew/share/qemu/edk2-aarch64-code.fd", "-nodefaults",
            "-display", "none", "-vga", "none", "-nic", "none", "-device", "ramfb",
            "-device", "virtio-gpu-pci", "-device", "nec-usb-xhci,id=usb-bus",
            "-device", "usb-tablet,bus=usb-bus.0", "-device", "usb-mouse,bus=usb-bus.0",
            "-device", "usb-kbd,bus=usb-bus.0", "-drive",
            "if=none,id=drive0,format=raw,file=" + str(disk) + ",discard=ignore,detect-zeroes=off",
            "-device", "virtio-blk-pci,drive=drive0,serial=0,bootindex=0",
            "-netdev", "socket,id=internal,connect=127.0.0.1:8010",
            "-device", "virtio-net-pci,netdev=internal", "-device", "virtio-rng-pci",
            "-serial", "file:" + str(console)]


def write_json_new(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")


def collect_process_metrics(before, after, peak_rss):
    if not before or not after or before["process_start"] != after["process_start"]:
        raise RuntimeError("Missing or inconsistent benchmark process identity")
    interval = after["sample_monotonic"] - before["sample_monotonic"]
    delta = after["cpu_seconds"] - before["cpu_seconds"]
    if not math.isfinite(interval) or interval <= 0 or not math.isfinite(delta) or delta < 0:
        raise RuntimeError("Invalid host process sampling interval")
    return {"host_before": before, "host_after": after, "host_interval_seconds": interval,
            "cpu_percent_one_core": delta / interval * 100,
            "sampled_peak_rss_kib": peak_rss}


def run_trial(label, cpu_count, log_directory):
    validate_environment()
    require_private_directory(log_directory)
    disk = DISKS / (label + ".raw")
    console = log_directory / (label + "-console.log")
    qemu_log = log_directory / (label + "-qemu.log")
    output = log_directory / (label + ".json")
    for path in (disk, console, qemu_log, output):
        if path.exists() or path.is_symlink():
            raise RuntimeError("Refusing to overwrite trial evidence: " + str(path))
    clone_trial_disk(DISKS / "template.raw", disk)
    args = qemu_arguments(disk, console, label, str(uuid.uuid4()).upper(), cpu_count)
    result = {"trial": label, "cpu_count": cpu_count, "memory_mib": 4096, "qemu_args": args,
              "qemu_version": subprocess.check_output([QEMU, "--version"], text=True, timeout=10).splitlines()[0],
              "workload_sha256": hashlib.sha256((ROOT / "benchmarks/browser-local.html").read_bytes()).hexdigest(),
              "host_memory_before": METRICS.host_memory()}
    # Reserve the serial path before QEMU opens it inside this private directory.
    with console.open("xb"):
        pass
    before = after = report = None
    peak_rss = 0
    print("Starting " + label + " (headless disposable clone)", flush=True)
    with qemu_log.open("xb") as errors:
        started = time.monotonic()
        process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=errors, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                if time.monotonic() - started >= DEADLINE_SECONDS:
                    raise RuntimeError("Guest did not finish within 360 seconds")
                parsed = parse_report(read_console(console))
                if parsed is not None and report is None:
                    report = parsed
                    result["boot_to_report_seconds"] = time.monotonic() - started
                try:
                    sample = METRICS.process_sample(process.pid)
                except (subprocess.CalledProcessError, IndexError):
                    if process.poll() is None:
                        raise
                else:
                    before = before or sample
                    after = sample
                    peak_rss = max(peak_rss, sample["rss_kib"])
                time.sleep(POLL_SECONDS)
            final_report = parse_report(read_console(console))
            if final_report is not None and report is None:
                result["boot_to_report_seconds"] = time.monotonic() - started
            report = final_report
            result["process_returncode"] = process.returncode
            result["boot_to_exit_seconds"] = time.monotonic() - started
            if process.returncode != 0 or report is None:
                raise RuntimeError("Incomplete browser benchmark; inspect retained logs")
            validate_report(report, cpu_count, result["workload_sha256"])
            result.update(collect_process_metrics(before, after, peak_rss))
            result["guest"] = report
        finally:
            # This handle names only our own disposable child, never a user VM.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
    result["host_memory_after"] = METRICS.host_memory()
    result["host_io_delta_pages"] = {
        key: result["host_memory_after"]["counts"][key] - result["host_memory_before"]["counts"][key]
        for key in ("Swapins", "Swapouts", "Pageouts")
        if key in result["host_memory_after"]["counts"] and key in result["host_memory_before"]["counts"]}
    write_json_new(output, result)
    print(label + ": four browser captures; screenshot content still requires validation", flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="run a single four-vCPU validation clone")
    args = parser.parse_args()
    os.umask(0o077)
    validate_environment()
    logs = SMOKE_LOGS if args.smoke else LOGS
    logs.mkdir(mode=0o700, exist_ok=False)
    plan = [("smoke", 4)] if args.smoke else [
        ("01-4cpu", 4), ("02-2cpu", 2), ("03-2cpu", 2), ("04-4cpu", 4)]
    for label, _ in plan:
        path = DISKS / (label + ".raw")
        if path.exists() or path.is_symlink():
            raise RuntimeError("Refusing to overwrite trial disk: " + str(path))
    results = [run_trial(label, cpus, logs) for label, cpus in plan]
    summary = {"schema_version": 1, "smoke": args.smoke, "trial_order_vcpus": [cpus for _, cpus in plan],
               "memory_mib": 4096, "trials": results,
               "workload_content_validated": False,
               "limits": "Headless QEMU native ARM64 browser experiment, not UTM GUI or native-speed parity. "
               "Host CPU covers sampled boot, settling and benchmark time; RSS peak is sampled every 0.5 seconds. "
               "Swap and page counters are host-wide; report arrival is observed with up to a polling interval of delay. "
               "Host load, cache state and memory pressure affect these ordered trials."}
    write_json_new(logs / "results.json", summary)
    print("Browser benchmark complete: " + str(logs / "results.json"), flush=True)


if __name__ == "__main__":
    main()
