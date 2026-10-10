"""One-boot benchmark fixtures; never run commands, guests, or real sleeps."""

import base64
from contextlib import ExitStack, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/guest-idle-benchmark.py"
SPEC = importlib.util.spec_from_file_location("guest_idle_benchmark", SCRIPT)
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def sample(when, pid=42, restarts=0, active=True, ticks=0):
    return {
        "monotonic_seconds": when,
        "kloak": {"MainPID": pid, "NRestarts": restarts,
                  "ActiveState": "active" if active else "activating",
                  "SubState": "running" if active else "auto-restart",
                  "Result": "success" if active else "core-dump",
                  "ExecMainCode": 0, "ExecMainStatus": 0 if active else 31},
        "cpu_ticks": {"cpu": dict(zip(bench.CPU_FIELDS,
                                      (ticks, 0, 0, ticks * 9, 0, 0, 0, 0, 0, 0)))},
        "proc_counters": {}, "memory_kib": {"MemAvailable": 3000000},
    }


class SafeFixture(unittest.TestCase):
    def setUp(self):
        # A missed mock fails rather than ever reaching systemctl or a workload.
        self.no_processes = mock.patch.object(
            bench.subprocess, "run", side_effect=AssertionError("Real commands forbidden in fixtures"))
        self.command = self.no_processes.start()
        self.addCleanup(self.no_processes.stop)


class ClaimTests(SafeFixture):
    def test_unsafe_platform_or_unprivileged_main_never_powers_off(self):
        for system, machine, uid in (("Darwin", "arm64", 0),
                                     ("Linux", "x86_64", 0),
                                     ("Linux", "aarch64", 1000)):
            with self.subTest(system=system, machine=machine, uid=uid), \
                 mock.patch.object(bench.platform, "system", return_value=system), \
                 mock.patch.object(bench.platform, "machine", return_value=machine), \
                 mock.patch.object(bench.os, "geteuid", return_value=uid), \
                 mock.patch.object(bench, "run_command") as commands, \
                 mock.patch.object(bench, "serial_line") as serial, \
                 mock.patch.object(bench, "require_private_path") as validate, \
                 redirect_stderr(io.StringIO()):
                self.assertEqual(bench.main(), 1)
                commands.assert_not_called()
                serial.assert_not_called()
                validate.assert_not_called()
        self.command.assert_not_called()

    def test_marker_is_required_and_consumed_only_for_a_valid_variant(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = Path(directory)
            stack.enter_context(mock.patch.object(bench, "STATE", state))
            stack.enter_context(mock.patch.object(bench.platform, "system", return_value="Linux"))
            stack.enter_context(mock.patch.object(bench.platform, "machine", return_value="aarch64"))
            stack.enter_context(mock.patch.object(bench.os, "geteuid", return_value=0))
            # Ownership validation is exercised separately; fixture files belong
            # to the developer on macOS, while /var is a symlink there.
            stack.enter_context(mock.patch.object(
                bench, "require_private_path",
                side_effect=lambda path, directory=False: SimpleNamespace(st_size=0) if directory else path.lstat()))
            commands = stack.enter_context(mock.patch.object(bench, "run_command"))
            serial = stack.enter_context(mock.patch.object(bench, "serial_line"))
            self.assertEqual(bench.main(), 0)
            commands.assert_not_called()
            serial.assert_not_called()
            (state / "enabled").touch()
            for variant in ("unknown", "x" * 33):
                (state / "variant").write_text(variant)
                with self.assertRaises(ValueError):
                    bench.claim_run()
                self.assertTrue((state / "enabled").exists())
            (state / "variant").write_text("fixed\n")
            self.assertEqual(bench.claim_run(), "fixed")
            self.assertFalse((state / "enabled").exists())
            self.assertIsNone(bench.claim_run())
        self.command.assert_not_called()

    def test_private_path_rejects_wrong_owner_writable_links_and_wrong_types(self):
        valid = {"st_mode": stat.S_IFREG | 0o600, "st_uid": 0, "st_nlink": 1}
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(**valid)
        bench.require_private_path(path)
        for change in ({"st_uid": 1000}, {"st_mode": stat.S_IFREG | 0o620},
                       {"st_mode": stat.S_IFLNK | 0o777}, {"st_nlink": 2},
                       {"st_mode": stat.S_IFDIR | 0o700}):
            with self.subTest(change=change):
                path.lstat.return_value = SimpleNamespace(**dict(valid, **change))
                with self.assertRaises(ValueError):
                    bench.require_private_path(path)


class MainTests(SafeFixture):
    def run_fixture(self, variant="fixed", snapshot_error=False, crypto_error=False):
        events, lines = [], []
        clock = [100.0]
        snapshots = [0]

        def sleep(deadline):
            events.append(("sleep", deadline - clock[0]))
            clock[0] = deadline

        def snapshot():
            index = snapshots[0]
            snapshots[0] += 1
            events.append(("snapshot", index))
            if snapshot_error:
                raise RuntimeError("fixture snapshot failure")
            if variant == "fixed":
                return sample(clock[0], ticks=index * 10)
            return sample(clock[0], pid=0, restarts=7 + index * 11,
                          active=False, ticks=index * 40)

        def serial(line):
            lines.append(line)
            events.append(("serial", line.split("=", 1)[0]))

        def crypto():
            events.append(("crypto", clock[0]))
            return {"status": "error" if crypto_error else "ok"}

        def pixman():
            events.append(("pixman", clock[0]))
            return {"status": "ok"}

        def command(argv, timeout=10):
            self.assertEqual(argv, [bench.SYSTEMCTL, "poweroff"])
            self.assertEqual(timeout, 10)
            events.append(("poweroff", clock[0]))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        original_write = bench.write_json

        def write(name, document):
            events.append(("write", name))
            return original_write(name, document)

        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = Path(directory)
            for target, name, replacement in (
                (bench, "STATE", state), (bench, "claim_run", mock.Mock(return_value=variant)),
                (bench, "snapshot", snapshot), (bench, "serial_line", serial),
                (bench, "sleep_until", sleep), (bench, "run_command", command),
                (bench, "crypto_benchmark", crypto), (bench, "pixman_benchmark", pixman),
                (bench, "write_json", write),
                (bench.time, "monotonic", lambda: clock[0]),
                (bench.platform, "machine", lambda: "aarch64"),
                (bench.platform, "release", lambda: "fixture-kernel"),
                (bench.os, "cpu_count", lambda: 4),
                (bench.os, "sysconf", lambda name: 100),
            ):
                stack.enter_context(mock.patch.object(target, name, replacement))
            # Only /proc/cpuinfo is read in main; reports use normal file I/O.
            with mock.patch.object(bench.Path, "read_text", return_value="Features: fp asimd aes sha2\n"):
                status = bench.main()
            report = json.loads((state / "report.json").read_text())
            self.assertEqual(stat.S_IMODE((state / "report.json").stat().st_mode), 0o600)
            self.assertEqual(sorted(path.name for path in state.iterdir()), ["report.json"])
        self.command.assert_not_called()
        records = [line.split("=", 1)[1] for line in lines if line.startswith("WHONIX_BENCH_REPORT=")]
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(base64.b64decode(records[0], validate=True)), report)
        self.assertEqual(sum(event[0] == "poweroff" for event in events), 1)
        self.assertEqual(events[-1][0], "poweroff")
        return status, report, events

    def test_fixed_snapshot_end_guard_and_workloads_are_strictly_ordered(self):
        status, report, events = self.run_fixture()
        self.assertEqual(status, 0)
        self.assertEqual(events, [
            ("sleep", 40.0), ("snapshot", 0), ("serial", "WHONIX_BENCH_BEGIN"),
            ("sleep", 60.0), ("snapshot", 1), ("serial", "WHONIX_BENCH_END"),
            ("sleep", 5.0), ("crypto", 205.0), ("pixman", 205.0),
            ("write", "report.json"), ("serial", "WHONIX_BENCH_REPORT"), ("poweroff", 205.0),
        ])
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(report["end_guard_seconds"], 5)
        self.assertEqual(report["elapsed_seconds"], 60)
        self.assertEqual(report["snapshot_elapsed_seconds"], 60)
        self.assertTrue(report["expectations"]["passed"])
        self.assertEqual(report["status"], "ok")

    def test_control_has_same_guard_before_report_and_never_runs_workloads(self):
        status, report, events = self.run_fixture("control")
        self.assertEqual(status, 0)
        self.assertEqual(events, [
            ("sleep", 40.0), ("snapshot", 0), ("serial", "WHONIX_BENCH_BEGIN"),
            ("sleep", 60.0), ("snapshot", 1), ("serial", "WHONIX_BENCH_END"),
            ("sleep", 5.0), ("write", "report.json"),
            ("serial", "WHONIX_BENCH_REPORT"), ("poweroff", 205.0),
        ])
        self.assertTrue(report["expectations"]["passed"])
        self.assertNotIn("crypto", report)
        self.assertNotIn("pixman", report)

    def test_failure_after_claim_reports_error_and_still_requests_shutdown(self):
        for options in ({"snapshot_error": True}, {"crypto_error": True}):
            with self.subTest(options=options):
                status, report, events = self.run_fixture(**options)
                self.assertEqual(status, 1)
                self.assertEqual(report["status"], "error")
                self.assertTrue(report["errors"])
                self.assertFalse(any(event[0] == "pixman" for event in events))


class CounterTests(SafeFixture):
    def test_fixed_requires_active_same_pid_and_no_restarts(self):
        before = sample(0)
        for after in (sample(60, pid=0), sample(60, pid=43),
                      sample(60, restarts=1), sample(60, active=False)):
            with self.subTest(after=after["kloak"]):
                self.assertFalse(bench.expectations("fixed", before, after)["passed"])
        self.assertFalse(bench.expectations("control", before, sample(60))["passed"])

    def test_cpu_counter_math_does_not_double_count_guest_time(self):
        before, after = sample(0), sample(60, ticks=10)
        after["cpu_ticks"]["cpu"]["guest"] = 4
        delta = bench.cpu_delta(before, after)
        self.assertEqual(delta["total_ticks"], 100)
        self.assertEqual(delta["busy_percent"], 10)
        with self.assertRaisesRegex(ValueError, "decreased"):
            bench.cpu_delta(after, before)


if __name__ == "__main__":
    unittest.main()
