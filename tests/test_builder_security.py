"""Builder security checks use disposable files and stubbed VM/SSH tools."""

import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

STUB = r'''
import json
import os
from pathlib import Path
import stat
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
state = json.loads(Path(os.environ["FIXTURE_STATE"]).read_text())
with open(os.environ["FIXTURE_TRACE"], "a") as stream:
    stream.write(json.dumps({"tool": tool, "args": args}) + "\n")
if tool == "uname":
    print("Darwin" if args == ["-s"] else "arm64")
elif tool == "stat":
    assert args[0] == "-f" and len(args) == 3
    path = Path(args[2])
    info = path.lstat()
    if args[1] == "%u":
        print(info.st_uid + (1 if str(path).endswith(state.get("foreign_owner", "NO-MATCH")) else 0))
    elif args[1] == "%Lp":
        print(format(stat.S_IMODE(info.st_mode), "o"))
    elif args[1] == "%l":
        print(info.st_nlink)
    else:
        raise AssertionError(args)
elif tool == "lsof":
    kind = "disk" if args[:2] == ["-t", "--"] else "port"
    if kind == "port":
        assert args == ["-nP", "-iTCP:22022", "-sTCP:LISTEN", "-t"]
    result = state.get("lsof_" + kind, {})
    if result.get("stderr"):
        print(result["stderr"], file=sys.stderr)
    if result.get("stdout"):
        print(result["stdout"])
    sys.exit(result.get("status", 1))
elif tool == "ps":
    assert args in (["-p", "9876", "-o", "pid="], ["-ww", "-p", "9876", "-o", "args="])
    if state.get("pid_live"):
        print("9876" if "pid=" in args else "unrelated-process")
    else:
        sys.exit(1)
elif tool == "qemu-system-aarch64":
    assert "-display" in args and args[args.index("-display") + 1] == "none"
    assert args[args.index("-netdev") + 1] == "user,id=builder,hostfwd=tcp:127.0.0.1:22022-:22"
    Path(args[args.index("-pidfile") + 1]).write_text("9876\n")
    console = args[args.index("-serial") + 1]
    assert console.startswith("file:")
    Path(console[5:]).write_text("fixture console\n")
elif tool == "ssh":
    pass
else:
    raise AssertionError("unexpected tool: " + tool)
'''


class BuilderSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.builder = self.root / "builder"
        self.builder.mkdir(mode=0o700)
        keys = self.builder / "keys"
        keys.mkdir(mode=0o700)
        for name in ("id_ed25519", "known_hosts"):
            (keys / name).write_text("fixture metadata; not a real key\n")
        self.data = self.builder / "Whonix ARM64 Builder.utm/Data"
        self.data.mkdir(parents=True)
        self.disk = self.data / "builder.qcow2"
        self.seed = self.data / "seed.iso"
        for path in (self.disk, self.seed):
            path.write_text("fixture bytes\n")
        self.pid = self.builder / "qemu.pid"
        self.trace = self.root / "trace.jsonl"
        self.state_file = self.root / "state.json"
        self.state = {}
        self.firmware = self.root / "firmware.fd"
        self.firmware.write_text("fixture firmware\n")
        for name in ("uname", "stat", "lsof", "ps", "qemu-system-aarch64", "ssh"):
            path = self.tools / name
            path.write_text("#!" + sys.executable + "\n" + STUB)
            path.chmod(0o755)
        source = (SCRIPTS / "start-builder.sh").read_text()
        old = "firmware=/opt/homebrew/share/qemu/edk2-aarch64-code.fd"
        self.assertEqual(source.count(old), 1)
        source = source.replace(old, "firmware=" + shlex.quote(str(self.firmware)))
        (self.scripts / "start-builder.sh").write_text(source)
        (self.scripts / "builder-ssh").write_text((SCRIPTS / "builder-ssh").read_text())

    def run_script(self, name="start-builder.sh", *args):
        self.state_file.write_text(json.dumps(self.state))
        self.trace.write_text("")
        result = subprocess.run(
            ["/bin/bash", str(self.scripts / name), *args],
            env=dict(os.environ, PATH=str(self.tools) + ":/usr/bin:/bin:/usr/sbin:/sbin",
                     FIXTURE_STATE=str(self.state_file), FIXTURE_TRACE=str(self.trace)),
            text=True, capture_output=True, timeout=8,
        )
        events = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return result, events

    def assert_no_launch(self, result, events):
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(e["tool"] == "qemu-system-aarch64" for e in events))
        self.assertFalse((self.builder / "start.lock").exists())

    def test_private_storage_starts_headlessly_and_creates_private_outputs(self):
        result, events = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(e["tool"] == "qemu-system-aarch64" for e in events), 1)
        for path in (self.pid, self.root / "logs/builder-console.log"):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "logs").stat().st_mode), 0o700)
        self.assertFalse((self.builder / "start.lock").exists())

    def test_shared_builder_boundary_is_rejected_before_reading_vm_inputs(self):
        self.builder.chmod(0o750)
        result, events = self.run_script()
        self.assert_no_launch(result, events)
        self.assertIn("must be private", result.stderr)
        self.assertFalse(any(e["tool"] in ("lsof", "ps") for e in events))
        self.assertFalse(any(str(self.disk) in e["args"] for e in events))

    def test_foreign_owner_at_storage_boundary_is_rejected(self):
        self.state["foreign_owner"] = "/builder"
        result, events = self.run_script()
        self.assert_no_launch(result, events)
        self.assertIn("belong to the current user", result.stderr)

    def test_symlinked_builder_boundary_is_rejected(self):
        real = self.root / "real-builder"
        self.builder.rename(real)
        self.builder.symlink_to(real, target_is_directory=True)
        result, events = self.run_script()
        self.assert_no_launch(result, events)
        self.assertIn("not a real directory", result.stderr)

    def test_symlinked_nested_directory_is_rejected(self):
        real = self.builder / "real-data"
        self.data.rename(real)
        self.data.symlink_to(real, target_is_directory=True)
        result, events = self.run_script()
        self.assert_no_launch(result, events)
        self.assertIn("not a real directory", result.stderr)

    def test_disk_and_seed_links_or_special_files_are_rejected(self):
        for target in (self.disk, self.seed):
            original = target.read_bytes()
            for kind in ("symlink", "hardlink", "fifo"):
                with self.subTest(target=target.name, kind=kind):
                    target.unlink()
                    other = self.builder / "other-input"
                    other.write_bytes(original)
                    if kind == "symlink":
                        target.symlink_to(other)
                    elif kind == "hardlink":
                        os.link(other, target)
                    else:
                        os.mkfifo(target)
                    result, events = self.run_script()
                    self.assert_no_launch(result, events)
                    target.unlink()
                    other.unlink()
                    target.write_bytes(original)

    def test_untrusted_pid_file_is_not_read_or_removed(self):
        other = self.builder / "not-a-pid"
        other.write_text("9876\n")
        self.pid.symlink_to(other)
        result, events = self.run_script()
        self.assert_no_launch(result, events)
        self.assertTrue(self.pid.is_symlink())
        self.assertFalse(any(e["tool"] == "ps" for e in events))

    def test_lsof_errors_and_occupied_resources_prevent_start(self):
        for kind in ("disk", "port"):
            for response in ({"status": 2}, {"status": 1, "stderr": "permission denied"},
                             {"status": 0}, {"status": 0, "stdout": "1234"},
                             {"status": 1, "stdout": "1234"}):
                with self.subTest(kind=kind, response=response):
                    self.state = {"lsof_" + kind: response}
                    result, events = self.run_script()
                    self.assert_no_launch(result, events)

    def test_live_foreign_pid_is_preserved(self):
        self.pid.write_text("9876\n")
        self.state["pid_live"] = True
        result, events = self.run_script()
        self.assert_no_launch(result, events)
        self.assertIn("occupied by another process", result.stderr)
        self.assertEqual(self.pid.read_text(), "9876\n")

    def test_builder_ssh_uses_only_explicit_identity_and_no_forwarding(self):
        command = ["sh", "-s", "--", "argument with spaces"]
        result, events = self.run_script("builder-ssh", *command)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([e["tool"] for e in events], ["ssh"])
        args = events[0]["args"]
        self.assertEqual(args[:2], ["-F", "/dev/null"])
        options = [args[i + 1] for i, value in enumerate(args) if value == "-o"]
        for option in ("IdentityAgent=none", "IdentitiesOnly=yes", "ForwardAgent=no",
                       "ForwardX11=no", "ClearAllForwardings=yes", "ControlMaster=no",
                       "ControlPath=none", "StrictHostKeyChecking=yes", "BatchMode=yes",
                       "GlobalKnownHostsFile=/dev/null",
                       "UserKnownHostsFile=" + str(self.builder / "keys/known_hosts")):
            self.assertIn(option, options)
        self.assertEqual(args[args.index("-i") + 1], str(self.builder / "keys/id_ed25519"))
        self.assertEqual(args[args.index("-p") + 1], "22022")
        endpoint = args.index("user@127.0.0.1")
        self.assertEqual(args[endpoint + 1:], command)

    def test_missing_or_linked_ssh_identity_never_falls_back_to_personal_keys(self):
        key = self.builder / "keys/id_ed25519"
        key.unlink()
        result, events = self.run_script("builder-ssh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])
        key.symlink_to(self.builder / "keys/known_hosts")
        result, events = self.run_script("builder-ssh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
