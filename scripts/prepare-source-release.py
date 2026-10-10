#!/usr/bin/env python3
"""Package a reviewed, committed source tree; never read local VM state.

Run the tests separately against the extracted archive. This command does not
publish, sign, tag, or certify a release. Requires Python 3.9+ and Git.
"""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile


MANIFEST = "release/source-files.txt"
MAX_FILE_BYTES = 2 * 1024 * 1024


def git(repo, *arguments):
    return subprocess.run(
        ["git", "-C", str(repo), *arguments], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def read_tree(repo, commit):
    entries = {}
    for record in git(repo, "ls-tree", "-rz", commit).split(b"\0"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        path = name.decode("utf-8")
        if (not re.fullmatch(r"[A-Za-z0-9_.\-/]+", path)
                or PurePosixPath(path).is_absolute()
                or any(part in (".", "..", ".git") for part in path.split("/"))):
            raise ValueError("Unsafe source path in committed tree")
        if kind != "blob" or mode not in ("100644", "100755"):
            raise ValueError("Source releases require regular files: " + path)
        size = int(git(repo, "cat-file", "-s", oid))
        if size > MAX_FILE_BYTES:
            raise ValueError("Oversized source file: " + path)
        entries[path] = (mode, oid)
    if MANIFEST not in entries:
        raise ValueError("Missing reviewed source manifest")
    manifest = git(repo, "cat-file", "blob", entries[MANIFEST][1]).decode("utf-8")
    approved = [line for line in manifest.splitlines() if line and not line.startswith("#")]
    if len(approved) != len(set(approved)) or set(approved) != set(entries):
        raise ValueError("Committed tree differs from reviewed source manifest")
    return entries


def package(repo, output, version, ref="HEAD"):
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+-alpha\.[0-9]+", version):
        raise ValueError("Version must identify an alpha, e.g. v0.1.0-alpha.1")
    # Requiring a clean tracked tree avoids silently dropping pending fixes.
    # Untracked files are deliberately never included, even if not ignored.
    if git(repo, "status", "--porcelain", "--untracked-files=no").strip():
        raise ValueError("Commit or isolate tracked changes before packaging")
    commit = git(repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").decode().strip()
    entries = read_tree(repo, commit)
    prefix = "whonix-apple-silicon-" + version
    archive_name = prefix + "-source.tar.gz"
    manifest_name = prefix + "-manifest.json"
    data = io.BytesIO()
    files = []
    with gzip.GzipFile(filename="", mode="wb", fileobj=data, mtime=0) as compressed:
        with tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as archive:
            for name, (mode, oid) in sorted(entries.items()):
                content = git(repo, "cat-file", "blob", oid)
                member = tarfile.TarInfo(prefix + "/" + name)
                member.size = len(content)
                member.mode = 0o755 if mode == "100755" else 0o644
                member.mtime = 0
                member.uid = member.gid = 0
                member.uname = member.gname = ""
                archive.addfile(member, io.BytesIO(content))
                files.append({"path": name, "mode": format(member.mode, "04o"),
                              "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    archive_bytes = data.getvalue()
    manifest = {
        "schema_version": 1,
        "version": version,
        "kind": "experimental-source-only",
        "source_commit": commit,
        "archive": archive_name,
        "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
        "files": files,
    }
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    checksums = "".join(hashlib.sha256(content).hexdigest() + "  " + name + "\n"
                        for name, content in ((archive_name, archive_bytes), (manifest_name, manifest_bytes)))
    # A fresh output directory is mandatory. Never overwrite an earlier release.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    (output / archive_name).write_bytes(archive_bytes)
    (output / manifest_name).write_bytes(manifest_bytes)
    (output / "SHA256SUMS").write_text(checksums, encoding="ascii")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--ref", default="HEAD", help="Committed source revision (default: HEAD)")
    parser.add_argument("--output", required=True, type=Path, help="New output directory")
    args = parser.parse_args()
    try:
        result = package(Path(__file__).resolve().parents[1], args.output, args.version, args.ref)
    except (OSError, ValueError, subprocess.CalledProcessError):
        # Do not echo subprocess stderr or arbitrary Git/file content.
        parser.exit(1, "Source packaging failed. Check the revision, clean tracked tree, "
                    "reviewed manifest, regular file limits, and new output directory.\n")
    print(json.dumps({key: result[key] for key in ("version", "source_commit", "archive", "archive_sha256")}, indent=2))


if __name__ == "__main__":
    main()
