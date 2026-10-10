"""Regression checks for UTM storage, socket isolation and config preparation."""

import importlib.util
from contextlib import redirect_stderr
import io
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock
import uuid


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_utm.py"
SPEC = importlib.util.spec_from_file_location("prepare_utm", SCRIPT)
prepare_utm = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare_utm)

DISK_OVERRIDES = (
    "-set drive.drive0.format=raw "
    "-set drive.drive0.discard=ignore "
    "-set drive.drive0.detect-zeroes=off"
)


def upstream_config(role):
    arguments = (
        "-device virtio-net-pci,netdev=external -device virtio-net-pci,netdev=internal "
        "-netdev user,id=external,ipv6=off,net=10.0.2.0/24 "
        "-netdev socket,id=internal,listen=:8010"
        if role == "Gateway" else
        "-device virtio-net-pci,netdev=internal "
        "-netdev socket,id=internal,connect=127.0.0.1:8010"
    )
    return {
        "Backend": "QEMU", "ConfigurationVersion": 4,
        "System": {"Architecture": "aarch64", "Target": "virt", "CPU": "default", "CPUCount": 3,
                   "MemorySize": 4096, "CPUFlagsAdd": [], "CPUFlagsRemove": []},
        "Information": {"Name": "Whonix-" + role + "-LXQt", "UUID": "2C279BF0-E968-4EF1-B93E-61BABD03A7B7"},
        "Network": [], "Serial": [], "Input": {"UsbSharing": False},
        "Sharing": {"ClipboardSharing": True, "DirectoryShareMode": "None"},
        "Display": [{"Hardware": "virtio-gpu-gl-pci", "DynamicResolution": False, "NativeResolution": True}],
        "QEMU": {"AdditionalArguments": [arguments], "Hypervisor": True, "UEFIBoot": True, "DebugLog": True, "TPMDevice": False},
        "Drive": [{"Identifier": "0", "ImageName": "Whonix-" + role + "-LXQt.raw", "ImageType": "Disk", "Interface": "VirtIO", "ReadOnly": False}],
    }


class PrepareUTMTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def bundle(self, role="Workstation", mutate=None):
        config = upstream_config(role)
        if mutate:
            mutate(config)
        bundle = self.root / (role + ".utm")
        bundle.mkdir()
        (bundle / "Data").mkdir()
        (bundle / "Data" / ("Whonix-" + role + "-LXQt.raw")).write_bytes(b"disk contents".ljust(512, b"\0"))
        (bundle / "config.plist").write_bytes(plistlib.dumps(config))
        return bundle

    def test_prepares_both_roles_and_check_does_not_write(self):
        for role in ("Gateway", "Workstation"):
            with self.subTest(role=role):
                bundle = self.bundle(role)
                disk = next((bundle / "Data").iterdir())
                disk_before = disk.read_bytes()
                before = plistlib.loads((bundle / "config.plist").read_bytes())
                prepare_utm.process_bundle(bundle)
                config_path = bundle / "config.plist"
                content = config_path.read_bytes()
                after = plistlib.loads(content)
                expected_system = dict(before["System"])
                expected_system.update(CPU="default", CPUCount=2 if role == "Gateway" else 4,
                                       MemorySize=2048 if role == "Gateway" else 4096)
                self.assertEqual(after["System"], expected_system)
                self.assertEqual(after["Drive"], before["Drive"])
                self.assertFalse(after["Sharing"]["ClipboardSharing"])
                self.assertEqual(after["Display"][0]["Hardware"], "virtio-ramfb")
                self.assertIs(after["Display"][0]["NativeResolution"], False)
                self.assertIs(after["Display"][0]["DynamicResolution"], True)
                self.assertIs(after["QEMU"]["DebugLog"], False)
                self.assertIs(after["QEMU"]["Hypervisor"], True)
                self.assertIs(after["QEMU"]["UEFIBoot"], True)
                self.assertEqual(uuid.UUID(after["Information"]["UUID"]).version, 4)
                self.assertNotEqual(after["Information"]["UUID"], before["Information"]["UUID"])
                self.assertNotIn("listen=:8010", after["QEMU"]["AdditionalArguments"][0])
                expected_network = before["QEMU"]["AdditionalArguments"][0].replace("listen=:8010", "listen=127.0.0.1:8010")
                self.assertEqual(after["QEMU"]["AdditionalArguments"], [expected_network + " " + DISK_OVERRIDES])
                self.assertEqual(disk.read_bytes(), disk_before)
                self.assertEqual(bundle.stat().st_mode & 0o777, 0o700)
                mtime = config_path.stat().st_mtime_ns
                self.assertEqual(prepare_utm.process_bundle(bundle, check=True), role)
                self.assertEqual(config_path.read_bytes(), content)
                self.assertEqual(config_path.stat().st_mtime_ns, mtime)

    def test_accepts_prepared_input_without_duplicating_disk_overrides(self):
        for role in ("Gateway", "Workstation"):
            with self.subTest(role=role):
                bundle = self.bundle(role)
                prepare_utm.process_bundle(bundle)
                before = plistlib.loads((bundle / "config.plist").read_bytes())
                prepare_utm.process_bundle(bundle)
                after = plistlib.loads((bundle / "config.plist").read_bytes())
                self.assertEqual(after["QEMU"]["AdditionalArguments"], before["QEMU"]["AdditionalArguments"])
                self.assertEqual(prepare_utm.process_bundle(bundle, check=True), role)

    def test_prepares_legacy_input_without_optional_profile_fields(self):
        def remove_profile(config):
            for field in ("CPU", "CPUCount", "MemorySize", "CPUFlagsAdd", "CPUFlagsRemove"):
                del config["System"][field]
            for field in ("NativeResolution", "DynamicResolution"):
                del config["Display"][0][field]
            del config["QEMU"]["DebugLog"]

        bundle = self.bundle(mutate=remove_profile)
        prepare_utm.process_bundle(bundle)
        self.assertEqual(prepare_utm.process_bundle(bundle, check=True), "Workstation")

    def test_check_requires_exact_role_profile(self):
        for role in ("Gateway", "Workstation"):
            bundle = self.bundle(role)
            prepare_utm.process_bundle(bundle)
            config_path = bundle / "config.plist"
            prepared_config = plistlib.loads(config_path.read_bytes())
            fields = (
                ("System", "CPU", ("host", "cortex-a72", "")),
                ("System", "CPUCount", (3, "2", 2.0 if role == "Gateway" else 4.0)),
                ("System", "MemorySize", (1024, "4096", 2048.0 if role == "Gateway" else 4096.0)),
                ("QEMU", "DebugLog", (True, 0)),
                ("Display", "NativeResolution", (True, 0)),
                ("Display", "DynamicResolution", (False, 1)),
            )
            for section, field, values in fields:
                for value in (*values, None):
                    with self.subTest(role=role, section=section, field=field, value=value):
                        config = plistlib.loads(plistlib.dumps(prepared_config))
                        target = config[section][0] if section == "Display" else config[section]
                        if value is None:
                            del target[field]
                        else:
                            target[field] = value
                        content = plistlib.dumps(config)
                        config_path.write_bytes(content)
                        with self.assertRaises(prepare_utm.ValidationError):
                            prepare_utm.process_bundle(bundle, check=True)
                        self.assertEqual(config_path.read_bytes(), content)

    def test_rejects_cpu_feature_overrides(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        config_path = bundle / "config.plist"
        prepared_config = plistlib.loads(config_path.read_bytes())
        for field in ("CPUFlagsAdd", "CPUFlagsRemove"):
            for value in (["sve"], ["pauth"], "", {}):
                with self.subTest(field=field, value=value):
                    config = plistlib.loads(plistlib.dumps(prepared_config))
                    config["System"][field] = value
                    content = plistlib.dumps(config)
                    config_path.write_bytes(content)
                    for check in (False, True):
                        with self.assertRaisesRegex(prepare_utm.ValidationError, field):
                            prepare_utm.process_bundle(bundle, check=check)
                        self.assertEqual(config_path.read_bytes(), content)

    def test_rejects_unapproved_sound_devices_and_tpm_helper(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        config_path = bundle / "config.plist"
        baseline = plistlib.loads(config_path.read_bytes())
        mutations = (
            ("Sound", [{"Hardware": "loader,file=/etc/passwd,addr=0x40000000,force-raw=on,id=hda_loader"}]),
            ("Sound", [{"Hardware": "usb-host,hostbus=1,hostaddr=2,id=hda_usb"}]),
            ("Sound", [{"Hardware": "intel-hda"}, {"Hardware": "intel-hda"}]),
            ("Sound", "intel-hda"),
            ("Sound", ["intel-hda"]),
            ("Sound", [{}]),
            ("TPMDevice", True),
            ("TPMDevice", 0),
            ("TPMDevice", "false"),
            ("TPMDevice", None),
        )
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                config = plistlib.loads(plistlib.dumps(baseline))
                target = config["QEMU"] if field == "TPMDevice" else config
                if value is None:
                    target.pop(field)
                else:
                    target[field] = value
                content = plistlib.dumps(config)
                config_path.write_bytes(content)
                for check in (False, True):
                    with self.assertRaisesRegex(prepare_utm.ValidationError, field):
                        prepare_utm.process_bundle(bundle, check=check)
                    self.assertEqual(config_path.read_bytes(), content)

    def test_accepts_only_approved_or_absent_sound_without_enabling_tpm(self):
        bundle = self.bundle()
        config_path = bundle / "config.plist"
        for sounds in (None, [], [{"Hardware": "intel-hda"}]):
            with self.subTest(sounds=sounds):
                config = upstream_config("Workstation")
                if sounds is not None:
                    config["Sound"] = sounds
                config["QEMU"]["TPMDevice"] = False
                config_path.write_bytes(plistlib.dumps(config))
                prepare_utm.process_bundle(bundle)
                self.assertEqual(prepare_utm.process_bundle(bundle, check=True), "Workstation")

    def test_requires_exact_hypervisor_and_uefi_booleans(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        config_path = bundle / "config.plist"
        prepared_config = plistlib.loads(config_path.read_bytes())
        for field in ("Hypervisor", "UEFIBoot"):
            for value in (False, 1, None):
                with self.subTest(field=field, value=value):
                    config = plistlib.loads(plistlib.dumps(prepared_config))
                    if value is None:
                        del config["QEMU"][field]
                    else:
                        config["QEMU"][field] = value
                    content = plistlib.dumps(config)
                    config_path.write_bytes(content)
                    for check in (False, True):
                        with self.assertRaisesRegex(prepare_utm.ValidationError, field):
                            prepare_utm.process_bundle(bundle, check=check)
                        self.assertEqual(config_path.read_bytes(), content)

    def test_check_requires_every_disk_override(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        config_path = bundle / "config.plist"
        prepared_config = plistlib.loads(config_path.read_bytes())
        for override in (
            DISK_OVERRIDES,
            "-set drive.drive0.format=raw",
            "-set drive.drive0.discard=ignore",
            "-set drive.drive0.detect-zeroes=off",
        ):
            with self.subTest(removed=override):
                config = plistlib.loads(plistlib.dumps(prepared_config))
                config["QEMU"]["AdditionalArguments"][0] = config["QEMU"]["AdditionalArguments"][0].replace(override, "")
                content = plistlib.dumps(config)
                config_path.write_bytes(content)
                with self.assertRaises(prepare_utm.ValidationError):
                    prepare_utm.process_bundle(bundle, check=True)
                self.assertEqual(config_path.read_bytes(), content)

    def test_rejects_disk_override_changes_and_network_bypass(self):
        mutations = {
            "wrong_format": lambda s: s.replace("format=raw", "format=qcow2"),
            "other_drive": lambda s: s.replace("drive.drive0.", "drive.drive1."),
            "discard_enabled": lambda s: s.replace("discard=ignore", "discard=unmap"),
            "zero_detection_enabled": lambda s: s.replace("detect-zeroes=off", "detect-zeroes=unmap"),
            "duplicate_override": lambda s: s + " -set drive.drive0.format=raw",
            "network_bypass": lambda s: s + " -netdev user,id=bypass",
        }
        for role in ("Gateway", "Workstation"):
            bundle = self.bundle(role)
            prepare_utm.process_bundle(bundle)
            config_path = bundle / "config.plist"
            prepared_config = plistlib.loads(config_path.read_bytes())
            for label, mutation in mutations.items():
                with self.subTest(role=role, label=label):
                    config = plistlib.loads(plistlib.dumps(prepared_config))
                    config["QEMU"]["AdditionalArguments"][0] = mutation(config["QEMU"]["AdditionalArguments"][0])
                    content = plistlib.dumps(config)
                    config_path.write_bytes(content)
                    for check in (False, True):
                        with self.assertRaises(prepare_utm.ValidationError):
                            prepare_utm.process_bundle(bundle, check=check)
                        self.assertEqual(config_path.read_bytes(), content)

    def test_requires_exact_disk_identifier(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        config_path = bundle / "config.plist"
        config = plistlib.loads(config_path.read_bytes())
        for identifier in ("1", 0, ""):
            with self.subTest(identifier=identifier):
                config["Drive"][0]["Identifier"] = identifier
                content = plistlib.dumps(config)
                config_path.write_bytes(content)
                for check in (False, True):
                    with self.assertRaisesRegex(prepare_utm.ValidationError, "Identifier"):
                        prepare_utm.process_bundle(bundle, check=check)
                    self.assertEqual(config_path.read_bytes(), content)
        del config["Drive"][0]["Identifier"]
        with self.assertRaisesRegex(prepare_utm.ValidationError, "Identifier"):
            prepare_utm.validate(bundle, config, prepared=True)

    def test_rejects_workstation_bypass_paths_without_modifying_config(self):
        mutations = {
            "utm_adapter": lambda c: c["Network"].append({"Mode": "Shared"}),
            "extra_user_backend": lambda c: c["QEMU"]["AdditionalArguments"].append("-netdev user,id=bypass"),
            "backend_substitution": lambda c: c["QEMU"].update(AdditionalArguments=["-device virtio-net-pci,netdev=internal -netdev user,id=internal"]),
            "extra_socket_option": lambda c: c["QEMU"]["AdditionalArguments"].append(",localaddr=0.0.0.0"),
            "directory_sharing": lambda c: c["Sharing"].update(DirectoryShareMode="VirtFS"),
            "usb_sharing": lambda c: c["Input"].update(UsbSharing=True),
            "serial": lambda c: c["Serial"].append({"Mode": "TcpClient"}),
            "quoted_arguments": lambda c: c["QEMU"].update(AdditionalArguments=['"-device virtio-net-pci,netdev=internal"']),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                case_dir = self.root / label
                case_dir.mkdir()
                original_root, self.root = self.root, case_dir
                bundle = self.bundle(mutate=mutation)
                self.root = original_root
                before = (bundle / "config.plist").read_bytes()
                with self.assertRaises(prepare_utm.ValidationError):
                    prepare_utm.process_bundle(bundle)
                self.assertEqual((bundle / "config.plist").read_bytes(), before)

    def test_check_rejects_unprepared_gateway(self):
        bundle = self.bundle("Gateway")
        before = (bundle / "config.plist").read_bytes()
        with self.assertRaises(prepare_utm.ValidationError):
            prepare_utm.process_bundle(bundle, check=True)
        self.assertEqual((bundle / "config.plist").read_bytes(), before)

    def test_rejects_disk_path_escape(self):
        bundle = self.bundle(mutate=lambda c: c["Drive"][0].update(ImageName="../../outside.raw"))
        with self.assertRaisesRegex(prepare_utm.ValidationError, "directly inside Data"):
            prepare_utm.process_bundle(bundle)

    def test_rejects_symlink_disk(self):
        bundle = self.bundle()
        disk = next((bundle / "Data").iterdir())
        disk.unlink()
        outside = self.root / "outside.raw"
        outside.write_bytes(b"x" * 512)
        disk.symlink_to(outside)
        with self.assertRaisesRegex(prepare_utm.ValidationError, "symlink"):
            prepare_utm.process_bundle(bundle)

    def test_accepts_missing_efi_vars_in_fresh_bundle(self):
        for role in ("Gateway", "Workstation"):
            with self.subTest(role=role):
                bundle = self.bundle(role)
                firmware = bundle / "Data" / "efi_vars.fd"
                self.assertFalse(firmware.exists())
                prepare_utm.process_bundle(bundle)
                self.assertEqual(prepare_utm.process_bundle(bundle, check=True), role)
                self.assertFalse(firmware.exists())

    def test_accepts_regular_efi_vars_without_changing_it(self):
        bundle = self.bundle()
        firmware = bundle / "Data" / "efi_vars.fd"
        content = b"firmware variable contents"
        firmware.write_bytes(content)
        for mode in (0o600, 0o644):
            with self.subTest(mode=oct(mode)):
                firmware.chmod(mode)
                before = firmware.stat()
                prepare_utm.process_bundle(bundle)
                self.assertEqual(prepare_utm.process_bundle(bundle, check=True), "Workstation")
                self.assertEqual(firmware.read_bytes(), content)
                after = firmware.stat()
                self.assertEqual(after.st_mode, before.st_mode)
                self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)

    def test_rejects_linked_efi_vars_without_changing_files(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        config_path = bundle / "config.plist"
        config_before = config_path.read_bytes()
        firmware = bundle / "Data" / "efi_vars.fd"
        outside = self.root / "outside.fd"
        outside_content = b"unrelated file"
        outside.write_bytes(outside_content)
        for kind in ("symlink", "dangling_symlink", "hardlink"):
            with self.subTest(kind=kind):
                if kind == "hardlink":
                    os.link(outside, firmware)
                else:
                    firmware.symlink_to(outside if kind == "symlink" else self.root / "missing.fd")
                for check in (False, True):
                    with self.assertRaisesRegex(prepare_utm.ValidationError, "efi_vars.fd"):
                        prepare_utm.process_bundle(bundle, check=check)
                    self.assertEqual(config_path.read_bytes(), config_before)
                    self.assertEqual(outside.read_bytes(), outside_content)
                firmware.unlink()

    def test_rejects_nonregular_efi_vars(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        (bundle / "Data" / "efi_vars.fd").mkdir()
        for check in (False, True):
            with self.assertRaisesRegex(prepare_utm.ValidationError, "efi_vars.fd"):
                prepare_utm.process_bundle(bundle, check=check)

    def test_rejects_group_or_other_writable_efi_vars(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        config_path = bundle / "config.plist"
        config_before = config_path.read_bytes()
        firmware = bundle / "Data" / "efi_vars.fd"
        firmware.write_bytes(b"firmware variable contents")
        for mode in (0o620, 0o602, 0o666):
            with self.subTest(mode=oct(mode)):
                firmware.chmod(mode)
                for check in (False, True):
                    with self.assertRaisesRegex(prepare_utm.ValidationError, "efi_vars.fd"):
                        prepare_utm.process_bundle(bundle, check=check)
                    self.assertEqual(config_path.read_bytes(), config_before)
                    self.assertEqual(firmware.stat().st_mode & 0o777, mode)

    def test_rejects_wrong_platform(self):
        bundle = self.bundle(mutate=lambda c: c["System"].update(Architecture="x86_64"))
        with self.assertRaisesRegex(prepare_utm.ValidationError, "aarch64"):
            prepare_utm.process_bundle(bundle)

    def test_rejects_qcow_disguised_as_raw(self):
        bundle = self.bundle()
        next((bundle / "Data").iterdir()).write_bytes(b"QFI\xfb" + b"\0" * 508)
        with self.assertRaisesRegex(prepare_utm.ValidationError, "QCOW"):
            prepare_utm.process_bundle(bundle)

    def test_malformed_plist_cli_fails_without_replacing_it(self):
        bundle = self.bundle()
        config_path = bundle / "config.plist"
        malformed = b'<?xml version="1.0"?><plist><dict>'
        config_path.write_bytes(malformed)
        errors = io.StringIO()
        with redirect_stderr(errors):
            self.assertEqual(prepare_utm.main([str(bundle)]), 1)
        self.assertIn("ERROR:", errors.getvalue())
        self.assertEqual(config_path.read_bytes(), malformed)

    def test_rejects_noncanonical_uuid(self):
        bundle = self.bundle(mutate=lambda c: c["Information"].update(UUID="2c279bf0e9684ef1b93e61babd03a7b7"))
        with self.assertRaisesRegex(prepare_utm.ValidationError, "canonical"):
            prepare_utm.process_bundle(bundle)

    def test_rejects_writable_config_before_preparing_or_checking(self):
        bundle = self.bundle()
        config_path = bundle / "config.plist"
        content = config_path.read_bytes()
        config_path.chmod(0o666)
        bundle.chmod(0o700)
        for check in (False, True):
            with self.assertRaisesRegex(prepare_utm.ValidationError, "config.plist.*writable"):
                prepare_utm.process_bundle(bundle, check=check)
            self.assertEqual(config_path.read_bytes(), content)

    def test_rejects_writable_bundle_directory(self):
        bundle = self.bundle()
        bundle.chmod(0o777)
        with self.assertRaisesRegex(prepare_utm.ValidationError, "writable"):
            prepare_utm.process_bundle(bundle)

    def test_check_requires_private_bundle_without_changing_permissions(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        for mode in (0o750, 0o705, 0o755):
            with self.subTest(mode=oct(mode)):
                bundle.chmod(mode)
                with self.assertRaisesRegex(prepare_utm.ValidationError, "group/other access"):
                    prepare_utm.process_bundle(bundle, check=True)
                self.assertEqual(bundle.stat().st_mode & 0o777, mode)

    def test_rejects_writable_data_or_disk_without_modifying_config(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        config_path = bundle / "config.plist"
        content = config_path.read_bytes()
        disk = next((bundle / "Data").iterdir())
        for target in (bundle / "Data", disk):
            original_mode = target.stat().st_mode & 0o777
            for mode in (0o777, 0o720, 0o702):
                with self.subTest(target=target.name, mode=oct(mode)):
                    target.chmod(mode)
                    for check in (False, True):
                        with self.assertRaisesRegex(prepare_utm.ValidationError, "writable"):
                            prepare_utm.process_bundle(bundle, check=check)
                        self.assertEqual(config_path.read_bytes(), content)
            target.chmod(original_mode)

    def test_rejects_config_and_disk_hardlinks_without_changing_outside_file(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        disk = next((bundle / "Data").iterdir())
        for target in (bundle / "config.plist", disk):
            with self.subTest(target=target.name):
                outside = self.root / "outside"
                os.link(target, outside)
                content = outside.read_bytes()
                for check in (False, True):
                    with self.assertRaisesRegex(prepare_utm.ValidationError, "hard links"):
                        prepare_utm.process_bundle(bundle, check=check)
                    self.assertEqual(outside.read_bytes(), content)
                outside.unlink()

    def test_rejects_foreign_owners_on_bundle_objects(self):
        bundle = self.bundle()
        prepare_utm.process_bundle(bundle)
        firmware = bundle / "Data" / "efi_vars.fd"
        firmware.write_bytes(b"firmware")
        disk = bundle / "Data" / "Whonix-Workstation-LXQt.raw"
        original_lstat = Path.lstat
        for target in (bundle, bundle / "Data", bundle / "config.plist", disk, firmware):
            with self.subTest(target=target.name):
                def foreign_owner(path):
                    result = original_lstat(path)
                    if path == target or path == target.resolve():
                        values = list(result)
                        values[4] = os.geteuid() + 1
                        return os.stat_result(values)
                    return result
                with mock.patch.object(Path, "lstat", foreign_owner):
                    for check in (False, True):
                        with self.assertRaisesRegex(prepare_utm.ValidationError, "owned by the current user"):
                            prepare_utm.process_bundle(bundle, check=check)

    def test_rejects_writable_ancestor_but_accepts_system_temp_alias(self):
        bundle = self.bundle()
        self.root.chmod(0o777)
        try:
            with self.assertRaisesRegex(prepare_utm.ValidationError, "ancestor.*writable"):
                prepare_utm.process_bundle(bundle)
        finally:
            self.root.chmod(0o700)
        # macOS temporary-directory paths commonly contain /var, which is a
        # root-owned symlink to /private/var. Resolving that path is supported.
        prepare_utm.process_bundle(bundle)
        self.assertEqual(prepare_utm.process_bundle(bundle, check=True), "Workstation")


if __name__ == "__main__":
    unittest.main()
