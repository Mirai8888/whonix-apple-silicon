"""Fixtures for the additive kloak ARM64 build hook; never mount a guest."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "scripts" / "95-arm64-kloak-syscalls"
DROP_IN = Path("etc/systemd/system/kloak.service.d/50-arm64-syscalls.conf")
FILTER = "SystemCallFilter=faccessat ppoll epoll_pwait unlinkat"


class KloakHookTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.guest = self.root / "guest"
        (self.guest / "etc").mkdir(parents=True)
        self.environment = dict(os.environ, MNTPOINT=str(self.guest))
        # The production hook runs on Debian. Use the same GNU file utilities
        # for unprivileged macOS fixtures when Homebrew coreutils is installed.
        coreutils = Path("/opt/homebrew/opt/coreutils/libexec/gnubin")
        if coreutils.is_dir():
            self.environment["PATH"] = str(coreutils) + os.pathsep + self.environment["PATH"]

    def run_hook(self, body="install_drop_in", **environment):
        return subprocess.run(
            ["/bin/bash", "-c", 'source "$1"\n' + body, "fixture", str(HOOK)],
            env=dict(self.environment, **environment), text=True, capture_output=True,
        )

    def test_adds_only_expected_syscalls_and_preserves_existing_unit(self):
        unit = self.guest / "usr/lib/systemd/system/kloak.service"
        unit.parent.mkdir(parents=True)
        # The hook must leave an existing unit byte-for-byte intact. A small
        # stand-in keeps this regression runnable without an upstream checkout;
        # the actual packaged unit was checked separately during VM validation.
        original = (b"[Service]\nExecStart=/usr/sbin/kloak\n"
                    b"NoNewPrivileges=yes\n"
                    b"SystemCallFilter=access poll epoll_wait unlink\n")
        unit.write_bytes(original)
        for attempt in range(2):
            with self.subTest(attempt=attempt):
                result = self.run_hook()
                self.assertEqual(result.returncode, 0, result.stderr)
                config = self.guest / DROP_IN
                lines = config.read_text().splitlines()
                self.assertEqual(lines[0], "[Service]")
                self.assertEqual([line for line in lines if line.startswith("SystemCallFilter=")], [FILTER])
                self.assertEqual(config.stat().st_mode & 0o777, 0o644)
                self.assertEqual(unit.read_bytes(), original)
                self.assertEqual(list(config.parent.glob(".50_arm64_syscalls.*")), [])

    def test_skips_other_architectures_and_flavors_before_host_checks(self):
        for arch, flavor in (
            ("amd64", "whonix-workstation-lxqt"),
            ("arm64", "whonix-gateway-lxqt"),
            ("arm64", "kicksecure-lxqt"),
            ("", ""),
        ):
            with self.subTest(arch=arch, flavor=flavor):
                result = self.run_hook(
                    'id() { return 99; }\nmain',
                    dist_build_target_arch=arch, dist_build_flavor=flavor,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.guest / DROP_IN).exists())

    def test_requires_root_and_expected_mountpoint_before_writing(self):
        for root_output, expected in (("1000", "must run as root"), ("0", "unexpected MNTPOINT")):
            with self.subTest(root_output=root_output):
                result = self.run_hook(
                    'id() { printf "%s\\n" ' + root_output + '; }\nmain',
                    dist_build_target_arch="arm64", dist_build_flavor="whonix-workstation-lxqt",
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
                self.assertFalse((self.guest / DROP_IN).exists())

    def test_rejects_symlinked_directory_components(self):
        for component in ("systemd", "systemd/system", "systemd/system/kloak.service.d"):
            with self.subTest(component=component):
                case = self.root / component.replace("/", "-")
                guest = case / "guest"
                target = guest / "etc" / component
                target.parent.mkdir(parents=True)
                outside = case / "outside"
                outside.mkdir()
                target.symlink_to(outside, target_is_directory=True)
                result = self.run_hook(MNTPOINT=str(guest))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlinks", result.stderr)
                self.assertEqual(list(outside.iterdir()), [])

    def test_rejects_symlink_or_directory_drop_in(self):
        target = self.guest / DROP_IN
        target.parent.mkdir(parents=True)
        outside = self.root / "outside.conf"
        outside.write_text("unrelated configuration\n")
        target.symlink_to(outside)
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-regular", result.stderr)
        self.assertEqual(outside.read_text(), "unrelated configuration\n")
        target.unlink()
        target.mkdir()
        result = self.run_hook()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-regular", result.stderr)
        self.assertEqual(list(target.iterdir()), [])

    def test_failed_replacement_preserves_existing_config_and_cleans_temporary(self):
        target = self.guest / DROP_IN
        target.parent.mkdir(parents=True)
        target.write_text("existing configuration\n")
        result = self.run_hook('mv() { return 42; }\ninstall_drop_in')
        self.assertEqual(result.returncode, 42)
        self.assertEqual(target.read_text(), "existing configuration\n")
        self.assertEqual(list(target.parent.glob(".50_arm64_syscalls.*")), [])


if __name__ == "__main__":
    unittest.main()
