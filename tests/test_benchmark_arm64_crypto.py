"""Synthetic OpenSSL fixtures; no real crypto workloads or guests are started."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark-arm64-crypto.py"
SPEC = importlib.util.spec_from_file_location("benchmark_arm64_crypto", SCRIPT)
crypto = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(crypto)

HELP = "\n".join(" -" + option + "  supported" for option in ("mr", "elapsed", "seconds", "bytes", "evp"))


def speed_output(algorithm="aes-256-gcm", rate="123456789.25"):
    label = "AES-256-GCM" if algorithm == "aes-256-gcm" else "sha256"
    return f"+H:16384\n+F:25:{label}:{rate}\n"


class SpeedParserTests(unittest.TestCase):
    def test_machine_readable_rates_are_bytes_per_second(self):
        for algorithm in ("aes-256-gcm", "sha256"):
            with self.subTest(algorithm=algorithm):
                self.assertEqual(crypto.parse_speed_output(speed_output(algorithm), algorithm), 123456789.25)

    def test_rejects_missing_ambiguous_or_wrong_buffer_and_algorithm_results(self):
        valid = speed_output()
        invalid = (
            "", "+H:16384\n", "+F:25:AES-256-GCM:123.45\n",
            valid.replace("16384", "8192"),
            valid.replace("+H:16384", "+H:8192:16384"),
            valid + "+H:16384\n",
            valid + "+F:25:AES-256-GCM:99.00\n",
            valid.replace("AES-256-GCM", "aes-128-gcm"),
            valid.replace("+F:25:", "+F2:25:"),
            valid.replace("+F:25:", "+F:invalid:"),
            valid.replace("123456789.25", "123.45:456.78"),
        )
        for output in invalid:
            with self.subTest(output=output):
                with self.assertRaises(crypto.BenchmarkError):
                    crypto.parse_speed_output(output, "aes-256-gcm")

    def test_rejects_malformed_nonfinite_zero_and_negative_throughput(self):
        for rate in ("", "not-a-number", "NaN", "nan", "inf", "-inf", "1e999", "0", "-1"):
            with self.subTest(rate=rate):
                with self.assertRaises(crypto.BenchmarkError):
                    crypto.parse_speed_output(speed_output(rate=rate), "aes-256-gcm")

    def test_help_requires_every_fixed_command_option(self):
        crypto.check_speed_help(HELP)
        for option in ("mr", "elapsed", "seconds", "bytes", "evp"):
            with self.subTest(option=option):
                with self.assertRaises(crypto.BenchmarkError):
                    crypto.check_speed_help(HELP.replace(" -" + option + " ", " -unsupported "))

    def test_cpu_features_preserve_distinct_reported_lines(self):
        info = "processor: 0\nFeatures\t: fp asimd aes sha2\nprocessor: 1\nFeatures: fp  asimd aes sha2\nFeatures: fp asimd\n"
        self.assertEqual(crypto.parse_cpu_features(info), ["fp asimd aes sha2", "fp asimd"])
        with self.assertRaises(crypto.BenchmarkError):
            crypto.parse_cpu_features("processor: 0\n")


class ChildExecutionTests(unittest.TestCase):
    def test_armcap_changes_are_child_only_and_auto_removes_inherited_override(self):
        environment = {"OPENSSL_armcap": "inherited-value", "PATH": "/usr/bin", "KEEP": "unchanged"}
        completed = subprocess.CompletedProcess([], 0, stdout="output\n", stderr="diagnostic\n")
        with mock.patch.dict(os.environ, environment, clear=True), \
             mock.patch.object(crypto.time, "monotonic", return_value=100), \
             mock.patch.object(crypto.subprocess, "run", return_value=completed) as run:
            for mode in ("auto", "armcap-disabled", "auto"):
                result = crypto.run_openssl("/usr/bin/openssl", ["version"], mode, 130)
                self.assertEqual(result["stdout"], "output\n")
                self.assertEqual(result["stderr"], "diagnostic\n")
                self.assertEqual(dict(os.environ), environment)
            calls = run.call_args_list
        self.assertNotIn("OPENSSL_armcap", calls[0].kwargs["env"])
        self.assertEqual(calls[1].kwargs["env"]["OPENSSL_armcap"], "0")
        self.assertNotIn("OPENSSL_armcap", calls[2].kwargs["env"])
        for call in calls:
            self.assertEqual(call.args, (["/usr/bin/openssl", "version"],))
            self.assertEqual(call.kwargs["env"]["KEEP"], "unchanged")
            self.assertEqual(call.kwargs["timeout"], 15)
            self.assertNotIn("shell", call.kwargs)
            self.assertTrue(call.kwargs["capture_output"])

    def test_child_timeout_uses_remaining_total_budget(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with mock.patch.object(crypto.time, "monotonic", return_value=100), \
             mock.patch.object(crypto.subprocess, "run", return_value=completed) as run:
            crypto.run_openssl("/usr/bin/openssl", ["version"], "auto", 101.25)
            self.assertEqual(run.call_args.kwargs["timeout"], 1.25)
            with self.assertRaisesRegex(crypto.BenchmarkError, "total time limit"):
                crypto.run_openssl("/usr/bin/openssl", ["version"], "auto", 100)
            self.assertEqual(run.call_count, 1)

    def test_timeout_and_failed_commands_do_not_produce_measurements(self):
        with mock.patch.object(crypto.time, "monotonic", return_value=100), \
             mock.patch.object(crypto.subprocess, "run", side_effect=subprocess.TimeoutExpired("openssl", 15)):
            with self.assertRaisesRegex(crypto.BenchmarkError, "timed out"):
                crypto.run_openssl("/usr/bin/openssl", ["version"], "auto", 130)
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="algorithm unavailable")
        with mock.patch.object(crypto.time, "monotonic", return_value=100), \
             mock.patch.object(crypto.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(crypto.BenchmarkError, "algorithm unavailable"):
                crypto.run_openssl("/usr/bin/openssl", ["version"], "auto", 130)

    def test_platform_gate_rejects_non_linux_or_non_aarch64(self):
        for system, machine in (("Darwin", "arm64"), ("Linux", "x86_64"), ("Linux", "arm64")):
            with self.subTest(system=system, machine=machine), \
                 mock.patch.object(crypto.platform, "system", return_value=system), \
                 mock.patch.object(crypto.platform, "machine", return_value=machine), \
                 mock.patch.object(crypto.subprocess, "run") as run:
                with self.assertRaises(crypto.BenchmarkError):
                    crypto.benchmark()
                run.assert_not_called()


class BenchmarkTests(unittest.TestCase):
    def test_alternating_modes_raw_samples_and_median_ratios(self):
        calls = []
        rates = {
            ("aes-256-gcm", "auto"): iter((100, 300, 200)),
            ("aes-256-gcm", "armcap-disabled"): iter((40, 60, 50)),
            ("sha256", "auto"): iter((900, 700, 800)),
            ("sha256", "armcap-disabled"): iter((100, 300, 200)),
        }

        def run(executable, arguments, mode, deadline):
            calls.append((arguments, mode, deadline))
            self.assertEqual(executable, "/usr/bin/openssl")
            if arguments == ["version"]:
                return {"stdout": "OpenSSL 3.5.0 fixture\n", "stderr": "", "elapsed_seconds": 0.01}
            if arguments == ["speed", "-help"]:
                return {"stdout": "", "stderr": HELP, "elapsed_seconds": 0.01}
            algorithm = arguments[-1]
            self.assertEqual(arguments, ["speed", "-mr", "-elapsed", "-seconds", "1", "-bytes", "16384", "-evp", algorithm])
            return {"stdout": speed_output(algorithm, str(next(rates[(algorithm, mode)]))),
                    "stderr": "+DT:evp:1:16384\n+R:100:evp:1.000000\n", "elapsed_seconds": 1.01}

        with mock.patch.object(crypto.platform, "system", return_value="Linux"), \
             mock.patch.object(crypto.platform, "machine", return_value="aarch64"), \
             mock.patch.object(crypto.Path, "read_text", return_value="Features: fp asimd aes sha2\n"), \
             mock.patch.object(crypto.shutil, "which", return_value="/usr/bin/openssl"), \
             mock.patch.object(crypto.time, "monotonic", side_effect=(100, 112.2)), \
             mock.patch.object(crypto, "run_openssl", side_effect=run):
            result = crypto.benchmark()
        expected_order = []
        for algorithm in ("aes-256-gcm", "sha256"):
            expected_order.extend((algorithm, repetition, mode) for repetition, mode in (
                (1, "auto"), (1, "armcap-disabled"),
                (2, "armcap-disabled"), (2, "auto"),
                (3, "auto"), (3, "armcap-disabled"),
            ))
        self.assertEqual([(sample["algorithm"], sample["repetition"], sample["mode"]) for sample in result["samples"]], expected_order)
        self.assertEqual(len(calls), 14)
        self.assertTrue(all(deadline == 130 for _, _, deadline in calls))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["summary"]["aes-256-gcm"]["median_bytes_per_second"], {"auto": 200, "armcap-disabled": 50})
        self.assertEqual(result["summary"]["sha256"]["median_bytes_per_second"], {"auto": 800, "armcap-disabled": 200})
        self.assertTrue(all(summary["auto_over_armcap_disabled_ratio"] == 4 for summary in result["summary"].values()))
        self.assertEqual(result["cpuinfo_features"], ["fp asimd aes sha2"])
        self.assertIn("not Tor throughput", result["limits"])
        self.assertIn("may still use SIMD", result["limits"])
        self.assertTrue(all("+R:" in sample["stderr"] for sample in result["samples"]))
        json.dumps(result, allow_nan=False)

    def test_cli_failure_is_one_parseable_json_document(self):
        output = io.StringIO()
        with mock.patch.object(crypto, "benchmark", side_effect=crypto.BenchmarkError("fixture failure")), redirect_stdout(output):
            status = crypto.main([])
        self.assertEqual(status, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error"], "fixture failure")


if __name__ == "__main__":
    unittest.main()
