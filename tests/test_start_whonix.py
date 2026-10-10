"""Exercise the launch CLI against private stubs; never contact UTM or a VM."""

import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/start-whonix.sh"
GATEWAY = "11111111-1111-4111-8111-111111111111"
WORKSTATION = "22222222-2222-4222-8222-222222222222"

STUB = r'''
import json
import os
from pathlib import Path
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]
state_path = Path(os.environ["FIXTURE_STATE"])
state = json.loads(state_path.read_text())
with open(os.environ["FIXTURE_TRACE"], "a") as stream:
    stream.write(json.dumps({"tool": tool, "args": args}) + "\n")

if tool == "prepare_utm.py":
    assert args[0] == "--check" and len(args) == 2
    sys.exit(1 if state.get("validation_failure") in Path(args[1]).name else 0)
if tool == "utmctl":
    command, identifier = args[0], args[-1]
    if command == "status":
        print(state["guests"][identifier])
    elif command == "start":
        assert args == ["start", identifier] or args == ["start", "--hide", identifier]
        if state.get("start_failure") == identifier:
            sys.exit(1)
        state["guests"][identifier] = "started"
        state_path.write_text(json.dumps(state))
    else:
        raise AssertionError("unexpected UTM mutation: " + repr(args))
elif tool == "lsof":
    assert args == ["-nP", "-iTCP:8010", "-sTCP:LISTEN", "-Fpn"]
    if state["guests"][state["gateway_id"]] != "started" and not state.get("occupied"):
        sys.exit(1)
    endpoint = "*:8010" if state.get("wildcard") else "127.0.0.1:8010"
    print("p9876\nn" + endpoint)
elif tool == "ps":
    assert args == ["-ww", "-p", "9876", "-o", "args="]
    identifier = "unrelated-process" if state.get("foreign_listener") else state["gateway_id"]
    print("qemu-system-aarch64 -uuid " + identifier + " -accel hvf")
else:
    raise AssertionError("unexpected stub: " + tool)
'''


class StartWhonixTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.scripts = self.root / "scripts"
        self.scripts.mkdir()
        self.tools = self.root / "stub-tools"
        self.tools.mkdir()
        self.trace = self.root / "trace.jsonl"
        self.state_path = self.root / "state.json"
        self.state = {
            "gateway_id": GATEWAY,
            "guests": {GATEWAY: "stopped", WORKSTATION: "stopped"},
            "validation_failure": "no-match",
        }
        for name in ("utmctl", "lsof", "ps"):
            target = self.tools / name
            target.write_text("#!" + sys.executable + "\n" + STUB)
            target.chmod(0o755)
        (self.scripts / "prepare_utm.py").write_text(STUB)
        for role, identifier in (("Gateway", GATEWAY), ("Workstation", WORKSTATION)):
            bundle = self.root / "artifacts/utm" / ("Whonix-" + role + "-LXQt.utm")
            bundle.mkdir(parents=True)
            (bundle / "config.plist").write_bytes(plistlib.dumps({
                "Information": {"Name": "Whonix-" + role + "-LXQt", "UUID": identifier},
            }))

        # Keep production's fixed tool paths. Only this disposable copy uses
        # stubs, including ps, so tests cannot start or inspect real VMs.
        source = SOURCE.read_text()
        replacements = {
            "utmctl=/Applications/UTM.app/Contents/MacOS/utmctl": "utmctl=" + shlex.quote(str(self.tools / "utmctl")),
            "python=/usr/bin/python3": "python=" + shlex.quote(sys.executable),
            "lsof=/usr/sbin/lsof": "lsof=" + shlex.quote(str(self.tools / "lsof")),
            "/bin/ps -ww": shlex.quote(str(self.tools / "ps")) + " -ww",
        }
        for old, new in replacements.items():
            self.assertEqual(source.count(old), 1, old)
            source = source.replace(old, new)
        self.script = self.scripts / "start-whonix.sh"
        self.script.write_text(source)

    def launch(self, *arguments):
        self.state_path.write_text(json.dumps(self.state))
        self.trace.write_text("")
        result = subprocess.run(
            ["/bin/bash", str(self.script), *arguments],
            env=dict(os.environ, FIXTURE_STATE=str(self.state_path), FIXTURE_TRACE=str(self.trace)),
            text=True, capture_output=True, timeout=5,
        )
        events = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return result, events

    @staticmethod
    def starts(events):
        return [event["args"] for event in events if event["tool"] == "utmctl" and event["args"][0] == "start"]

    def test_default_and_hide_validate_both_guests_then_start_in_order(self):
        for options in ((), ("--hide",)):
            with self.subTest(options=options):
                result, events = self.launch(*options)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([event["tool"] for event in events[:2]], ["prepare_utm.py", "prepare_utm.py"])
                self.assertIn("Gateway", events[0]["args"][1])
                self.assertIn("Workstation", events[1]["args"][1])
                self.assertEqual(self.starts(events), [["start", *options, GATEWAY], ["start", *options, WORKSTATION]])
                gateway_start = next(i for i, event in enumerate(events) if event["args"] == ["start", *options, GATEWAY])
                workstation_start = next(i for i, event in enumerate(events) if event["args"] == ["start", *options, WORKSTATION])
                owner_check = next(i for i, event in enumerate(events) if event["tool"] == "ps")
                self.assertLess(gateway_start, owner_check)
                self.assertLess(owner_check, workstation_start)

    def test_validation_failures_prevent_every_utm_call(self):
        for role in ("Gateway", "Workstation"):
            for options in ((), ("--hide",)):
                with self.subTest(role=role, options=options):
                    self.state["validation_failure"] = role
                    result, events = self.launch(*options)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(role + " validation failed", result.stderr)
                    self.assertFalse(any(event["tool"] == "utmctl" for event in events))

    def test_listener_guards_prevent_workstation_start(self):
        for failure in ("occupied", "wildcard", "foreign_listener"):
            with self.subTest(failure=failure):
                self.state[failure] = True
                result, events = self.launch("--hide")
                del self.state[failure]
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(["start", "--hide", WORKSTATION], self.starts(events))
                if failure == "occupied":
                    self.assertEqual(self.starts(events), [])

    def test_running_workstation_without_gateway_is_not_restarted(self):
        self.state["guests"][WORKSTATION] = "started"
        result, events = self.launch("--hide")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Workstation is already running", result.stderr)
        self.assertEqual(self.starts(events), [])

    def test_running_pair_is_not_restarted_or_hidden(self):
        self.state["guests"] = {GATEWAY: "started", WORKSTATION: "started"}
        result, events = self.launch("--hide")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.starts(events), [])

    def test_gateway_start_failure_prevents_workstation_start(self):
        self.state["start_failure"] = GATEWAY
        result, events = self.launch("--hide")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not start Gateway", result.stderr)
        self.assertEqual(self.starts(events), [["start", "--hide", GATEWAY]])

    def test_duplicate_ids_are_rejected_before_starting(self):
        config = self.root / "artifacts/utm/Whonix-Workstation-LXQt.utm/config.plist"
        value = plistlib.loads(config.read_bytes())
        value["Information"]["UUID"] = GATEWAY
        config.write_bytes(plistlib.dumps(value))
        result, events = self.launch("--hide")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("different UUIDs", result.stderr)
        self.assertFalse(any(event["tool"] == "utmctl" for event in events))

    def test_help_and_invalid_arguments_have_no_vm_side_effects(self):
        for option in ("-h", "--help"):
            result, events = self.launch(option)
            self.assertEqual(result.returncode, 0)
            self.assertIn("hide the main UTM window", result.stdout)
            self.assertIn("Guest console windows still open", result.stdout)
            self.assertEqual(events, [])
        for options in (("--background",), ("--hide", "--hide"), ("--unknown",)):
            with self.subTest(options=options):
                result, events = self.launch(*options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("usage:", result.stderr)
                self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
