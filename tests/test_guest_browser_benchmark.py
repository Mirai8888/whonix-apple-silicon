"""Safety and measurement fixtures; never execute a browser or shutdown command."""

import base64
from contextlib import ExitStack, redirect_stderr
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/guest-browser-benchmark.py"
SPEC = importlib.util.spec_from_file_location("guest_browser_benchmark", SCRIPT)
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def png():
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\0" * (64 * 3)) * 64)) + chunk(b"IEND", b""))


def counters(available=3000000, value=0):
    return {"monotonic_seconds": 1, "memory_kib": {"MemAvailable": available},
            "vmstat": {"pswpin": value, "pswpout": value, "pgmajfault": value},
            "pressure_total_microseconds": {"memory": {"some": value, "full": value}}}


def capture(index):
    return {"index": index, "kind": "cold" if index == 1 else "warm",
            "status": "captured-unvalidated", "duration_seconds": float(index),
            "minimum_mem_available_kib": 3000000 - index, "returncode": 0, "timed_out": False}


class SafeFixture(unittest.TestCase):
    def setUp(self):
        for target in ("run", "Popen"):
            patcher = mock.patch.object(bench.subprocess, target,
                                       side_effect=AssertionError("Real commands forbidden in fixtures"))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(bench.os, "pidfd_open", create=True,
                                    side_effect=AssertionError("Real pidfds forbidden in fixtures"))
        patcher.start()
        self.addCleanup(patcher.stop)


class GuardTests(SafeFixture):
    def test_browser_directory_accepts_only_verified_private_group_write(self):
        account = SimpleNamespace(pw_name="user", pw_uid=1000, pw_gid=1000)
        group = SimpleNamespace(gr_mem=[])
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | 0o770, st_uid=1000, st_gid=1000)
        with mock.patch.object(bench.grp, "getgrgid", return_value=group), \
             mock.patch.object(bench.pwd, "getpwall", return_value=[account]):
            bench.validate_private_group(account)
            bench.require_browser_directory(path, account)
        for change in ({"st_gid": 1001}, {"st_uid": 1001},
                       {"st_mode": stat.S_IFDIR | 0o772}, {"st_mode": stat.S_IFLNK | 0o770}):
            with self.subTest(change=change):
                path.lstat.return_value = SimpleNamespace(**dict(
                    st_mode=stat.S_IFDIR | 0o770, st_uid=1000, st_gid=1000) | change)
                with self.assertRaises(ValueError):
                    bench.require_browser_directory(path, account)

    def test_browser_private_group_rejects_foreign_members_and_shared_primary_gid(self):
        account = SimpleNamespace(pw_name="user", pw_uid=1000, pw_gid=1000)
        for members, accounts in ((["intruder"], [account]),
                                  ([], [account, SimpleNamespace(pw_uid=1001, pw_gid=1000)])):
            with self.subTest(members=members, accounts=accounts), \
                 mock.patch.object(bench.grp, "getgrgid", return_value=SimpleNamespace(gr_mem=members)), \
                 mock.patch.object(bench.pwd, "getpwall", return_value=accounts):
                with self.assertRaises(ValueError):
                    bench.validate_private_group(account)

    def test_unsafe_host_never_claims_or_powers_off(self):
        for system, machine, uid in (("Darwin", "arm64", 0), ("Linux", "x86_64", 0),
                                    ("Linux", "aarch64", 1000)):
            with self.subTest(system=system, machine=machine, uid=uid), \
                 mock.patch.object(bench.platform, "system", return_value=system), \
                 mock.patch.object(bench.platform, "machine", return_value=machine), \
                 mock.patch.object(bench.os, "geteuid", return_value=uid), \
                 mock.patch.object(bench, "poweroff") as poweroff, \
                 mock.patch.object(bench, "require_path") as paths, redirect_stderr(io.StringIO()):
                self.assertEqual(bench.main(), 1)
                poweroff.assert_not_called()
                paths.assert_not_called()

    def test_empty_marker_cpu_guard_and_one_time_claim(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = Path(directory)
            for target, name, replacement in (
                (bench, "STATE", state), (bench.platform, "system", lambda: "Linux"),
                (bench.platform, "machine", lambda: "aarch64"), (bench.os, "geteuid", lambda: 0),
                (bench, "require_path", lambda path, **kwargs: path.lstat()),
            ):
                stack.enter_context(mock.patch.object(target, name, replacement))
            self.assertFalse(bench.claim_run())
            marker = state / "enabled"
            marker.write_text("not empty")
            with self.assertRaisesRegex(ValueError, "empty"):
                bench.claim_run()
            marker.write_text("")
            with mock.patch.object(bench.os, "cpu_count", return_value=3):
                with self.assertRaisesRegex(ValueError, "vCPUs"):
                    bench.claim_run()
            self.assertTrue(marker.exists())
            with mock.patch.object(bench.os, "cpu_count", return_value=4):
                self.assertTrue(bench.claim_run())
            self.assertFalse(marker.exists())
            self.assertFalse(bench.claim_run())

    def test_private_paths_reject_permissions_owners_types_and_links(self):
        valid = {"st_mode": stat.S_IFREG | 0o600, "st_uid": 0, "st_nlink": 1}
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(**valid)
        bench.require_path(path, private=True)
        for change in ({"st_mode": stat.S_IFREG | 0o640}, {"st_uid": 1000},
                       {"st_nlink": 2}, {"st_mode": stat.S_IFLNK | 0o777}):
            with self.subTest(change=change):
                path.lstat.return_value = SimpleNamespace(**dict(valid, **change))
                with self.assertRaises(ValueError):
                    bench.require_path(path, private=True)


class OutputTests(SafeFixture):
    def test_png_validity_does_not_claim_workload_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.png"
            path.write_bytes(png())
            path.chmod(0o600)
            result = bench.read_png(path, os.getuid())
            self.assertEqual(result["dimensions"], [64, 64])
            self.assertFalse(result["workload_content_validated"])
            self.assertIn("e5f37d05", result["content_validation_required"])

    def test_png_rejects_symlinks_hardlinks_permissions_crc_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.png"
            path.write_bytes(png())
            path.chmod(0o600)
            symlink = Path(directory) / "symlink.png"
            symlink.symlink_to(path)
            with self.assertRaises(OSError):
                bench.read_png(symlink, os.getuid())
            hardlink = Path(directory) / "hardlink.png"
            os.link(path, hardlink)
            with self.assertRaises(ValueError):
                bench.read_png(path, os.getuid())
            hardlink.unlink()
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                bench.read_png(path, os.getuid())
            path.chmod(0o600)
            malformed = bytearray(png())
            malformed[-1] ^= 1
            path.write_bytes(malformed)
            with self.assertRaisesRegex(ValueError, "CRC"):
                bench.read_png(path, os.getuid())
            with path.open("wb") as stream:
                stream.truncate(bench.MAX_PNG_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "oversized"):
                bench.read_png(path, os.getuid())

    def test_workload_hash_rejects_owner_and_oversize(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.html"
            path.write_text("fixture")
            path.chmod(0o600)
            with mock.patch.object(bench, "PAGE", path):
                self.assertEqual(len(bench.workload_hash(os.getuid())), 64)
                with self.assertRaises(ValueError):
                    bench.workload_hash(os.getuid() + 1)
                with path.open("wb") as stream:
                    stream.truncate(128 * 1024 + 1)
                with self.assertRaises(ValueError):
                    bench.workload_hash(os.getuid())


class MeasurementTests(SafeFixture):
    def test_timeout_signals_only_own_child_and_records_failure(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            clock = [0.0]
            process = mock.Mock(pid=4242, returncode=None)
            process.poll.side_effect = lambda: process.returncode
            def terminate(child):
                self.assertIs(child, process)
                child.returncode = -15
            def select(readable, writable, errors, timeout):
                self.assertEqual(readable, [9876])
                clock[0] += timeout
                return [], [], []
            for target, name, replacement in (
                (bench, "STATE", root), (bench, "BENCH", root),
                (bench, "PAGE", root / "local.html"), (bench, "LAUNCH_TIMEOUT_SECONDS", 0.5),
                (bench, "read_counters", lambda: counters()),
                (bench.time, "monotonic", lambda: clock[0]),
                (bench, "terminate_group", terminate),
                (bench.select, "select", select),
            ):
                stack.enter_context(mock.patch.object(target, name, replacement))
            popen = stack.enter_context(mock.patch.object(bench.subprocess, "Popen", return_value=process))
            pidfd_open = stack.enter_context(mock.patch.object(bench.os, "pidfd_open", return_value=9876))
            close = stack.enter_context(mock.patch.object(bench.os, "close"))
            png_reader = stack.enter_context(mock.patch.object(bench, "read_png"))
            result = bench.launch(1, 1000)
            self.assertTrue(result["timed_out"])
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["duration_seconds"], 0.5)
            self.assertEqual(result["returncode"], -15)
            pidfd_open.assert_called_once_with(4242, 0)
            close.assert_called_once_with(9876)
            png_reader.assert_not_called()
            argv = popen.call_args.args[0]
            self.assertEqual(argv[:5], ["/usr/sbin/runuser", "-u", "user", "--", "/bin/bash"])
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertNotIn("-profile", argv)
            self.assertNotIn("--no-sandbox", argv)
            for path in root.iterdir():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_exit_timing_does_not_wait_out_sampling_interval(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            clock = [0.0]
            process = mock.Mock(pid=4242, returncode=None)
            process.poll.side_effect = lambda: process.returncode
            def select(readable, writable, errors, timeout):
                self.assertEqual(readable, [9876])
                self.assertEqual(timeout, bench.SAMPLE_SECONDS)
                clock[0] += 0.035
                return [9876], [], []
            def wait():
                process.returncode = 0
                return 0
            process.wait.side_effect = wait
            for target, name, replacement in (
                (bench, "STATE", root), (bench, "BENCH", root),
                (bench, "PAGE", root / "local.html"), (bench, "read_counters", lambda: counters()),
                (bench.time, "monotonic", lambda: clock[0]),
                (bench.time, "sleep", mock.Mock(side_effect=AssertionError("Unexpected polling sleep"))),
                (bench, "read_png", lambda path, uid: {"workload_content_validated": False}),
                (bench.select, "select", select),
            ):
                stack.enter_context(mock.patch.object(target, name, replacement))
            stack.enter_context(mock.patch.object(bench.subprocess, "Popen", return_value=process))
            stack.enter_context(mock.patch.object(bench.os, "pidfd_open", return_value=9876))
            close = stack.enter_context(mock.patch.object(bench.os, "close"))
            result = bench.launch(1, 1000)
            self.assertEqual(result["status"], "captured-unvalidated")
            self.assertEqual(result["duration_seconds"], 0.035)
            self.assertFalse(result["timed_out"])
            process.wait.assert_called_once_with()
            close.assert_called_once_with(9876)

    def test_pidfd_open_failure_terminates_owned_child_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            process = mock.Mock(pid=4242, returncode=None)
            process.poll.return_value = None
            for target, name, replacement in (
                (bench, "STATE", root), (bench, "BENCH", root),
                (bench, "PAGE", root / "local.html"), (bench, "read_counters", lambda: counters()),
            ):
                stack.enter_context(mock.patch.object(target, name, replacement))
            stack.enter_context(mock.patch.object(bench.subprocess, "Popen", return_value=process))
            stack.enter_context(mock.patch.object(bench.os, "pidfd_open", side_effect=OSError("unsupported")))
            cleanup = stack.enter_context(mock.patch.object(bench, "terminate_group"))
            close = stack.enter_context(mock.patch.object(bench.os, "close"))
            with self.assertRaisesRegex(OSError, "unsupported"):
                bench.launch(1, 1000)
            cleanup.assert_called_once_with(process)
            close.assert_not_called()

    def test_pidfd_closes_after_sampler_exception(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            root = Path(directory)
            process = mock.Mock(pid=4242, returncode=None)
            process.poll.return_value = None
            for target, name, replacement in (
                (bench, "STATE", root), (bench, "BENCH", root),
                (bench, "PAGE", root / "local.html"),
            ):
                stack.enter_context(mock.patch.object(target, name, replacement))
            stack.enter_context(mock.patch.object(bench, "read_counters", side_effect=[counters(), ValueError("sample failed")]))
            stack.enter_context(mock.patch.object(bench.subprocess, "Popen", return_value=process))
            stack.enter_context(mock.patch.object(bench.os, "pidfd_open", return_value=9876))
            cleanup = stack.enter_context(mock.patch.object(bench, "terminate_group"))
            close = stack.enter_context(mock.patch.object(bench.os, "close"))
            with self.assertRaisesRegex(ValueError, "sample failed"):
                bench.launch(1, 1000)
            cleanup.assert_called_once_with(process)
            close.assert_called_once_with(9876)

    def test_summary_keeps_cold_separate_and_counters_require_monotonicity(self):
        launches = [capture(index) for index in range(1, 5)]
        result = bench.summary(launches)
        self.assertEqual(result["cold_start_seconds"], 1)
        self.assertEqual(result["warm_median_seconds"], 3)
        self.assertFalse(result["workload_content_validated"])
        self.assertEqual(bench.counter_delta(counters(value=1), counters(value=4))["vmstat"]["pswpin"], 3)
        with self.assertRaises(ValueError):
            bench.counter_delta(counters(value=4), counters(value=1))
        launches[-1]["status"] = "error"
        with self.assertRaises(ValueError):
            bench.summary(launches)


class MainTests(SafeFixture):
    def fixture(self, fail=False):
        events, lines = [], []
        def launch(index, uid):
            events.append(("launch", index, uid))
            if fail:
                raise RuntimeError("fixture launch failure")
            return capture(index)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            state = Path(directory)
            for target, name, replacement in (
                (bench, "STATE", state), (bench, "claim_run", lambda: True),
                (bench, "browser_user", lambda: SimpleNamespace(pw_uid=1000)),
                (bench, "workload_hash", lambda uid: "a" * 64), (bench, "launch", launch),
                (bench, "serial_line", lines.append),
                (bench, "poweroff", lambda: events.append(("poweroff",))),
                (bench.time, "sleep", lambda seconds: events.append(("sleep", seconds))),
                (bench.os, "umask", mock.Mock()), (bench.os, "cpu_count", lambda: 4),
                (bench.platform, "machine", lambda: "aarch64"),
                (bench.platform, "release", lambda: "fixture-kernel"),
            ):
                stack.enter_context(mock.patch.object(target, name, replacement))
            status = bench.main()
            report = json.loads((state / "report.json").read_text())
            self.assertEqual(stat.S_IMODE((state / "report.json").stat().st_mode), 0o600)
        records = [line.split("=", 1)[1] for line in lines if line.startswith("WHONIX_BROWSER_REPORT=")]
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(base64.b64decode(records[0], validate=True)), report)
        self.assertEqual(events[-1], ("poweroff",))
        self.assertEqual(events.count(("poweroff",)), 1)
        return status, report, events

    def test_four_launches_after_settle_then_report_and_poweroff(self):
        status, report, events = self.fixture()
        self.assertEqual(status, 0)
        self.assertEqual(report["status"], "captured-unvalidated")
        self.assertEqual(events, [("sleep", 40)] + [("launch", index, 1000) for index in range(1, 5)] + [("poweroff",)])
        self.assertEqual(report["summary"]["warm_median_seconds"], 3)

    def test_claimed_failure_still_reports_and_powers_off(self):
        status, report, events = self.fixture(fail=True)
        self.assertEqual(status, 1)
        self.assertEqual(report["status"], "error")
        self.assertIn("fixture launch failure", report["errors"][0])
        self.assertEqual(len(report["launches"]), 0)


if __name__ == "__main__":
    unittest.main()
