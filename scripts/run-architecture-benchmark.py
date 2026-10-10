#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare native ARM64/HVF with emulated x86_64/TCG on disposable guests.

Both prepared templates need the architecture guest benchmark. All trials use
four vCPUs, 4096 MiB and the same local browser workload. Neither imported VM is
booted or modified. --smoke runs x86 only; --smoke-arm runs ARM only. Existing
trial disks and logs are never overwritten.
"""
import argparse
import hashlib
import importlib.util
import math
import os
from pathlib import Path
import plistlib
import re
import stat
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
DISKS = ROOT / "artifacts/benchmarks/architecture"
LOGS = ROOT / "logs/architecture"
SMOKE_X86_LOGS = ROOT / "logs/architecture-smoke-x86"
SMOKE_ARM_LOGS = ROOT / "logs/architecture-smoke-arm"
UTM = "/Applications/UTM.app/Contents/MacOS/utmctl"
DEADLINE_SECONDS = 1200
POLL_SECONDS = 0.5
BROWSER_VERSION = "16.0a11"
ARCHITECTURES = {
    "arm64": {"guest": "aarch64", "acceleration": "hvf",
              "qemu": "/opt/homebrew/bin/qemu-system-aarch64"},
    "x86_64": {"guest": "x86_64", "acceleration": "tcg",
               "qemu": "/opt/homebrew/bin/qemu-system-x86_64"},
}


def load_helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Separate imports leave the historical experiment and its source unchanged.
BROWSER = load_helper("architecture_browser_helpers", "run-browser-benchmark.py")
HEADLESS = BROWSER.HEADLESS
METRICS = BROWSER.METRICS
PREPARE = BROWSER.PREPARE
read_console = BROWSER.read_console
parse_report = BROWSER.parse_report
REPORT_PREFIX = BROWSER.REPORT_PREFIX
require_private_directory = BROWSER.require_private_directory
require_unused = BROWSER.require_unused
validate_listener = BROWSER.validate_listener
collect_process_metrics = BROWSER.collect_process_metrics
write_json_new = BROWSER.write_json_new


def architecture_settings(architecture):
    if architecture not in ARCHITECTURES:
        raise RuntimeError("Architecture must be arm64 or x86_64")
    return ARCHITECTURES[architecture]


def require_template(path):
    require_unused(path)
    if stat.S_IMODE(path.lstat().st_mode) != 0o600:
        raise RuntimeError("Benchmark templates must have mode 0600: " + str(path))


def clone_trial_disk(source, destination):
    require_private_directory(DISKS)
    require_template(source)
    HEADLESS.DISKS = DISKS
    HEADLESS.clone_trial_disk(source, destination)


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
    for architecture in ARCHITECTURES:
        require_template(DISKS / (architecture + "-template.raw"))


def validate_report(report, architecture, workload_sha256):
    selected = architecture_settings(architecture)
    if report.get("status") != "captured-unvalidated" or report.get("errors") != []:
        raise RuntimeError("Guest browser benchmark did not report successful captures")
    if type(report.get("schema_version")) is not int or report["schema_version"] != 1:
        raise RuntimeError("Unsupported guest browser report schema")
    if report.get("architecture") != selected["guest"]:
        raise RuntimeError("Guest architecture does not match the requested trial")
    if type(report.get("cpu_count")) is not int or report["cpu_count"] != 4:
        raise RuntimeError("Guest CPU count must be four")
    if report.get("workload_sha256") != workload_sha256:
        raise RuntimeError("Guest browser workload does not match the local source")
    for field, expected in (("warmup_seconds", 40), ("per_launch_timeout_seconds", 180),
                            ("sample_interval_seconds", 0.25)):
        if type(report.get(field)) not in (int, float) or report[field] != expected:
            raise RuntimeError("Unexpected guest measurement setting: " + field)
    metadata = report.get("browser_metadata")
    if (not isinstance(metadata, dict) or metadata.get("version") != BROWSER_VERSION or
            metadata.get("architecture") != selected["guest"] or metadata.get("executable") != "firefox.real" or
            metadata.get("channel") != "alpha" or
            metadata.get("package_architecture") != "linux-" + selected["guest"]):
        raise RuntimeError("Browser release or executable architecture does not match the experiment")
    if (not isinstance(metadata.get("application_version"), str) or
            not re.fullmatch(r"[0-9]{1,4}(?:\.[0-9]{1,4}){1,3}", metadata["application_version"])):
        raise RuntimeError("Invalid browser application version")
    for field in ("build_id", "platform_build_id"):
        if not isinstance(metadata.get(field), str) or not re.fullmatch(r"[0-9]{1,32}", metadata[field]):
            raise RuntimeError("Invalid browser build metadata")
    for field in ("application_ini_sha256", "platform_ini_sha256", "tbb_version_json_sha256"):
        if not isinstance(metadata.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", metadata[field]):
            raise RuntimeError("Invalid browser metadata digest")
    backend_sha256 = hashlib.sha256((ROOT / "scripts/guest-browser-benchmark.py").read_bytes()).hexdigest()
    if report.get("backend_sha256") != backend_sha256:
        raise RuntimeError("Guest browser measurement backend does not match the local source")
    launches = report.get("launches")
    if not isinstance(launches, list) or len(launches) != 4:
        raise RuntimeError("Expected four browser launches")
    for index, launch in enumerate(launches, 1):
        if (not isinstance(launch, dict) or launch.get("status") != "captured-unvalidated" or
                type(launch.get("returncode")) is not int or launch["returncode"] != 0 or
                launch.get("timed_out") is not False or launch.get("log_limit_exceeded") is not False or
                type(launch.get("index")) is not int or launch["index"] != index or
                launch.get("kind") != ("cold" if index == 1 else "warm")):
            raise RuntimeError("Every browser launch must capture successfully in order")
        duration = launch.get("duration_seconds")
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 185:
            raise RuntimeError("Invalid browser launch duration")
        screenshot = launch.get("screenshot")
        if (not isinstance(screenshot, dict) or screenshot.get("workload_content_validated") is not False or
                not isinstance(screenshot.get("sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", screenshot["sha256"]) or
                type(screenshot.get("bytes")) is not int or not 45 <= screenshot["bytes"] <= 20 * 1024 * 1024):
            raise RuntimeError("Invalid browser screenshot metadata")
        dimensions = screenshot.get("dimensions")
        if (not isinstance(dimensions, list) or len(dimensions) != 2 or
                any(type(value) is not int or not 64 <= value <= 4096 for value in dimensions)):
            raise RuntimeError("Invalid browser screenshot dimensions")


def qemu_arguments(disk, console, label, identifier, architecture):
    selected = architecture_settings(architecture)
    args = [selected["qemu"], "-name", "Whonix-architecture-" + label, "-uuid", identifier]
    if architecture == "arm64":
        args += ["-machine", "virt,accel=hvf", "-cpu", "host", "-smp", "4", "-m", "4096",
                 "-bios", "/opt/homebrew/share/qemu/edk2-aarch64-code.fd"]
    else:
        args += ["-machine", "pc-q35-11.1", "-accel", "tcg,thread=multi", "-cpu", "max",
                 "-smp", "4", "-m", "4096"]
    args += ["-nodefaults", "-display", "none", "-vga", "none", "-nic", "none"]
    args += (["-device", "ramfb", "-device", "virtio-gpu-pci"] if architecture == "arm64"
             else ["-device", "virtio-vga"])
    return args + ["-device", "nec-usb-xhci,id=usb-bus", "-device", "usb-tablet,bus=usb-bus.0",
                   "-device", "usb-mouse,bus=usb-bus.0", "-device", "usb-kbd,bus=usb-bus.0",
                   "-drive", "if=none,id=drive0,format=raw,file=" + str(disk) +
                   ",discard=ignore,detect-zeroes=off",
                   "-device", "virtio-blk-pci,drive=drive0,serial=0,bootindex=0",
                   "-netdev", "socket,id=internal,connect=127.0.0.1:8010",
                   "-device", "virtio-net-pci,netdev=internal", "-device", "virtio-rng-pci",
                   "-serial", "file:" + str(console)]


class LaunchObservations:
    """Record host receipt times, not precise guest launch boundaries."""

    def __init__(self):
        self.events = []

    def observe(self, content, elapsed):
        markers = [line.rstrip(b"\r") for line in content.split(b"\n")[:-1]
                   if line.startswith((b"WHONIX_BROWSER_LAUNCH_BEGIN=", b"WHONIX_BROWSER_LAUNCH_END="))]
        expected = [("WHONIX_BROWSER_LAUNCH_" + kind + "=" + str(index)).encode("ascii")
                    for index in range(1, 5) for kind in ("BEGIN", "END")]
        if len(markers) < len(self.events) or markers != expected[:len(markers)]:
            raise RuntimeError("Browser launch markers are duplicate, missing or out of order")
        for number in range(len(self.events), len(markers)):
            self.events.append({"index": number // 2 + 1, "kind": "begin" if number % 2 == 0 else "end",
                                "observed_seconds_since_spawn": elapsed})

    def result(self):
        if len(self.events) != 8:
            raise RuntimeError("Incomplete browser launch markers")
        return {"events": self.events, "approximate_launch_seconds": [
            self.events[index + 1]["observed_seconds_since_spawn"] -
            self.events[index]["observed_seconds_since_spawn"] for index in range(0, 8, 2)],
            "limits": "Host observations of complete serial lines, including transport and polling delay; "
            "not precise launch timing. Sub-poll launches may have identical begin/end receipt times."}


def run_trial(label, architecture, log_directory):
    selected = architecture_settings(architecture)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", label):
        raise RuntimeError("Invalid trial label")
    validate_environment()
    require_private_directory(log_directory)
    disk = DISKS / (label + ".raw")
    console = log_directory / (label + "-console.log")
    qemu_log = log_directory / (label + "-qemu.log")
    output = log_directory / (label + ".json")
    for path in (disk, console, qemu_log, output):
        if path.exists() or path.is_symlink():
            raise RuntimeError("Refusing to overwrite trial evidence: " + str(path))
    clone_trial_disk(DISKS / (architecture + "-template.raw"), disk)
    args = qemu_arguments(disk, console, label, str(uuid.uuid4()).upper(), architecture)
    result = {"trial": label, "architecture": architecture, "acceleration": selected["acceleration"],
              "cpu_count": 4, "memory_mib": 4096, "qemu_args": args,
              "qemu_version": subprocess.check_output([selected["qemu"], "--version"], text=True, timeout=10).splitlines()[0],
              "workload_sha256": hashlib.sha256((ROOT / "benchmarks/browser-local.html").read_bytes()).hexdigest(),
              "host_memory_before": METRICS.host_memory(), "host_poll_seconds": POLL_SECONDS}
    with console.open("xb"):
        pass
    before = after = report = None
    peak_rss = max_poll_gap = 0
    observations = LaunchObservations()
    print("Starting " + label + " (headless disposable " + architecture + "/" + selected["acceleration"] + ")", flush=True)
    with qemu_log.open("xb") as errors:
        started = last_poll = time.monotonic()
        process = subprocess.Popen(args, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=errors, stderr=subprocess.STDOUT)
        try:
            while process.poll() is None:
                now = time.monotonic()
                if now - started >= DEADLINE_SECONDS:
                    raise RuntimeError("Guest did not finish within 1200 seconds")
                max_poll_gap = max(max_poll_gap, now - last_poll)
                last_poll = now
                content = read_console(console)
                read_console(qemu_log)  # The diagnostic stream has the same 2 MiB bound.
                observations.observe(content, now - started)
                parsed = parse_report(content)
                if parsed is not None and report is None:
                    report = parsed
                    result["boot_to_report_seconds"] = now - started
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
            now = time.monotonic()
            max_poll_gap = max(max_poll_gap, now - last_poll)
            content = read_console(console)
            read_console(qemu_log)
            observations.observe(content, now - started)
            final_report = parse_report(content)
            if final_report is not None and report is None:
                result["boot_to_report_seconds"] = now - started
            report = final_report
            result["process_returncode"] = process.returncode
            result["boot_to_exit_seconds"] = now - started
            if process.returncode != 0 or report is None:
                raise RuntimeError("Incomplete architecture benchmark; inspect retained logs")
            validate_report(report, architecture, result["workload_sha256"])
            result.update(collect_process_metrics(before, after, peak_rss))
            result["guest"] = report
            result["host_launch_observations"] = observations.result()
            result["max_observed_poll_gap_seconds"] = max_poll_gap
        finally:
            # The handle identifies only this disposable child, never a user VM.
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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--smoke", action="store_true", help="run one emulated x86_64 validation clone")
    mode.add_argument("--smoke-arm", action="store_true", help="run one native ARM64 validation clone")
    args = parser.parse_args()
    os.umask(0o077)
    validate_environment()
    if args.smoke:
        logs, plan = SMOKE_X86_LOGS, [("smoke-x86_64", "x86_64")]
    elif args.smoke_arm:
        logs, plan = SMOKE_ARM_LOGS, [("smoke-arm64", "arm64")]
    else:
        logs = LOGS
        plan = [("01-arm64", "arm64"), ("02-x86_64", "x86_64"),
                ("03-x86_64", "x86_64"), ("04-arm64", "arm64")]
    for label, _ in plan:
        path = DISKS / (label + ".raw")
        if path.exists() or path.is_symlink():
            raise RuntimeError("Refusing to overwrite trial disk: " + str(path))
    logs.mkdir(mode=0o700, exist_ok=False)
    results = [run_trial(label, architecture, logs) for label, architecture in plan]
    summary = {"schema_version": 1, "smoke": args.smoke or args.smoke_arm,
               "trial_order_architectures": [architecture for _, architecture in plan],
               "cpu_count": 4, "memory_mib": 4096, "trials": results,
               "workload_content_validated": False,
               "limits": "Native ARM64/HVF versus full-system x86_64/TCG with different guest builds. "
               "This does not isolate ISA effects, measure UTM GUI responsiveness, or establish native-speed parity. "
               "Host CPU covers sampled boot, settling and benchmark time; RSS is sampled every 0.5 seconds. "
               "Swap/page counters are host-wide. Serial observations have polling/transport delay. "
               "Host load, cache state and memory pressure affect these ordered trials. "
               "Captures require per-architecture offline content validation; cross-architecture PNG equality is not required."}
    write_json_new(logs / "results.json", summary)
    print("Architecture benchmark complete: " + str(logs / "results.json"), flush=True)


if __name__ == "__main__":
    main()
