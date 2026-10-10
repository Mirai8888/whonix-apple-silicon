"""Host sampler parser/discovery fixtures; no real processes or VMs are read."""

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/measure-host.py"
SPEC = importlib.util.spec_from_file_location("measure_host", SCRIPT)
measure_host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(measure_host)

GATEWAY = "A1111111-1111-4111-8111-111111111111"
WORKSTATION = "B2222222-2222-4222-8222-222222222222"
PROCESS_START = "Thu Sep 17 08:57:58 2026"


def memory_output(swapins=100, swapouts=200, pageouts=300):
    return (
        "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
        "Pages free:                               5460.\n"
        '"Translation faults":                6284946782.\n'
        f"Pageouts: {pageouts}.\nSwapins: {swapins}.\nSwapouts: {swapouts}."
    )


class HostParserTests(unittest.TestCase):
    def test_cpu_time_accepts_macos_minutes_and_extended_durations(self):
        for value, expected in (
            ("0:00.00", 0),
            ("2:11.35", 131.35),
            ("123:45.67", 7425.67),
            ("1:02:03.45", 3723.45),
            ("2-01:02:03.45", 176523.45),
        ):
            with self.subTest(value=value):
                self.assertAlmostEqual(measure_host.cpu_seconds(value), expected)

    def test_process_fields_preserve_start_identity_and_rss_units(self):
        with mock.patch.object(measure_host, "command", return_value="  2:11.35    168352   " + PROCESS_START) as command, \
             mock.patch.object(measure_host.time, "monotonic", side_effect=(10.0, 14.0)):
            sample = measure_host.process_sample(1234)
        command.assert_called_once_with("/bin/ps", "-p", "1234", "-o", "time=,rss=,lstart=")
        self.assertEqual(sample["cpu_seconds"], 131.35)
        self.assertEqual(sample["rss_kib"], 168352)
        self.assertEqual(sample["process_start"], PROCESS_START)
        self.assertEqual(sample["sample_monotonic"], 12.0)

    def test_host_memory_preserves_page_counts_without_treating_them_as_bytes(self):
        with mock.patch.object(measure_host, "command", side_effect=[memory_output(), "total = 25600.00M used = 24491.50M"]) as command:
            result = measure_host.host_memory()
        self.assertEqual(command.call_args_list, [
            mock.call("/usr/bin/vm_stat"),
            mock.call("/usr/sbin/sysctl", "-n", "vm.swapusage"),
        ])
        self.assertEqual(result["page_size"], 16384)
        self.assertEqual(result["counts"]["Pages free"], 5460)
        self.assertEqual(result["counts"]['"Translation faults"'], 6284946782)
        self.assertEqual(result["counts"]["Swapins"], 100)
        self.assertEqual(result["swap_usage"], "total = 25600.00M used = 24491.50M")


class HostDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for role, identifier in (("Gateway", GATEWAY), ("Workstation", WORKSTATION)):
            config = self.root / "artifacts/utm" / ("Whonix-" + role + "-LXQt.utm") / "config.plist"
            config.parent.mkdir(parents=True)
            config.write_bytes(plistlib.dumps({"Information": {"UUID": identifier}}))
        self.listing = (
            "  10 /Applications/UTM.app/Contents/MacOS/QEMULauncher\n"
            "  20 /Applications/UTM.app/Contents/MacOS/QEMULauncher\n"
            "  30 /Applications/UTM.app/Contents/MacOS/QEMULauncher\n"
            "  40 /opt/homebrew/bin/qemu-system-aarch64\n"
            "  50 /Applications/Some Other App.app/Contents/MacOS/app"
        )
        self.arguments = {
            "10": "QEMULauncher -uuid " + GATEWAY.lower() + " -accel hvf -cpu host -smp cpus=2,sockets=1,cores=2,threads=1 -m 2048",
            "20": "QEMULauncher -uuid " + WORKSTATION + " -accel hvf -cpu host -smp cpus=4,sockets=1,cores=4,threads=1 -m 4096",
            "30": "QEMULauncher -uuid 99999999-9999-4999-8999-999999999999",
        }
        self.samples = {
            "10": ["2:11.35 168352 " + PROCESS_START, "2:12.65 149168 " + PROCESS_START],
            "20": ["4:53.72 283760 " + PROCESS_START, "4:56.02 272592 " + PROCESS_START],
        }
        self.memory_reads = 0
        self.calls = []
        self.now = 100.0

    def command(self, *args):
        self.calls.append(args)
        if args == ("/bin/ps", "-axo", "pid=,comm="):
            return self.listing
        if args[:2] == ("/bin/ps", "-ww"):
            return self.arguments[args[3]]
        if args[:2] == ("/bin/ps", "-p"):
            return self.samples[args[2]].pop(0)
        if args == ("/usr/bin/vm_stat",):
            self.memory_reads += 1
            return memory_output() if self.memory_reads == 1 else memory_output(109, 202, 305)
        if args == ("/usr/sbin/sysctl", "-n", "vm.swapusage"):
            return "total = 25600.00M used = 24491.50M"
        self.fail("Unexpected host command: " + repr(args))

    def sleep(self, seconds):
        self.now += seconds

    def run_main(self):
        output = io.StringIO()
        with mock.patch.object(measure_host, "ROOT", self.root), \
             mock.patch.object(measure_host, "command", side_effect=self.command), \
             mock.patch.object(measure_host.time, "monotonic", side_effect=lambda: self.now), \
             mock.patch.object(measure_host.time, "sleep", side_effect=self.sleep), \
             mock.patch("sys.argv", [str(SCRIPT), "--seconds", "30"]), \
             redirect_stdout(output):
            measure_host.main()
        return json.loads(output.getvalue())

    def test_only_matching_utm_guests_are_sampled_and_rates_use_one_core(self):
        result = self.run_main()
        self.assertEqual(set(result["guests"]), {"Gateway", "Workstation"})
        self.assertEqual(result["guests"]["Gateway"]["pid"], 10)
        self.assertEqual(result["guests"]["Workstation"]["pid"], 20)
        self.assertEqual(result["guests"]["Gateway"]["cpu_percent_one_core"], 4.333)
        self.assertEqual(result["guests"]["Workstation"]["cpu_percent_one_core"], 7.667)
        self.assertEqual(result["host_io_delta_pages"], {"Swapins": 9, "Swapouts": 2, "Pageouts": 5})
        self.assertEqual(result["duration_seconds"], 30)
        self.assertIn("100% is one CPU core", result["limits"])
        self.assertIn("host-wide", result["limits"])
        self.assertIn("not a native-performance benchmark", result["limits"])
        self.assertFalse(any("40" in args or "50" in args for args in self.calls))
        self.assertFalse(any(args[:3] == ("/bin/ps", "-p", "30") for args in self.calls))

    def test_duplicate_matching_processes_are_rejected(self):
        self.listing += "\n60 /Applications/UTM.app/Contents/MacOS/QEMULauncher"
        self.arguments["60"] = self.arguments["10"]
        with self.assertRaisesRegex(RuntimeError, "Multiple processes match Gateway"):
            self.run_main()
        self.assertEqual(self.now, 100)

    def test_duplicate_configured_uuids_are_rejected_before_process_discovery(self):
        config = self.root / "artifacts/utm/Whonix-Workstation-LXQt.utm/config.plist"
        config.write_bytes(plistlib.dumps({"Information": {"UUID": GATEWAY.lower()}}))
        with self.assertRaisesRegex(RuntimeError, "Gateway and Workstation UUIDs must differ"):
            self.run_main()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.now, 100)

    def test_slow_discovery_and_memory_commands_do_not_inflate_cpu_rates(self):
        normal_command = self.command
        rates = {"10": 0.2, "20": 0.4}
        sample_delays = {"10": 2.0, "20": 4.0}

        def delayed_command(*args):
            if args[:2] == ("/bin/ps", "-p"):
                self.calls.append(args)
                pid = args[2]
                delay = sample_delays[pid]
                # Simulate constant 20%/40% one-core loads. ps observes the
                # cumulative CPU counter halfway through its own slow read.
                observed_at = self.now + delay / 2
                self.now += delay
                minutes, seconds = divmod(observed_at * rates[pid], 60)
                return f"{int(minutes)}:{seconds:05.2f} 123456 {PROCESS_START}"
            result = normal_command(*args)
            if args == ("/bin/ps", "-axo", "pid=,comm="):
                self.now += 7
            elif args[:2] == ("/bin/ps", "-ww"):
                self.now += 5
            elif args == ("/usr/bin/vm_stat",):
                self.now += 3
            elif args == ("/usr/sbin/sysctl", "-n", "vm.swapusage"):
                self.now += 11
            return result

        with mock.patch.object(self, "command", side_effect=delayed_command):
            result = self.run_main()
        self.assertEqual(result["duration_seconds"], 30)
        gateway = result["guests"]["Gateway"]
        workstation = result["guests"]["Workstation"]
        self.assertEqual(gateway["sample_seconds"], 60)
        self.assertEqual(workstation["sample_seconds"], 55)
        self.assertEqual(gateway["cpu_percent_one_core"], 20)
        self.assertEqual(workstation["cpu_percent_one_core"], 40)

    def test_wrong_accelerator_or_cpu_is_rejected_before_sampling_interval(self):
        original = self.arguments["10"]
        for old, new in (("-accel hvf", "-accel tcg"), ("-cpu host", "-cpu cortex-a72")):
            with self.subTest(replacement=new):
                self.arguments["10"] = original.replace(old, new)
                with self.assertRaisesRegex(RuntimeError, "expected native HVF CPU"):
                    self.run_main()
                self.assertEqual(self.now, 100)

    def test_changed_process_identity_aborts_without_a_measurement(self):
        self.samples["20"][1] = "4:56.02 272592 Thu Sep 17 09:00:00 2026"
        with self.assertRaisesRegex(RuntimeError, "Workstation process changed"):
            self.run_main()

    def test_no_matching_utm_guests_does_not_sample_headless_qemu(self):
        self.listing = "40 /opt/homebrew/bin/qemu-system-aarch64"
        with self.assertRaisesRegex(RuntimeError, "No running Whonix UTM guests"):
            self.run_main()
        self.assertEqual(self.now, 100)
        self.assertEqual(self.calls, [("/bin/ps", "-axo", "pid=,comm=")])


if __name__ == "__main__":
    unittest.main()
