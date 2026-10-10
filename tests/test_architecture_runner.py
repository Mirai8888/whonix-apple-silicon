# SPDX-License-Identifier: GPL-3.0-or-later
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("architecture_runner", ROOT / "scripts/run-architecture-benchmark.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
WORKLOAD = hashlib.sha256((ROOT / "benchmarks/browser-local.html").read_bytes()).hexdigest()
BACKEND = hashlib.sha256((ROOT / "scripts/guest-browser-benchmark.py").read_bytes()).hexdigest()


def valid_report(architecture="aarch64"):
    return {"status": "captured-unvalidated", "schema_version": 1, "architecture": architecture,
            "cpu_count": 4, "errors": [], "workload_sha256": WORKLOAD, "backend_sha256": BACKEND,
            "warmup_seconds": 40, "per_launch_timeout_seconds": 180, "sample_interval_seconds": 0.25,
            "browser_metadata": {"version": "16.0a11", "architecture": architecture,
                "executable": "firefox.real", "build_id": "20260902080000", "platform_build_id": "20260902080000",
                "application_version": "153.2.0", "channel": "alpha", "package_architecture": "linux-" + architecture,
                "application_ini_sha256": "a" * 64, "platform_ini_sha256": "b" * 64, "tbb_version_json_sha256": "c" * 64},
            "launches": [{"status": "captured-unvalidated", "index": i, "kind": "cold" if i == 1 else "warm",
                          "returncode": 0, "timed_out": False, "log_limit_exceeded": False, "duration_seconds": 2.5,
                          "screenshot": {"sha256": "f" * 64, "bytes": 100, "dimensions": [1366, 832],
                                         "workload_content_validated": False}} for i in range(1, 5)]}


def report_line(report):
    return runner.REPORT_PREFIX + base64.b64encode(json.dumps(report).encode()) + b"\r\n"


def markers():
    return [("WHONIX_BROWSER_LAUNCH_" + kind + "=" + str(index)).encode() + b"\r\n"
            for index in range(1, 5) for kind in ("BEGIN", "END")]


class ReportValidationTests(unittest.TestCase):
    def test_both_architectures_accept_independent_png_digests(self):
        arm = valid_report()
        x86 = valid_report("x86_64")
        x86["launches"][0]["screenshot"]["sha256"] = "e" * 64
        runner.validate_report(arm, "arm64", WORKLOAD)
        runner.validate_report(x86, "x86_64", WORKLOAD)

    def test_wrong_architecture_release_backend_or_configuration_rejected(self):
        for key, value in (("architecture", "x86_64"), ("cpu_count", True), ("cpu_count", 2),
                           ("schema_version", True), ("workload_sha256", "f" * 64),
                           ("backend_sha256", "f" * 64), ("warmup_seconds", 0),
                           ("per_launch_timeout_seconds", 60), ("sample_interval_seconds", 1)):
            report = valid_report()
            report[key] = value
            with self.subTest(field=key), self.assertRaises(RuntimeError):
                runner.validate_report(report, "arm64", WORKLOAD)
        for key, value in (("version", "16.0"), ("architecture", "x86_64"),
                           ("executable", "firefox"), ("channel", "stable"),
                           ("package_architecture", "linux-x86_64"), ("application_version", "version"),
                           ("build_id", ""), ("platform_build_id", 123),
                           ("application_ini_sha256", "bad"), ("tbb_version_json_sha256", "bad")):
            report = valid_report()
            report["browser_metadata"][key] = value
            with self.subTest(field=key), self.assertRaises(RuntimeError):
                runner.validate_report(report, "arm64", WORKLOAD)

    def test_partial_failed_oversized_or_unordered_launches_rejected(self):
        report = valid_report()
        report["launches"].pop()
        with self.assertRaisesRegex(RuntimeError, "four"):
            runner.validate_report(report, "arm64", WORKLOAD)
        for key, value in (("status", "error"), ("returncode", 1), ("timed_out", True),
                           ("log_limit_exceeded", True), ("index", True), ("index", 2),
                           ("kind", "warm"), ("duration_seconds", 186),
                           ("duration_seconds", float("nan")), ("duration_seconds", True)):
            report = valid_report()
            report["launches"][0][key] = value
            with self.subTest(field=key), self.assertRaises(RuntimeError):
                runner.validate_report(report, "arm64", WORKLOAD)
        for key, value in (("dimensions", [0, 100]), ("dimensions", [5000, 100]),
                           ("bytes", 21 * 1024 * 1024), ("sha256", "bad"),
                           ("workload_content_validated", True)):
            report = valid_report()
            report["launches"][0]["screenshot"][key] = value
            with self.subTest(field=key), self.assertRaises(RuntimeError):
                runner.validate_report(report, "arm64", WORKLOAD)

    def test_shared_parser_still_requires_complete_unique_finite_json(self):
        complete = report_line(valid_report())
        self.assertIsNone(runner.parse_report(complete[:-2]))
        self.assertEqual(runner.parse_report(complete), valid_report())
        for data in (complete + complete, runner.REPORT_PREFIX + b"garbage!\n",
                     runner.REPORT_PREFIX + base64.b64encode(b'{"a":1,"a":2}') + b"\n",
                     runner.REPORT_PREFIX + base64.b64encode(b'{"a":1e999}') + b"\n"):
            with self.subTest(data=data[:60]), self.assertRaises(RuntimeError):
                runner.parse_report(data)


class ArchitectureArgumentsTests(unittest.TestCase):
    def test_arm_configuration_matches_historical_four_cpu_experiment(self):
        args = (Path("/private/disk.raw"), Path("/private/console"), "test", "UUID")
        historical = runner.BROWSER.qemu_arguments(*args, 4)
        historical[historical.index("-name") + 1] = "Whonix-architecture-test"
        self.assertEqual(runner.qemu_arguments(*args, "arm64"), historical)

    def test_x86_requires_tcg_q35_legacy_firmware_and_equal_resources(self):
        argv = runner.qemu_arguments(Path("/d"), Path("/c"), "test", "UUID", "x86_64")
        self.assertTrue(argv[0].endswith("qemu-system-x86_64"))
        for flag, value in (("-machine", "pc-q35-11.1"), ("-accel", "tcg,thread=multi"),
                            ("-cpu", "max"), ("-smp", "4"), ("-m", "4096")):
            self.assertEqual(argv[argv.index(flag) + 1], value)
        self.assertIn("virtio-vga", argv)
        for flag in ("-bios", "-pflash", "-icount"):
            self.assertNotIn(flag, argv)

    def test_both_architectures_have_exactly_one_internal_network_and_no_host_interface(self):
        for architecture in ("arm64", "x86_64"):
            with self.subTest(architecture=architecture):
                argv = runner.qemu_arguments(Path("/d"), Path("/c"), "test", "UUID", architecture)
                self.assertEqual(argv.count("-netdev"), 1)
                self.assertEqual(argv[argv.index("-netdev") + 1], "socket,id=internal,connect=127.0.0.1:8010")
                self.assertEqual(argv[argv.index("-nic") + 1], "none")
                self.assertEqual(argv[argv.index("-display") + 1], "none")
                self.assertIn("-nodefaults", argv)
                for forbidden in ("-virtfs", "-fsdev", "-spice", "-qmp", "-monitor", "-vnc", "-icount"):
                    self.assertNotIn(forbidden, argv)
        with self.assertRaises(RuntimeError):
            runner.qemu_arguments(Path("/d"), Path("/c"), "test", "UUID", "i386")


class EnvironmentGuardTests(unittest.TestCase):
    def test_all_templates_and_builder_are_checked_without_changing_vm_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            disks = root / "disks"
            disks.mkdir(mode=0o700)
            for role, identifier in (("Gateway", "GW"), ("Workstation", "WS")):
                bundle = root / "artifacts/utm" / ("Whonix-" + role + "-LXQt.utm")
                bundle.mkdir(parents=True)
                (bundle / "config.plist").write_bytes(plistlib.dumps({"Information": {"UUID": identifier}}))
            output = ["started\n", "stopped\n", "p123\nn127.0.0.1:8010\n",
                      "/Applications/UTM.app/Contents/XPCServices/QEMULauncher -uuid GW"]
            with mock.patch.object(runner, "ROOT", root), mock.patch.object(runner, "DISKS", disks), \
                 mock.patch.object(runner.os, "uname", return_value=types.SimpleNamespace(sysname="Darwin", machine="arm64")), \
                 mock.patch.object(runner.PREPARE, "process_bundle") as bundles, \
                 mock.patch.object(runner.subprocess, "check_output", side_effect=output) as command, \
                 mock.patch.object(runner, "require_unused") as unused, \
                 mock.patch.object(runner, "require_template") as templates:
                runner.validate_environment()
            unused.assert_called_once_with(root / "builder/Whonix ARM64 Builder.utm/Data/builder.qcow2")
            self.assertEqual(templates.call_args_list, [mock.call(disks / "arm64-template.raw"),
                                                      mock.call(disks / "x86_64-template.raw")])
            self.assertEqual(bundles.call_count, 2)
            self.assertEqual([item.args[0][1] for item in command.call_args_list[:2]], ["status", "status"])

    def test_template_requires_0600_even_inside_private_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "template.raw"
            path.write_bytes(b"data")
            with mock.patch.object(runner, "require_unused") as unused:
                path.chmod(0o644)
                with self.assertRaisesRegex(RuntimeError, "0600"):
                    runner.require_template(path)
                path.chmod(0o600)
                runner.require_template(path)
                self.assertEqual(unused.call_count, 2)

    def test_environment_failure_or_unsafe_label_prevents_clone_and_spawn(self):
        with mock.patch.object(runner, "validate_environment", side_effect=RuntimeError("unsafe")), \
             mock.patch.object(runner, "clone_trial_disk") as clone, \
             mock.patch.object(runner.subprocess, "Popen") as spawn:
            for label in ("refused", "../elsewhere"):
                with self.subTest(label=label), self.assertRaises(RuntimeError):
                    runner.run_trial(label, "arm64", Path("/unused"))
            clone.assert_not_called()
            spawn.assert_not_called()


class HostObservationTests(unittest.TestCase):
    def test_receipt_timestamps_are_coarse_and_allow_both_markers_in_one_poll(self):
        observer = runner.LaunchObservations()
        lines = markers()
        observer.observe(lines[0], 10)
        observer.observe(b"".join(lines[:2]), 11)
        observer.observe(b"".join(lines), 20)
        observer.observe(b"".join(lines), 25)
        result = observer.result()
        self.assertEqual(result["approximate_launch_seconds"], [1, 0, 0, 0])
        self.assertIn("not precise", result["limits"])

    def test_partial_lines_wait_and_missing_duplicate_reordered_markers_fail(self):
        observer = runner.LaunchObservations()
        lines = markers()
        observer.observe(lines[0][:-2], 10)
        self.assertEqual(observer.events, [])
        with self.assertRaisesRegex(RuntimeError, "Incomplete"):
            observer.result()
        for content in (lines[1], lines[0] * 2, b"".join(lines) + lines[0],
                        b"WHONIX_BROWSER_LAUNCH_BEGIN=99\n"):
            with self.subTest(content=content), self.assertRaises(RuntimeError):
                runner.LaunchObservations().observe(content, 10)


class ChildLifetimeTests(unittest.TestCase):
    def test_deadline_terminates_only_created_child_and_escalates_if_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            logs, disks = Path(directory) / "logs", Path(directory) / "disks"
            logs.mkdir(mode=0o700)
            disks.mkdir(mode=0o700)
            process = mock.Mock(pid=1234)
            process.poll.return_value = None
            process.wait.side_effect = [subprocess.TimeoutExpired("own-child", 15), 0]
            with mock.patch.object(runner, "DISKS", disks), mock.patch.object(runner, "validate_environment"), \
                 mock.patch.object(runner, "clone_trial_disk") as clone, \
                 mock.patch.object(runner.METRICS, "host_memory", return_value={}), \
                 mock.patch.object(runner.subprocess, "check_output", return_value="QEMU test\n"), \
                 mock.patch.object(runner.subprocess, "Popen", return_value=process) as spawn, \
                 mock.patch.object(runner.time, "monotonic", side_effect=[0.0, 1201.0]), \
                 self.assertRaisesRegex(RuntimeError, "1200 seconds"):
                runner.run_trial("deadline", "x86_64", logs)
            clone.assert_called_once_with(disks / "x86_64-template.raw", disks / "deadline.raw")
            spawn.assert_called_once()
            process.terminate.assert_called_once_with()
            process.kill.assert_called_once_with()
            self.assertEqual(process.wait.call_args_list, [mock.call(timeout=15), mock.call(timeout=10)])
            self.assertTrue((logs / "deadline-qemu.log").exists())
            self.assertFalse((logs / "deadline.json").exists())

    def test_console_bound_failure_retains_logs_and_cleans_up_child(self):
        with tempfile.TemporaryDirectory() as directory:
            logs, disks = Path(directory) / "logs", Path(directory) / "disks"
            logs.mkdir(mode=0o700)
            disks.mkdir(mode=0o700)
            process = mock.Mock(pid=1234)
            process.poll.return_value = None
            with mock.patch.object(runner, "DISKS", disks), mock.patch.object(runner, "validate_environment"), \
                 mock.patch.object(runner, "clone_trial_disk"), \
                 mock.patch.object(runner.METRICS, "host_memory", return_value={}), \
                 mock.patch.object(runner.subprocess, "check_output", return_value="QEMU test\n"), \
                 mock.patch.object(runner.subprocess, "Popen", return_value=process), \
                 mock.patch.object(runner, "read_console", side_effect=RuntimeError("size limit")), \
                 self.assertRaisesRegex(RuntimeError, "size limit"):
                runner.run_trial("oversize", "arm64", logs)
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=15)
            process.kill.assert_not_called()
            self.assertFalse((logs / "oversize.json").exists())

    def test_complete_trial_retains_raw_report_and_arguments_but_not_content_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            logs, disks = Path(directory) / "logs", Path(directory) / "disks"
            logs.mkdir(mode=0o700)
            disks.mkdir(mode=0o700)
            process = mock.Mock(pid=1234, returncode=0)
            process.poll.side_effect = [None, None, 0, 0]
            report = valid_report("x86_64")
            content = b"".join(markers()) + report_line(report)
            samples = [{"sample_monotonic": 1.0, "cpu_seconds": 1.0, "process_start": "own", "rss_kib": 100},
                       {"sample_monotonic": 2.0, "cpu_seconds": 1.5, "process_start": "own", "rss_kib": 200}]
            with mock.patch.object(runner, "DISKS", disks), mock.patch.object(runner, "validate_environment"), \
                 mock.patch.object(runner, "clone_trial_disk"), \
                 mock.patch.object(runner.METRICS, "host_memory", return_value={"counts": {"Swapins": 10}}), \
                 mock.patch.object(runner.METRICS, "process_sample", side_effect=samples), \
                 mock.patch.object(runner.subprocess, "check_output", return_value="QEMU test\n"), \
                 mock.patch.object(runner.subprocess, "Popen", return_value=process), \
                 mock.patch.object(runner.time, "monotonic", side_effect=[0.0, 1.0, 2.0, 3.0]), \
                 mock.patch.object(runner.time, "sleep"), \
                 mock.patch.object(runner, "read_console", side_effect=lambda path: content if "console" in path.name else b""):
                result = runner.run_trial("finished", "x86_64", logs)
            self.assertEqual(result["guest"], report)
            self.assertEqual(result["cpu_percent_one_core"], 50)
            self.assertEqual(result["sampled_peak_rss_kib"], 200)
            self.assertEqual(result["max_observed_poll_gap_seconds"], 1)
            self.assertIn("tcg,thread=multi", result["qemu_args"])
            self.assertEqual(json.loads((logs / "finished.json").read_text()), result)
            process.terminate.assert_not_called()
            process.kill.assert_not_called()

    def test_existing_evidence_prevents_cloning_and_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            logs, disks = Path(directory) / "logs", Path(directory) / "disks"
            logs.mkdir(mode=0o700)
            disks.mkdir(mode=0o700)
            (logs / "existing.json").write_text("prior evidence")
            with mock.patch.object(runner, "DISKS", disks), mock.patch.object(runner, "validate_environment"), \
                 mock.patch.object(runner, "clone_trial_disk") as clone, \
                 mock.patch.object(runner.subprocess, "Popen") as spawn, \
                 self.assertRaisesRegex(RuntimeError, "overwrite"):
                runner.run_trial("existing", "arm64", logs)
            clone.assert_not_called()
            spawn.assert_not_called()
            self.assertEqual((logs / "existing.json").read_text(), "prior evidence")


class PlanTests(unittest.TestCase):
    def test_default_and_smoke_plans_are_separate_and_preserve_abba_order(self):
        for option, expected in (([], ["arm64", "x86_64", "x86_64", "arm64"]),
                                 (["--smoke"], ["x86_64"]), (["--smoke-arm"], ["arm64"])):
            with self.subTest(option=option), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with mock.patch.object(runner, "DISKS", root), mock.patch.object(runner, "LOGS", root / "full"), \
                     mock.patch.object(runner, "SMOKE_X86_LOGS", root / "x86"), \
                     mock.patch.object(runner, "SMOKE_ARM_LOGS", root / "arm"), \
                     mock.patch.object(runner, "validate_environment"), mock.patch.object(runner.os, "umask"), \
                     mock.patch.object(runner, "run_trial", side_effect=lambda label, arch, logs: {"trial": label}) as run, \
                     mock.patch("sys.argv", ["run-architecture-benchmark.py"] + option):
                    runner.main()
                self.assertEqual([call.args[1] for call in run.call_args_list], expected)


if __name__ == "__main__":
    unittest.main()
