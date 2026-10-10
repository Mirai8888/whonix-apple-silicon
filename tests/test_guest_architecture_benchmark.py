"""Architecture wrapper fixtures; never launch a browser or power off a host."""

from contextlib import ExitStack, redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("guest_architecture_benchmark", ROOT / "scripts/guest-architecture-benchmark.py")
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)
BACKEND_SOURCE = (ROOT / "scripts/guest-browser-benchmark.py").read_bytes()


def backend_fixture():
    backend = ModuleType("fixture_backend")
    exec(compile(BACKEND_SOURCE, "fixture_backend", "exec"), backend.__dict__)
    return backend


class SafeFixture(unittest.TestCase):
    def setUp(self):
        for target in ("Popen", "run"):
            patcher = mock.patch.object(subprocess, target,
                                       side_effect=AssertionError("Real commands forbidden in fixtures"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def linux_root(self, stack, architecture="aarch64"):
        stack.enter_context(mock.patch.object(bench.platform, "system", return_value="Linux"))
        stack.enter_context(mock.patch.object(bench.platform, "machine", return_value=architecture))
        stack.enter_context(mock.patch.object(bench.os, "geteuid", return_value=0))


class GuardTests(SafeFixture):
    def test_runtime_rejects_other_hosts_before_touching_state(self):
        for system, architecture, uid in (("Darwin", "arm64", 0), ("Linux", "aarch64", 1000),
                                          ("Linux", "i686", 0)):
            with self.subTest(system=system, architecture=architecture, uid=uid), \
                 mock.patch.object(bench.platform, "system", return_value=system), \
                 mock.patch.object(bench.platform, "machine", return_value=architecture), \
                 mock.patch.object(bench.os, "geteuid", return_value=uid), \
                 mock.patch.object(bench, "validate_state") as state, redirect_stderr(io.StringIO()):
                self.assertEqual(bench.main(), 1)
                state.assert_not_called()

    def test_root_paths_reject_owners_permissions_links_and_types(self):
        valid = dict(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_gid=0, st_nlink=1)
        path = mock.Mock()
        path.lstat.return_value = SimpleNamespace(**valid)
        bench.require_root_path(path, private=True)
        for change in ({"st_uid": 1000}, {"st_gid": 1000}, {"st_nlink": 2},
                       {"st_mode": stat.S_IFLNK | 0o600}, {"st_mode": stat.S_IFREG | 0o640},
                       {"st_mode": stat.S_IFREG | 0o400}, {"st_mode": stat.S_IFREG | 0o620}):
            with self.subTest(change=change):
                path.lstat.return_value = SimpleNamespace(**(valid | change))
                with self.assertRaises(ValueError):
                    bench.require_root_path(path, private=True)

    def test_state_checks_all_ancestors_and_exact_private_directory(self):
        with mock.patch.object(bench, "require_root_path") as guard:
            bench.validate_state()
        self.assertEqual(guard.call_args_list, [mock.call(Path("/"), directory=True),
                         mock.call(Path("/var"), directory=True), mock.call(Path("/var/lib"), directory=True),
                         mock.call(bench.STATE, directory=True, private=True)])
        path = mock.Mock()
        for mode in (0o750, 0o770, 0o755):
            path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_uid=0, st_gid=0)
            with self.assertRaises(ValueError):
                bench.require_root_path(path, directory=True, private=True)

    def test_claim_is_four_cpu_empty_marker_one_use_and_missing_is_failure(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            self.linux_root(stack)
            state = Path(directory)
            stack.enter_context(mock.patch.object(bench, "STATE", state))
            stack.enter_context(mock.patch.object(bench, "validate_state"))
            stack.enter_context(mock.patch.object(bench, "require_root_path", side_effect=lambda path, **kw: path.lstat()))
            with self.assertRaisesRegex(ValueError, "one-use"):
                bench.claim_run()
            marker = state / "enabled"
            marker.write_text("not empty")
            with self.assertRaisesRegex(ValueError, "empty"):
                bench.claim_run()
            marker.write_text("")
            for count in (None, 2, 8):
                with mock.patch.object(bench.os, "cpu_count", return_value=count):
                    with self.assertRaisesRegex(ValueError, "four vCPUs"):
                        bench.claim_run()
                self.assertTrue(marker.exists())
            with mock.patch.object(bench.os, "cpu_count", return_value=4):
                self.assertTrue(bench.claim_run())
            self.assertFalse(marker.exists())
            with self.assertRaisesRegex(ValueError, "one-use"):
                bench.claim_run()


class ImportTests(SafeFixture):
    def fixture(self, source=BACKEND_SOURCE):
        stack = ExitStack()
        self.addCleanup(stack.close)
        directory = stack.enter_context(tempfile.TemporaryDirectory())
        state = Path(directory)
        (state / bench.BACKEND_NAME).write_bytes(source)
        (state / bench.BACKEND_NAME).chmod(0o600)
        wrapper = state / "guest-architecture-benchmark.py"
        wrapper.write_text("fixture")
        wrapper.chmod(0o600)
        self.linux_root(stack)
        stack.enter_context(mock.patch.object(bench, "STATE", state))
        stack.enter_context(mock.patch.object(bench, "__file__", str(wrapper)))
        stack.enter_context(mock.patch.object(bench, "validate_state"))
        stack.enter_context(mock.patch.object(bench, "require_root_path"))
        real_fstat = os.fstat
        def root_fstat(descriptor):
            info = real_fstat(descriptor)
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_gid=0,
                                   st_nlink=info.st_nlink, st_size=info.st_size)
        stack.enter_context(mock.patch.object(bench.os, "fstat", side_effect=root_fstat))
        return state

    def test_only_exact_pinned_backend_is_executed(self):
        self.fixture()
        self.assertEqual(hashlib.sha256(BACKEND_SOURCE).hexdigest(), bench.BACKEND_SHA256)
        backend = bench.load_backend()
        self.assertEqual(backend.LAUNCH_TIMEOUT_SECONDS, 60)
        self.assertEqual(backend.WARMUP_SECONDS, 40)
        self.assertEqual(backend.SAMPLE_SECONDS, 0.25)

    def test_modified_backend_is_rejected_before_execution(self):
        self.fixture(b"raise AssertionError('must never execute')\n")
        with self.assertRaisesRegex(ValueError, "pinned"):
            bench.load_backend()

    def test_wrong_wrapper_location_is_rejected(self):
        self.fixture()
        with mock.patch.object(bench, "__file__", "/tmp/guest-architecture-benchmark.py"):
            with self.assertRaisesRegex(ValueError, "dedicated private"):
                bench.load_backend()

    def test_backend_descriptor_rejects_oversize_links_and_permissions(self):
        state = self.fixture()
        path = state / bench.BACKEND_NAME
        path.chmod(0o640)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            bench.load_backend()
        path.chmod(0o600)
        os.link(path, state / "other")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            bench.load_backend()
        (state / "other").unlink()
        with path.open("wb") as stream:
            stream.truncate(bench.MAX_BACKEND_BYTES + 1)
        with self.assertRaisesRegex(ValueError, "oversized"):
            bench.load_backend()


class MetadataTests(SafeFixture):
    def fixture(self, architecture="aarch64"):
        stack = ExitStack()
        self.addCleanup(stack.close)
        root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.linux_root(stack, architecture)
        files = {"application.ini": b"[App]\nVersion=153.2.0\nBuildID=20260902080000\n",
                 "platform.ini": b"[Build]\nBuildID=20260902080000\nMilestone=153.2.0\n",
                 "tbb_version.json": json.dumps({"version": "16.0a11", "channel": "alpha",
                                                "architecture": "linux-" + architecture}).encode()}
        executable = bytearray(64)
        executable[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", executable, 18, bench.ARCHITECTURES[architecture][0])
        files["firefox.real"] = executable
        for name, content in files.items():
            path = root / name
            path.write_bytes(content)
            path.chmod(0o600)
        backend = SimpleNamespace(BENCH=root / "Downloads/bench")
        account = SimpleNamespace(pw_uid=os.getuid())
        return root, backend, account

    def test_reports_actual_package_application_and_binary_metadata_for_both_architectures(self):
        for architecture in ("aarch64", "x86_64"):
            with self.subTest(architecture=architecture):
                root, backend, account = self.fixture(architecture)
                metadata = bench.browser_metadata(backend, account)
                self.assertEqual(metadata["version"], "16.0a11")
                self.assertEqual(metadata["application_version"], "153.2.0")
                self.assertEqual(metadata["build_id"], "20260902080000")
                self.assertEqual(metadata["architecture"], architecture)
                self.assertEqual(metadata["executable"], "firefox.real")
                self.assertEqual(metadata["package_architecture"], "linux-" + architecture)
                self.assertEqual(metadata["tbb_version_json_sha256"], hashlib.sha256((root / "tbb_version.json").read_bytes()).hexdigest())

    def test_rejects_version_channel_architecture_and_duplicate_package_keys(self):
        root, backend, account = self.fixture()
        for content in (b'{"version":"15.0","channel":"alpha","architecture":"linux-aarch64"}',
                        b'{"version":"16.0a11","channel":"stable","architecture":"linux-aarch64"}',
                        b'{"version":"16.0a11","channel":"alpha","architecture":"linux-x86_64"}',
                        b'{"version":"16.0a11","version":"16.0a11"}', b'[]'):
            with self.subTest(content=content):
                (root / "tbb_version.json").write_bytes(content)
                with self.assertRaises(ValueError):
                    bench.browser_metadata(backend, account)

    def test_rejects_script_or_mismatched_elf_architecture(self):
        root, backend, account = self.fixture()
        executable = root / "firefox.real"
        source = bytearray(executable.read_bytes())
        executable.write_bytes(b"#!/bin/sh\n")
        with self.assertRaisesRegex(ValueError, "ELF"):
            bench.browser_metadata(backend, account)
        struct.pack_into("<H", source, 18, 62)
        executable.write_bytes(source)
        with self.assertRaisesRegex(ValueError, "architecture"):
            bench.browser_metadata(backend, account)

    def test_bounded_metadata_read_rejects_symlink_hardlink_owner_and_size(self):
        root, _, account = self.fixture()
        path = root / "application.ini"
        link = root / "link"
        link.symlink_to(path)
        with self.assertRaises(OSError):
            bench.read_browser_file(link, account.pw_uid, 1024)
        link.unlink()
        os.link(path, link)
        with self.assertRaises(ValueError):
            bench.read_browser_file(path, account.pw_uid, 1024)
        link.unlink()
        with self.assertRaises(ValueError):
            bench.read_browser_file(path, account.pw_uid + 1, 1024)
        with self.assertRaises(ValueError):
            bench.read_browser_file(path, account.pw_uid, 5)


class IntegrationTests(SafeFixture):
    def test_serial_markers_start_own_line_after_unterminated_console_prompt(self):
        backend = backend_fixture()
        console = io.StringIO("host login: ")
        console.seek(0, io.SEEK_END)
        backend.serial_line = lambda line: console.write(line + "\n")
        with ExitStack() as stack:
            self.linux_root(stack)
            bench.configure_backend(backend)
        records = ["WHONIX_BROWSER_LAUNCH_BEGIN=1", "WHONIX_BROWSER_LAUNCH_END=1",
                   "WHONIX_BROWSER_REPORT=e30="]
        for record in records:
            backend.serial_line(record)
        self.assertEqual(console.getvalue().splitlines(), ["host login: ", records[0], "",
                                                          records[1], "", records[2]])

    def test_configured_main_preserves_measurements_and_injects_metadata_before_launch(self):
        for architecture, serial in (("aarch64", "/dev/ttyAMA0"), ("x86_64", "/dev/ttyS0")):
            with self.subTest(architecture=architecture), tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
                self.linux_root(stack, architecture)
                stack.enter_context(mock.patch.object(bench, "STATE", Path(directory)))
                stack.enter_context(mock.patch.object(bench, "claim_run", return_value=True))
                backend = backend_fixture()
                account = SimpleNamespace(pw_uid=1000)
                events, records = [], []
                backend.browser_user = lambda: account
                backend.write_json = lambda document: records.append(dict(document)) or "{}"
                stack.enter_context(mock.patch.object(bench, "browser_metadata", side_effect=lambda *_: events.append("metadata") or {"version": "16.0a11"}))
                bench.configure_backend(backend)
                backend.workload_hash = lambda uid: "a" * 64
                backend.launch = lambda index, uid: events.append(index) or {
                    "index": index, "status": "captured-unvalidated", "duration_seconds": index,
                    "minimum_mem_available_kib": 10000}
                backend.serial_line = mock.Mock()
                backend.poweroff = lambda: events.append("poweroff")
                stack.enter_context(mock.patch.object(backend.time, "sleep", side_effect=lambda seconds: events.append(("sleep", seconds))))
                stack.enter_context(mock.patch.object(backend.os, "umask"))
                stack.enter_context(mock.patch.object(backend.os, "cpu_count", return_value=4))
                self.assertEqual(backend.main(), 0)
                self.assertEqual(events, ["metadata", ("sleep", 40), 1, 2, 3, 4, "poweroff"])
                self.assertEqual(str(backend.SERIAL), serial)
                self.assertEqual(records[0]["architecture"], architecture)
                self.assertEqual(records[0]["per_launch_timeout_seconds"], 180)
                self.assertEqual(records[0]["sample_interval_seconds"], 0.25)
                self.assertEqual(records[0]["browser_metadata"], {"version": "16.0a11"})
                self.assertEqual(records[0]["backend_sha256"], bench.BACKEND_SHA256)
                self.assertEqual(len(records[0]["launches"]), 4)

    def test_missing_marker_returns_failure_without_direct_poweroff(self):
        backend = backend_fixture()
        with ExitStack() as stack, redirect_stderr(io.StringIO()):
            self.linux_root(stack)
            stack.enter_context(mock.patch.object(bench, "claim_run", side_effect=ValueError("Missing one-use marker")))
            bench.configure_backend(backend)
            backend.poweroff = mock.Mock()
            self.assertEqual(backend.main(), 1)
            backend.poweroff.assert_not_called()

    def test_service_limits_and_failure_poweroff_are_explicit(self):
        service = (ROOT / "benchmarks/whonix-architecture-benchmark.service").read_text()
        for setting in ("After=display-manager.service", "FailureAction=poweroff", "RuntimeMaxSec=840",
                        "TimeoutStopSec=15", "KillMode=control-group", "UMask=0077",
                        "ExecStart=/usr/bin/python3 -I /var/lib/whonix-architecture-benchmark/guest-architecture-benchmark.py"):
            self.assertIn(setting, service.splitlines())


if __name__ == "__main__":
    unittest.main()
