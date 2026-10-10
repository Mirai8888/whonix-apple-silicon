import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/run-headless-benchmark.py'
SPEC = importlib.util.spec_from_file_location('headless_benchmark', SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class ConsoleBoundaryTests(unittest.TestCase):
    def test_final_console_read_rejects_oversized_sparse_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'console.log'
            with path.open('wb') as stream:
                stream.truncate(1024 * 1024 * 1024)
            with self.assertRaisesRegex(RuntimeError, 'size'):
                runner.read_console(path)

    def test_regular_console_and_absent_startup_log(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'console.log'
            self.assertEqual(runner.read_console(path), b'')
            path.write_bytes(b'WHONIX_BENCH_BEGIN\r\n')
            self.assertEqual(runner.read_console(path), b'WHONIX_BENCH_BEGIN\r\n')

    def test_console_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'target'
            target.write_bytes(b'not a console')
            link = Path(directory) / 'console.log'
            link.symlink_to(target)
            with self.assertRaises(OSError):
                runner.read_console(link)


class CloneConfidentialityTests(unittest.TestCase):
    def test_clone_is_private_even_when_copy_preserves_public_template_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            disks = Path(directory)
            disks.chmod(0o700)
            source, destination = disks / 'template.raw', disks / 'trial.raw'
            source.write_bytes(b'guest private state')
            source.chmod(0o644)

            def copy(argv, check):
                self.assertEqual(argv, ['/bin/cp', '-c', str(source), str(destination)])
                self.assertTrue(check)
                self.assertEqual(stat.S_IMODE(disks.stat().st_mode), 0o700)
                destination.write_bytes(source.read_bytes())
                destination.chmod(0o644)  # Reproduce macOS clone mode preservation.
                return subprocess.CompletedProcess(argv, 0)

            with mock.patch.object(runner, 'DISKS', disks), \
                 mock.patch.object(runner.subprocess, 'run', side_effect=copy) as command:
                runner.clone_trial_disk(source, destination)
                command.assert_called_once()
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o644)

    def test_loose_or_wrong_owner_directory_fails_before_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            disks = Path(directory)
            source, destination = disks / 'template.raw', disks / 'trial.raw'
            source.write_bytes(b'guest private state')
            with mock.patch.object(runner, 'DISKS', disks), \
                 mock.patch.object(runner.subprocess, 'run') as command:
                for mode in (0o755, 0o750, 0o770):
                    with self.subTest(mode=oct(mode)):
                        disks.chmod(mode)
                        with self.assertRaisesRegex(RuntimeError, 'mode 0700'):
                            runner.clone_trial_disk(source, destination)
                disks.chmod(0o700)
                other_uid = os.geteuid() + 1
                with mock.patch.object(runner.os, 'geteuid', return_value=other_uid):
                    with self.assertRaisesRegex(RuntimeError, 'caller-owned'):
                        runner.clone_trial_disk(source, destination)
                command.assert_not_called()
            self.assertFalse(destination.exists())

    def test_directory_symlink_file_and_missing_directory_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            real = parent / 'real'
            real.mkdir(mode=0o700)
            link = parent / 'link'
            link.symlink_to(real, target_is_directory=True)
            regular = parent / 'file'
            regular.touch(mode=0o700)
            for path in (link, regular, parent / 'missing'):
                with self.subTest(path=path.name), mock.patch.object(runner, 'DISKS', path):
                    with self.assertRaisesRegex(RuntimeError, 'directory.*0700'):
                        runner.require_private_disk_directory()

    def test_existing_and_symlink_destinations_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            disks = Path(directory)
            disks.chmod(0o700)
            source, destination = disks / 'template.raw', disks / 'trial.raw'
            source.write_bytes(b'template')
            with mock.patch.object(runner, 'DISKS', disks), \
                 mock.patch.object(runner.subprocess, 'run') as command:
                destination.write_bytes(b'existing evidence')
                with self.assertRaisesRegex(RuntimeError, 'overwrite'):
                    runner.clone_trial_disk(source, destination)
                self.assertEqual(destination.read_bytes(), b'existing evidence')
                destination.unlink()
                destination.symlink_to(disks / 'absent')
                with self.assertRaisesRegex(RuntimeError, 'overwrite'):
                    runner.clone_trial_disk(source, destination)
                command.assert_not_called()

    def test_linked_templates_and_paths_outside_private_directory_fail_before_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            disks = parent / 'private'
            disks.mkdir(mode=0o700)
            source, destination = disks / 'template.raw', disks / 'trial.raw'
            source.write_bytes(b'template')
            link = disks / 'linked.raw'
            link.symlink_to(source)
            with mock.patch.object(runner, 'DISKS', disks), \
                 mock.patch.object(runner.subprocess, 'run') as command:
                for candidate, output in ((link, destination), (source, parent / 'outside.raw')):
                    with self.subTest(candidate=candidate.name, output=output.name):
                        with self.assertRaises(RuntimeError):
                            runner.clone_trial_disk(candidate, output)
                link.unlink()
                os.link(source, link)
                with self.assertRaisesRegex(RuntimeError, 'without links'):
                    runner.clone_trial_disk(source, destination)
                command.assert_not_called()

    def test_unexpected_copy_output_is_rejected_without_chmodding_link_target(self):
        with tempfile.TemporaryDirectory() as directory:
            disks = Path(directory)
            disks.chmod(0o700)
            source, destination = disks / 'template.raw', disks / 'trial.raw'
            source.write_bytes(b'template')
            source.chmod(0o644)

            def copy(argv, check):
                destination.symlink_to(source)
                return subprocess.CompletedProcess(argv, 0)

            with mock.patch.object(runner, 'DISKS', disks), \
                 mock.patch.object(runner.subprocess, 'run', side_effect=copy):
                with self.assertRaises(OSError):
                    runner.clone_trial_disk(source, destination)
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o644)
