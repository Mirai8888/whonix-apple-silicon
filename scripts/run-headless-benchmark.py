#!/usr/bin/python3
"""Run an ABBA idle comparison on disposable, prepared ARM64 disk clones.

Requires the two benchmark templates prepared by the local maintenance workflow.
Does not open UTM or change either imported guest. Trial copies remain as evidence.
"""
import base64
import importlib.util
import json
import os
import pathlib
import plistlib
import re
import stat
import statistics
import subprocess
import time
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISKS = ROOT / "artifacts/benchmarks/kloak-comparison"
LOGS = ROOT / "logs/kloak-comparison"
UTM = "/Applications/UTM.app/Contents/MacOS/utmctl"
QEMU = "/opt/homebrew/bin/qemu-system-aarch64"
MAX_CONSOLE_BYTES = 2 * 1024 * 1024
SPEC = importlib.util.spec_from_file_location("host_metrics", ROOT / "scripts/measure-host.py")
METRICS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(METRICS)


def read_console(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return b""
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONSOLE_BYTES:
            raise RuntimeError("Unexpected diagnostic console type or size")
        # The bounded read also handles a file growing after fstat().
        content = stream.read(MAX_CONSOLE_BYTES + 1)
    if len(content) > MAX_CONSOLE_BYTES:
        raise RuntimeError("Diagnostic console exceeded its size limit")
    return content


def require_unused(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
        raise RuntimeError("Expected an unlinked regular disk file: " + str(path))
    result = subprocess.run(["/usr/sbin/lsof", "-t", "--", str(path)],
                            capture_output=True, text=True)
    if result.returncode != 1 or result.stdout:
        raise RuntimeError("Disk is open or its use could not be checked: " + str(path))


def require_private_disk_directory():
    try:
        info = DISKS.lstat()
    except FileNotFoundError as error:
        raise RuntimeError("Create a caller-owned benchmark disk directory with mode 0700: " + str(DISKS)) from error
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
            stat.S_IMODE(info.st_mode) != 0o700):
        raise RuntimeError("Benchmark disk directory must be a real caller-owned directory with mode 0700; "
                           "check ownership and use chmod 700 before running: " + str(DISKS))


def clone_trial_disk(source, destination):
    # cp -c can preserve a template's public mode despite umask. Protect the
    # directory before copying so there is never a readable clone window.
    require_private_disk_directory()
    if source.parent != DISKS or destination.parent != DISKS:
        raise RuntimeError("Benchmark disks must stay inside the private disk directory")
    info = source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        raise RuntimeError("Expected a caller-owned regular template without links: " + str(source))
    if destination.exists() or destination.is_symlink():
        raise RuntimeError("Refusing to overwrite trial disk: " + str(destination))
    subprocess.run(["/bin/cp", "-c", str(source), str(destination)], check=True)
    descriptor = os.open(destination, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
            raise RuntimeError("Unexpected cloned disk type, owner, or links")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def validate_environment():
    if os.uname().sysname != "Darwin" or os.uname().machine != "arm64":
        raise RuntimeError("This runner requires native Apple Silicon macOS")
    require_private_disk_directory()
    identifiers = {}
    for role in ("Gateway", "Workstation"):
        bundle = ROOT / "artifacts/utm" / ("Whonix-" + role + "-LXQt.utm")
        subprocess.run(["/usr/bin/python3", str(ROOT / "scripts/prepare_utm.py"),
                        "--check", str(bundle)], check=True)
        with (bundle / "config.plist").open("rb") as stream:
            identifiers[role] = plistlib.load(stream)["Information"]["UUID"]
    for role, required in (("Gateway", "started"), ("Workstation", "stopped")):
        state = subprocess.check_output([UTM, "status", identifiers[role]], text=True).strip()
        if state != required:
            raise RuntimeError(role + " must be " + required + "; no VM state was changed")
    listener = subprocess.check_output(
        ["/usr/sbin/lsof", "-nP", "-iTCP:8010", "-sTCP:LISTEN", "-Fpn"], text=True)
    pids = re.findall(r"^p(\d+)$", listener, re.M)
    endpoints = re.findall(r"^n(.+)$", listener, re.M)
    if len(pids) != 1 or endpoints != ["127.0.0.1:8010"]:
        raise RuntimeError("Expected a single Gateway loopback listener")
    command = subprocess.check_output(["/bin/ps", "-ww", "-p", pids[0], "-o", "args="], text=True)
    if " -uuid " + identifiers["Gateway"] + " " not in command:
        raise RuntimeError("Port 8010 is not owned by this Gateway")
    require_unused(ROOT / "builder/Whonix ARM64 Builder.utm/Data/builder.qcow2")
    for variant in ("control", "fixed"):
        require_unused(DISKS / (variant + "-template.raw"))


def run_trial(number, variant):
    validate_environment()
    label = "%02d-%s" % (number, variant)
    disk = DISKS / (label + ".raw")
    clone_trial_disk(DISKS / (variant + "-template.raw"), disk)
    console = LOGS / (label + "-console.log")
    identifier = str(uuid.uuid4()).upper()
    args = [QEMU, "-name", "Whonix-ARM64-benchmark-" + label, "-uuid", identifier,
            "-machine", "virt,accel=hvf", "-cpu", "host", "-smp", "4", "-m", "4096",
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
    result = {"trial": label, "variant": variant, "qemu_args": args,
              "qemu_version": subprocess.check_output([QEMU, "--version"], text=True).splitlines()[0]}
    before = after = report = None
    max_poll_gap = 0.0
    print("Starting " + label + " (headless disposable clone)", flush=True)
    with (LOGS / (label + "-qemu.log")).open("xb") as errors:
        process = subprocess.Popen(args, cwd=ROOT, stdout=errors, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 240
            last_poll = time.monotonic()
            while process.poll() is None and time.monotonic() < deadline:
                content = read_console(console)
                now = time.monotonic()
                if before is not None and after is None:
                    max_poll_gap = max(max_poll_gap, now - last_poll)
                last_poll = now
                if b"WHONIX_BENCH_BEGIN" in content and before is None:
                    if b"WHONIX_BENCH_END" in content:
                        raise RuntimeError("Missed the idle sampling window")
                    before = METRICS.process_sample(process.pid)
                    result["host_memory_before"] = METRICS.host_memory()
                    print(label + ": idle sample started", flush=True)
                if b"WHONIX_BENCH_END" in content and after is None:
                    if before is None:
                        raise RuntimeError("Idle markers arrived out of order")
                    sample_started = time.monotonic()
                    after = METRICS.process_sample(process.pid)
                    result["end_sample_latency_seconds"] = time.monotonic() - sample_started
                    if result["end_sample_latency_seconds"] >= 2:
                        raise RuntimeError("Host process sampling exceeded the quiet-interval budget")
                    result["host_memory_after"] = METRICS.host_memory()
                    print(label + ": idle sample finished", flush=True)
                match = re.search(rb"WHONIX_BENCH_REPORT=([A-Za-z0-9+/=]+)\r?\n", content)
                if match:
                    report = json.loads(base64.b64decode(match.group(1), validate=True))
                time.sleep(0.25)
            if process.poll() is None:
                raise RuntimeError("Guest did not finish within 240 seconds")
            # Read once more after the final serial write/process exit.
            content = read_console(console)
            match = re.search(rb"WHONIX_BENCH_REPORT=([A-Za-z0-9+/=]+)\r?\n", content)
            if match:
                report = json.loads(base64.b64decode(match.group(1), validate=True))
            if process.returncode or before is None or after is None or report is None:
                raise RuntimeError("Incomplete headless benchmark; inspect retained logs")
            if report.get("variant") != variant or report.get("status") != "ok":
                raise RuntimeError("Guest benchmark did not report success: " + repr(report))
            if report.get("end_guard_seconds", 0) < 5 or max_poll_gap >= 2:
                raise RuntimeError("Host idle boundary was not protected by a sufficient quiet interval")
            if before["process_start"] != after["process_start"]:
                raise RuntimeError("Benchmark process identity changed")
            interval = after["sample_monotonic"] - before["sample_monotonic"]
            if not 59 <= interval <= 61:
                raise RuntimeError("Host interval did not closely match the 60-second guest sample")
            result.update({"host_before": before, "host_after": after,
                           "host_interval_seconds": interval,
                           "max_idle_poll_gap_seconds": max_poll_gap,
                           "cpu_percent_one_core": (after["cpu_seconds"] - before["cpu_seconds"]) / interval * 100,
                           "guest": report})
        finally:
            # Only this runner's own disposable child can be terminated here.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    (LOGS / (label + ".json")).write_text(json.dumps(result, indent=2) + "\n")
    print(label + ": %.3f%% of one host CPU core" % result["cpu_percent_one_core"], flush=True)
    return result


def main():
    os.umask(0o077)
    validate_environment()
    LOGS.mkdir(exist_ok=False)
    results = []
    for number, variant in enumerate(("control", "fixed", "fixed", "control"), 1):
        results.append(run_trial(number, variant))
        (LOGS / "results.json").write_text(json.dumps({"trials": results}, indent=2) + "\n")
    medians = {variant: statistics.median(r["cpu_percent_one_core"] for r in results
                                        if r["variant"] == variant)
               for variant in ("control", "fixed")}
    summary = {"trials": results, "median_cpu_percent_one_core": medians,
               "limits": "Four ordered ABBA headless idle trials, not a UTM GUI or native-speed benchmark. "
               "Host load and memory pressure can affect results; console sampling has up to 250ms polling delay."}
    (LOGS / "results.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(medians, indent=2))


if __name__ == "__main__":
    main()
