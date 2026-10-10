#!/usr/bin/python3
"""Prepare unpacked derivative-maker ARM64 Whonix UTM bundles before import.

Run only while the VM is stopped, with the bundle in a directory only your
account can modify. Preparation restricts bundle access to your account.
--check validates the prepared configuration without
writing. This checks the declared topology and disk path, not guest bootability,
firewall behavior, or runtime network isolation.
"""

import argparse
import copy
import os
from pathlib import Path
import plistlib
import re
import stat
import sys
import tempfile
import uuid
from xml.parsers.expat import ExpatError


class ValidationError(ValueError):
    """The bundle differs from the supported derivative-maker configuration."""


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def section(config, name, kind=dict):
    value = config.get(name)
    require(isinstance(value, kind), f"{name} must be a {kind.__name__}")
    return value


def network_tokens(role, wildcard=False):
    internal = ["-device", "virtio-net-pci,netdev=internal"]
    if role == "Workstation":
        return internal + [
            "-netdev", "socket,id=internal,connect=127.0.0.1:8010"
        ]
    listener = ":8010" if wildcard else "127.0.0.1:8010"
    return ["-device", "virtio-net-pci,netdev=external"] + internal + [
        "-netdev", "user,id=external,ipv6=off,net=10.0.2.0/24",
        "-netdev", f"socket,id=internal,listen={listener}",
    ]


def disk_tokens():
    # UTM's raw disk needs explicit format and zero/discard settings to avoid
    # early guest writes failing with EINVAL on this Apple Silicon setup.
    return [
        "-set", "drive.drive0.format=raw",
        "-set", "drive.drive0.discard=ignore",
        "-set", "drive.drive0.detect-zeroes=off",
    ]


def system_profile(role):
    return {
        "CPU": "default",
        "CPUCount": 2 if role == "Gateway" else 4,
        "MemorySize": 2048 if role == "Gateway" else 4096,
    }


def trusted_metadata(path, description, directory=False, allow_missing=False):
    """Validate existing bundle objects before opening their contents."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_missing:
            return None
        raise
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    require(expected(metadata.st_mode),
            f"{description} must be a {'directory' if directory else 'regular file'}, not a symlink")
    require(metadata.st_uid == os.geteuid(), f"{description} must be owned by the current user")
    require(metadata.st_mode & 0o022 == 0, f"{description} must not be group/other writable")
    if not directory:
        require(metadata.st_nlink == 1, f"{description} must not have hard links")
    return metadata


def trusted_bundle_path(bundle):
    require(bundle.suffix == ".utm", "Bundle must be an unpacked .utm directory")
    trusted_metadata(bundle, "Bundle directory", directory=True)
    # Use the resolved path throughout: macOS /var and /tmp are legitimate
    # aliases, but a mutable alias must not be consulted again after validation.
    bundle = bundle.resolve(strict=True)
    trusted_metadata(bundle, "Bundle directory", directory=True)
    for parent in bundle.parents:
        metadata = parent.stat()
        require(metadata.st_uid in (0, os.geteuid()),
                f"Bundle ancestor must be owned by root or the current user: {parent}")
        # Root-owned sticky directories (/private/tmp) cannot have an existing
        # caller-owned child replaced by another unprivileged account.
        sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
        require(metadata.st_mode & 0o022 == 0 or sticky_root,
                f"Bundle ancestor must not be group/other writable: {parent}")
    return bundle


def validate(bundle, config, prepared=False):
    """Reject unsupported platform, storage, sharing, and network settings."""
    require(isinstance(config, dict), "config.plist must contain a dictionary")
    require(config.get("Backend") == "QEMU", "Backend must be QEMU")
    require(config.get("ConfigurationVersion") == 4, "ConfigurationVersion must be 4")
    system = section(config, "System")
    require(system.get("Architecture") == "aarch64", "Architecture must be aarch64")
    require(system.get("Target") == "virt", "Target must be virt")
    info = section(config, "Information")
    name = info.get("Name")
    require(isinstance(name, str), "Information.Name must be a string")
    match = re.fullmatch(r"Whonix-(Gateway|Workstation)(?:-[A-Za-z0-9._]+)*", name)
    require(match is not None, "Name must identify Whonix-Gateway or Whonix-Workstation")
    role = match.group(1)
    for field in ("CPUFlagsAdd", "CPUFlagsRemove"):
        flags = system.get(field, [])
        require(isinstance(flags, list) and not flags, f"System.{field} must be empty")
    if prepared:
        for field, expected in system_profile(role).items():
            actual = system.get(field)
            require(type(actual) is type(expected) and actual == expected,
                    f"Prepared {role} System.{field} must be {expected!r}")
    identifier = info.get("UUID")
    require(isinstance(identifier, str), "Information.UUID must be a UUID string")
    try:
        parsed_uuid = uuid.UUID(identifier)
    except ValueError as error:
        raise ValidationError("Information.UUID is invalid") from error
    require(str(parsed_uuid).lower() == identifier.lower(), "UUID must use canonical hyphenated format")
    require(parsed_uuid.int != 0, "UUID must not be nil")

    qemu = section(config, "QEMU")
    require(qemu.get("Hypervisor") is True, "Hypervisor must be enabled")
    require(qemu.get("UEFIBoot") is True, "UEFIBoot must be enabled")
    # Enabling TPM starts a separate helper with an implicit writable state
    # file. This profile neither needs that helper nor validates its state.
    require(qemu.get("TPMDevice") is False, "TPMDevice is unsupported and must be disabled")
    if prepared:
        require(qemu.get("DebugLog") is False, "Debug logging must be disabled")
    require(not qemu.get("MachinePropertyOverride"), "MachinePropertyOverride is unsupported")
    require(section(config, "Network", list) == [], "Network must be empty: adapters use the approved socket topology")
    arguments = qemu.get("AdditionalArguments")
    require(isinstance(arguments, list) and arguments, "AdditionalArguments must be a nonempty string array")
    tokens = []
    for argument in arguments:
        require(isinstance(argument, str), "AdditionalArguments entries must be strings")
        # These known arguments need no quotes or escapes. UTM has its own
        # parser, so accepting shell syntax via shlex would be misleading.
        require(not any(c in argument for c in "\"'\\\x00"), "Quotes, escapes, and NULs are unsupported in AdditionalArguments")
        tokens.extend(argument.split())
    allowed = [network_tokens(role) + disk_tokens()]
    if not prepared:
        allowed.append(network_tokens(role))
        if role == "Gateway":
            allowed.append(network_tokens(role, wildcard=True))
    require(tokens in allowed, f"Unexpected {role} QEMU arguments; only the approved network and disk settings are allowed")

    require(section(config, "Serial", list) == [], "Serial devices are unsupported")
    # UTM treats Sound.Hardware as an arbitrary QEMU device string, rather
    # than decoding a closed enum. Validate this path as well as arguments.
    sounds = config.get("Sound", [])
    require(isinstance(sounds, list) and len(sounds) <= 1 and all(
        isinstance(sound, dict) and sound.get("Hardware") == "intel-hda"
        for sound in sounds
    ), "Sound must be empty or contain only the approved intel-hda device")
    sharing = section(config, "Sharing")
    require(sharing.get("DirectoryShareMode") == "None", "Directory sharing must be disabled")
    require(type(sharing.get("ClipboardSharing")) is bool, "ClipboardSharing must be a boolean")
    if prepared:
        require(sharing["ClipboardSharing"] is False, "Clipboard sharing must be disabled")
    require(section(config, "Input").get("UsbSharing") is False, "USB sharing must be disabled")
    displays = section(config, "Display", list)
    require(len(displays) == 1 and isinstance(displays[0], dict), "Exactly one display is required")
    hardware = displays[0].get("Hardware")
    allowed_displays = ["virtio-ramfb", "virtio-gpu-pci"]
    if not prepared:
        allowed_displays.append("virtio-gpu-gl-pci")
    require(hardware in allowed_displays, "Unsupported display; prepared bundles require non-GL VirtIO graphics")
    if prepared:
        require(displays[0].get("NativeResolution") is False, "Native resolution must be disabled")
        require(displays[0].get("DynamicResolution") is True, "Dynamic resolution must be enabled")

    drives = section(config, "Drive", list)
    require(len(drives) == 1 and isinstance(drives[0], dict), "Exactly one disk is required")
    drive = drives[0]
    require(drive.get("Identifier") == "0", "Disk Identifier must be '0' for drive0 overrides")
    require(drive.get("ImageType") == "Disk" and drive.get("Interface") == "VirtIO", "Disk must use VirtIO")
    require(drive.get("ReadOnly") is False, "Disk must be writable")
    image_name = drive.get("ImageName")
    require(isinstance(image_name, str) and re.fullmatch(r"Whonix-" + role + r"(?:-[A-Za-z0-9._]+)*\.raw", image_name) is not None,
            "Disk must be a role-matching Whonix .raw file directly inside Data")
    data = bundle / "Data"
    trusted_metadata(data, "Data", directory=True)
    # UTM also opens this implicit writable firmware disk.
    # Fresh upstream bundles do not contain it yet.
    trusted_metadata(data / "efi_vars.fd", "efi_vars.fd", allow_missing=True)
    disk = data / image_name
    disk_stat = trusted_metadata(disk, "Disk")
    require(disk_stat.st_size >= 512,
            "Disk must be a nonempty raw image of at least 512 bytes")
    with disk.open("rb") as stream:
        require(stream.read(4) != b"QFI\xfb", "Disk contains QCOW data despite its .raw extension")
    return role


def process_bundle(bundle, check=False):
    bundle = trusted_bundle_path(Path(bundle))
    if check:
        require(bundle.stat().st_mode & 0o077 == 0,
                "Prepared bundle directory must not allow group/other access")
    config_path = bundle / "config.plist"
    trusted_metadata(config_path, "config.plist")
    with config_path.open("rb") as stream:
        config = plistlib.load(stream)
    role = validate(bundle, config, prepared=check)
    if check:
        return role
    config = copy.deepcopy(config)
    config["System"].update(system_profile(role))
    config["QEMU"]["AdditionalArguments"] = [" ".join(network_tokens(role) + disk_tokens())]
    config["QEMU"]["DebugLog"] = False
    config["Sharing"]["ClipboardSharing"] = False
    config["Display"][0]["Hardware"] = "virtio-ramfb"
    config["Display"][0]["NativeResolution"] = False
    config["Display"][0]["DynamicResolution"] = True
    config["Information"]["UUID"] = str(uuid.uuid4()).upper()
    validate(bundle, config, prepared=True)
    payload = plistlib.dumps(config, fmt=plistlib.FMT_XML, sort_keys=False)
    temporary = None
    try:
        os.chmod(bundle, 0o700)
        with tempfile.NamedTemporaryFile(dir=bundle, prefix=".config-", suffix=".plist", delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config_path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return role


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="unpacked .utm bundle; VM must be stopped")
    parser.add_argument("--check", action="store_true", help="validate prepared settings without changing any file")
    args = parser.parse_args(argv)
    try:
        role = process_bundle(args.bundle, check=args.check)
    except (ValidationError, OSError, plistlib.InvalidFileException, ValueError, ExpatError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"{'Checked' if args.check else 'Prepared'} Whonix-{role}: {args.bundle}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
