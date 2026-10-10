import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('browser_runner', ROOT / 'scripts/run-browser-benchmark.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def report_line(value):
    return runner.REPORT_PREFIX + base64.b64encode(json.dumps(value).encode()) + b'\r\n'


def valid_report(cpus=4):
    return {'status': 'captured-unvalidated', 'schema_version': 1, 'architecture': 'aarch64',
            'cpu_count': cpus, 'errors': [], 'workload_sha256': 'a' * 64,
            'launches': [{'status': 'captured-unvalidated', 'index': i,
                          'kind': 'cold' if i == 1 else 'warm', 'returncode': 0,
                          'timed_out': False, 'duration_seconds': 2.5,
                          'screenshot': {'sha256': 'f' * 64, 'bytes': 100,
                                         'dimensions': [1920, 1080], 'workload_content_validated': False}}
                         for i in range(1, 5)]}


class ReportTests(unittest.TestCase):
    def test_waits_for_complete_line(self):
        line = report_line(valid_report())
        self.assertIsNone(runner.parse_report(line[:-2]))
        self.assertEqual(runner.parse_report(b'boot\r\n' + line), valid_report())

    def test_rejects_duplicate_reports_and_malformed_payloads(self):
        line = report_line(valid_report())
        for data in (line + line, runner.REPORT_PREFIX + b'!broken!\n',
                     runner.REPORT_PREFIX + base64.b64encode(b'not json') + b'\n',
                     report_line([])):
            with self.subTest(data=data[:50]), self.assertRaises(RuntimeError):
                runner.parse_report(data)

    def test_rejects_duplicate_json_keys_and_nonfinite_values(self):
        for payload in (b'{"status":"error","status":"ok"}', b'{"time":NaN}',
                        b'{"time":Infinity}', b'{"nested":[1e999]}'):
            with self.subTest(payload=payload), self.assertRaisesRegex(RuntimeError, 'Malformed'):
                runner.parse_report(runner.REPORT_PREFIX + base64.b64encode(payload) + b'\n')

    def test_rejects_wrong_cpu_partial_launches_and_guest_failure(self):
        candidates = [valid_report(2), {'status': 'ok', 'cpu_count': True, 'launches': []},
                      {'status': 'error', 'cpu_count': 4, 'launches': []}]
        fewer = valid_report()
        fewer['launches'].pop()
        failed = valid_report()
        failed['launches'][2]['status'] = 'timeout'
        candidates += [fewer, failed]
        for value in candidates:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                runner.validate_report(value, 4)
        runner.validate_report(valid_report(), 4)

    def test_workload_digest_and_capture_metadata_are_validated_without_trusting_paths(self):
        value = valid_report()
        with self.assertRaisesRegex(RuntimeError, 'workload'):
            runner.validate_report(value, 4, 'b' * 64)
        runner.validate_report(value, 4, 'a' * 64)
        for section, field, bad in [('launch', 'returncode', 1), ('launch', 'timed_out', True),
                                    ('launch', 'duration_seconds', float('inf')),
                                    ('launch', 'index', 4), ('screenshot', 'dimensions', [0, 1920]),
                                    ('screenshot', 'sha256', 'invalid'),
                                    ('screenshot', 'workload_content_validated', True)]:
            value = valid_report()
            target = value['launches'][0]
            if section == 'screenshot':
                target = target['screenshot']
            target[field] = bad
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                runner.validate_report(value, 4)


class EnvironmentGuardTests(unittest.TestCase):
    def test_listener_requires_exact_endpoint_and_actual_uuid_argument(self):
        listener = 'p123\nn127.0.0.1:8010\n'
        command = '/Applications/UTM.app/Contents/XPCServices/QEMULauncher -uuid ABC -name VM'
        runner.validate_listener(listener, command, 'abc')
        cases = [(listener.replace('127.0.0.1', '*'), command),
                 (listener + 'p124\nn127.0.0.1:8010\n', command),
                 (listener, command.replace('QEMULauncher', 'unrelated')),
                 (listener, command.replace('-uuid ABC', '-name ABC -uuid OTHER')),
                 (listener, command + ' -uuid ABC')]
        for data, argv in cases:
            with self.subTest(data=data, argv=argv), self.assertRaises(RuntimeError):
                runner.validate_listener(data, argv, 'ABC')

    def test_private_directory_guard_rejects_symlinks_and_public_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'disks'
            path.mkdir(mode=0o700)
            runner.require_private_directory(path)
            path.chmod(0o750)
            with self.assertRaises(RuntimeError):
                runner.require_private_directory(path)
            link = Path(directory) / 'link'
            link.symlink_to(path, target_is_directory=True)
            with self.assertRaises(RuntimeError):
                runner.require_private_directory(link)

    def test_template_rejects_foreign_writable_or_hard_linked_disk_before_lsof(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'template.raw'
            path.write_bytes(b'raw')
            with mock.patch.object(runner.subprocess, 'run') as command:
                path.chmod(0o666)
                with self.assertRaises(ValueError):
                    runner.require_unused(path)
                path.chmod(0o600)
                os.link(path, Path(directory) / 'link.raw')
                with self.assertRaises(ValueError):
                    runner.require_unused(path)
                command.assert_not_called()

    def test_lsof_errors_and_open_disks_are_not_treated_as_unused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'template.raw'
            path.write_bytes(b'raw')
            for code, out, err in ((0, '123\n', ''), (2, '', ''), (1, '', 'error')):
                with self.subTest(code=code), mock.patch.object(runner.subprocess, 'run',
                        return_value=subprocess.CompletedProcess([], code, out, err)), self.assertRaises(RuntimeError):
                    runner.require_unused(path)

    def test_qemu_arguments_preserve_hvf_no_shares_and_single_internal_nic(self):
        argv = runner.qemu_arguments(Path('/private/disk.raw'), Path('/private/console'), 'test', 'uuid', 2)
        self.assertEqual(argv[argv.index('-machine') + 1], 'virt,accel=hvf')
        self.assertEqual(argv[argv.index('-smp') + 1], '2')
        self.assertEqual(argv[argv.index('-m') + 1], '4096')
        self.assertEqual(argv.count('-netdev'), 1)
        self.assertEqual(argv[argv.index('-netdev') + 1], 'socket,id=internal,connect=127.0.0.1:8010')
        self.assertEqual(argv[argv.index('-display') + 1], 'none')
        self.assertIn('virtio-gpu-pci', argv)
        self.assertIn('-nodefaults', argv)
        self.assertIn('format=raw', argv[argv.index('-drive') + 1])
        self.assertIn('discard=ignore,detect-zeroes=off', argv[argv.index('-drive') + 1])
        for forbidden in ('-virtfs', '-fsdev', '-spice', '-qmp', '-monitor'):
            self.assertNotIn(forbidden, argv)
        for cpus in (1, 8, True, '4'):
            with self.subTest(cpus=cpus), self.assertRaises(RuntimeError):
                runner.qemu_arguments(Path('/d'), Path('/c'), 'test', 'uuid', cpus)


class HostAccountingTests(unittest.TestCase):
    def test_measures_actual_interval_and_checks_identity(self):
        before = {'sample_monotonic': 10.0, 'cpu_seconds': 2.0, 'process_start': 'original'}
        after = {'sample_monotonic': 20.0, 'cpu_seconds': 3.0, 'process_start': 'original'}
        result = runner.collect_process_metrics(before, after, 100)
        self.assertEqual(result['cpu_percent_one_core'], 10.0)
        self.assertEqual(result['sampled_peak_rss_kib'], 100)
        for field, value in (('sample_monotonic', 10.0), ('cpu_seconds', 1.0),
                             ('process_start', 'replacement')):
            changed = dict(after, **{field: value})
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                runner.collect_process_metrics(before, changed, 100)

    def test_existing_results_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            path.write_text('existing evidence')
            with self.assertRaises(FileExistsError):
                runner.write_json_new(path, {'replace': True})
            self.assertEqual(path.read_text(), 'existing evidence')


class ChildLifetimeTests(unittest.TestCase):
    def test_deadline_terminates_only_created_child_and_escalates_if_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory) / 'logs'
            logs.mkdir(mode=0o700)
            disks = Path(directory) / 'disks'
            disks.mkdir(mode=0o700)
            process = mock.Mock()
            process.pid = 1234
            process.poll.return_value = None
            process.wait.side_effect = [subprocess.TimeoutExpired('own-child', 15), 0]
            with mock.patch.object(runner, 'DISKS', disks), \
                 mock.patch.object(runner, 'validate_environment'), \
                 mock.patch.object(runner, 'clone_trial_disk'), \
                 mock.patch.object(runner.METRICS, 'host_memory', return_value={}), \
                 mock.patch.object(runner.subprocess, 'check_output', return_value='QEMU test\n'), \
                 mock.patch.object(runner.subprocess, 'Popen', return_value=process) as spawn, \
                 mock.patch.object(runner.time, 'monotonic', side_effect=[0.0, 361.0]), \
                 self.assertRaisesRegex(RuntimeError, '360 seconds'):
                runner.run_trial('deadline', 4, logs)
            spawn.assert_called_once()
            process.terminate.assert_called_once_with()
            process.kill.assert_called_once_with()
            self.assertEqual(process.wait.call_args_list, [mock.call(timeout=15), mock.call(timeout=10)])
            self.assertTrue((logs / 'deadline-qemu.log').exists())
            self.assertFalse((logs / 'deadline.json').exists())

    def test_environment_failure_prevents_cloning_or_launch(self):
        with mock.patch.object(runner, 'validate_environment', side_effect=RuntimeError('unsafe')),\
             mock.patch.object(runner, 'clone_trial_disk') as clone,\
             mock.patch.object(runner.subprocess, 'Popen') as spawn,\
             self.assertRaisesRegex(RuntimeError, 'unsafe'):
            runner.run_trial('refused', 4, Path('/unused'))
        clone.assert_not_called()
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
