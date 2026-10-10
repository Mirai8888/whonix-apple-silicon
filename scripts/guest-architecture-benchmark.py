#!/usr/bin/python3
"""Run the pinned browser workload on disposable ARM64 or x86-64 guests.

Install this wrapper and the unchanged browser backend as root:root 0600 in
STATE (root:root 0700), with an empty root:root 0600 enabled marker. The service
powers off on pre-claim failure; after claiming, the backend always requests
poweroff. This wrapper never changes the architecture reported by Python.
"""

import configparser
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import struct
import sys
import types


STATE = Path("/var/lib/whonix-architecture-benchmark")
BACKEND_NAME = "guest-browser-benchmark.py"
BACKEND_SHA256 = "107a3c854dddd720d922b45f65cbfdbd6b918e955dad205382f202558f22298c"
MAX_BACKEND_BYTES = 64 * 1024
MAX_INI_BYTES = 128 * 1024
ARCHITECTURES = {"aarch64": (183, "/dev/ttyAMA0"), "x86_64": (62, "/dev/ttyS0")}


def runtime_architecture():
    architecture = platform.machine()
    if platform.system() != "Linux" or architecture not in ARCHITECTURES or os.geteuid() != 0:
        raise RuntimeError("Requires root on Linux aarch64 or x86_64 in a disposable benchmark clone.")
    return architecture


def require_root_path(path, directory=False, private=False):
    info = path.lstat()
    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected_kind(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
            or info.st_mode & 0o022):
        raise ValueError(f"Unsafe benchmark path: {path}")
    if private and stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600):
        raise ValueError(f"Benchmark path must have exact private permissions: {path}")
    if not directory and info.st_nlink != 1:
        raise ValueError(f"Unexpected hard links: {path}")
    return info


def validate_state():
    for path in (Path("/"), Path("/var"), Path("/var/lib")):
        require_root_path(path, directory=True)
    require_root_path(STATE, directory=True, private=True)


def load_backend():
    runtime_architecture()
    validate_state()
    wrapper = Path(__file__)
    if wrapper != STATE / "guest-architecture-benchmark.py":
        raise ValueError("Wrapper must run from its dedicated private state directory.")
    require_root_path(wrapper, private=True)
    source_path = STATE / BACKEND_NAME
    require_root_path(source_path, private=True)
    descriptor = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or not 1 <= info.st_size <= MAX_BACKEND_BYTES):
            raise ValueError("Unsafe or oversized browser backend.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            source = stream.read(MAX_BACKEND_BYTES + 1)
        if len(source) != info.st_size or hashlib.sha256(source).hexdigest() != BACKEND_SHA256:
            raise ValueError("Browser backend differs from the pinned benchmark source.")
    finally:
        os.close(descriptor)
    # Execute exactly the bytes that were verified, never reopen the filename.
    backend = types.ModuleType("architecture_browser_backend")
    backend.__file__ = str(source_path)
    exec(compile(source, str(source_path), "exec"), backend.__dict__)
    return backend


def claim_run():
    runtime_architecture()
    validate_state()
    marker = STATE / "enabled"
    try:
        info = require_root_path(marker, private=True)
    except FileNotFoundError as error:
        # Returning success here would leave a disposable VM running forever.
        raise ValueError("Missing one-use benchmark marker.") from error
    if info.st_size != 0:
        raise ValueError("Enabled marker must be empty.")
    if os.cpu_count() != 4:
        raise ValueError("Architecture benchmark accepts exactly four vCPUs.")
    marker.unlink()
    return True


def read_browser_file(path, uid, maximum, prefix=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_nlink != 1
                or info.st_mode & 0o022 or not 1 <= info.st_size <= maximum):
            raise ValueError(f"Unsafe or oversized browser metadata file: {path.name}")
        bound = maximum + 1 if prefix is None else prefix
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(bound)
        expected = info.st_size if prefix is None else min(prefix, info.st_size)
        if len(data) != expected:
            raise ValueError("Browser metadata changed while reading.")
        return data
    finally:
        os.close(descriptor)


def parse_ini(data):
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.read_string(data.decode("utf-8"))
    return parser


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate browser package metadata key.")
        result[key] = value
    return result


def browser_metadata(backend, account):
    # browser_user() has already validated every ancestor of this directory.
    directory = backend.BENCH.parent.parent
    application = read_browser_file(directory / "application.ini", account.pw_uid, MAX_INI_BYTES)
    platform_ini = read_browser_file(directory / "platform.ini", account.pw_uid, MAX_INI_BYTES)
    package_json = read_browser_file(directory / "tbb_version.json", account.pw_uid, MAX_INI_BYTES)
    package = json.loads(package_json, object_pairs_hook=unique_object)
    architecture = runtime_architecture()
    if (not isinstance(package, dict) or package.get("version") != "16.0a11"
            or package.get("architecture") != "linux-" + architecture or package.get("channel") != "alpha"):
        raise ValueError("Expected Tor Browser 16.0a11 alpha for the actual guest architecture.")
    application_fields, platform_fields = parse_ini(application), parse_ini(platform_ini)
    application_version = application_fields.get("App", "Version")
    build_id = application_fields.get("App", "BuildID")
    platform_build_id = platform_fields.get("Build", "BuildID")
    if not re.fullmatch(r"[0-9][A-Za-z0-9.+_-]{0,63}", application_version):
        raise ValueError("Unexpected browser version metadata.")
    if any(not re.fullmatch(r"[0-9]{8,32}", value) for value in (build_id, platform_build_id)):
        raise ValueError("Unexpected browser build identifier.")
    executable = read_browser_file(directory / "firefox.real", account.pw_uid, 512 * 1024 * 1024, prefix=64)
    if len(executable) != 64 or executable[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Expected a 64-bit little-endian ELF browser executable.")
    if struct.unpack_from("<H", executable, 18)[0] != ARCHITECTURES[architecture][0]:
        raise ValueError("Browser executable architecture differs from the guest kernel.")
    return {"version": package["version"], "channel": package["channel"],
            "package_architecture": package["architecture"], "application_version": application_version,
            "build_id": build_id, "platform_build_id": platform_build_id,
            "architecture": architecture, "executable": "firefox.real",
            "application_ini_sha256": hashlib.sha256(application).hexdigest(),
            "platform_ini_sha256": hashlib.sha256(platform_ini).hexdigest(),
            "tbb_version_json_sha256": hashlib.sha256(package_json).hexdigest()}


def configure_backend(backend):
    architecture = runtime_architecture()
    backend.STATE = STATE
    backend.SERIAL = Path(ARCHITECTURES[architecture][1])
    backend.LAUNCH_TIMEOUT_SECONDS = 180
    backend.claim_run = claim_run
    original_browser_user, original_write_json = backend.browser_user, backend.write_json
    original_serial_line = backend.serial_line
    metadata = {}

    def measured_browser_user():
        account = original_browser_user()
        metadata.update(browser_metadata(backend, account))
        return account

    def write_json(document):
        document["browser_metadata"] = dict(metadata)
        document["backend_sha256"] = BACKEND_SHA256
        return original_write_json(document)

    def serial_line(line):
        # A login prompt can leave the shared console mid-line. Keep the pinned
        # writer and its bounds, but frame each benchmark record on its own line.
        return original_serial_line("\n" + line)

    backend.browser_user = measured_browser_user
    backend.write_json = write_json
    backend.serial_line = serial_line


def main():
    try:
        backend = load_backend()
        configure_backend(backend)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return backend.main()


if __name__ == "__main__":
    sys.exit(main())
