#!/usr/bin/python3
"""Sample host CPU use of this workspace's running Whonix VMs without UI."""
import argparse
import datetime
import json
import pathlib
import plistlib
import re
import shlex
import subprocess
import time


ROOT = pathlib.Path(__file__).resolve().parent.parent


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def cpu_seconds(value):
    days, _, remainder = value.rpartition("-")
    total = int(days or 0) * 86400
    for exponent, part in enumerate(reversed(remainder.split(":"))):
        total += float(part) * 60 ** exponent
    return total


def process_sample(pid):
    begin = time.monotonic()
    fields = command("/bin/ps", "-p", str(pid), "-o", "time=,rss=,lstart=").split()
    return {"cpu_seconds": cpu_seconds(fields[0]), "rss_kib": int(fields[1]),
            "sample_monotonic": (begin + time.monotonic()) / 2,
            "process_start": " ".join(fields[2:])}


def host_memory():
    raw = command("/usr/bin/vm_stat")
    size = int(re.search(r"page size of (\d+) bytes", raw).group(1))
    counts = {key.strip(): int(value) for key, value in
              re.findall(r"^([^:\n]+):\s+(\d+)\.", raw, re.M)}
    return {"page_size": size, "counts": counts,
            "swap_usage": command("/usr/sbin/sysctl", "-n", "vm.swapusage")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60:
        parser.error("--seconds must be between 1 and 60")
    expected = {}
    for role in ("Gateway", "Workstation"):
        with (ROOT / "artifacts/utm" / ("Whonix-" + role + "-LXQt.utm") /
              "config.plist").open("rb") as stream:
            identifier = plistlib.load(stream)["Information"]["UUID"].upper()
            if identifier in expected:
                raise RuntimeError("Gateway and Workstation UUIDs must differ")
            expected[identifier] = role
    guests = {}
    for line in command("/bin/ps", "-axo", "pid=,comm=").splitlines():
        pid, executable = line.strip().split(None, 1)
        if pathlib.Path(executable).name != "QEMULauncher":
            continue
        tokens = shlex.split(command("/bin/ps", "-ww", "-p", pid, "-o", "args="))
        if "-uuid" not in tokens:
            continue
        identifier = tokens[tokens.index("-uuid") + 1].upper()
        if identifier not in expected:
            continue
        role = expected[identifier]
        if role in guests:
            raise RuntimeError("Multiple processes match " + role)
        flags = {key: tokens[tokens.index(key) + 1]
                 for key in ("-accel", "-cpu", "-smp", "-m")}
        if flags["-accel"] != "hvf" or flags["-cpu"] != "host":
            raise RuntimeError(role + " is not using the expected native HVF CPU")
        guests[role] = {"pid": int(pid), "flags": flags,
                        "before": process_sample(pid)}
    if not guests:
        raise RuntimeError("No running Whonix UTM guests found")
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    memory_before = host_memory()
    begin = time.monotonic()
    time.sleep(args.seconds)
    elapsed = time.monotonic() - begin
    for role, guest in guests.items():
        after = process_sample(guest["pid"])
        if after["process_start"] != guest["before"]["process_start"]:
            raise RuntimeError(role + " process changed during the sample")
        guest["after"] = after
        interval = after["sample_monotonic"] - guest["before"]["sample_monotonic"]
        guest["sample_seconds"] = interval
        guest["cpu_percent_one_core"] = round(
            (after["cpu_seconds"] - guest["before"]["cpu_seconds"]) / interval * 100, 3)
    memory_after = host_memory()
    delta = {key: memory_after["counts"][key] - memory_before["counts"][key]
             for key in ("Swapins", "Swapouts", "Pageouts")
             if key in memory_after["counts"] and key in memory_before["counts"]}
    print(json.dumps({"started_utc": started, "duration_seconds": elapsed,
                      "guests": guests, "host_memory_before": memory_before,
                      "host_memory_after": memory_after, "host_io_delta_pages": delta,
                      "limits": "Host CPU accounting includes virtual devices. 100% is one CPU core. "
                      "RSS is a snapshot, not guest RAM demand. Memory and swap counters are host-wide. "
                      "Compare the same workload; this is not a native-performance benchmark."}, indent=2))


if __name__ == "__main__":
    main()
