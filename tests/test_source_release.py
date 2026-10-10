"""Exercise source distribution boundaries using disposable Git repositories."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location(
    "source_release", Path(__file__).resolve().parents[1] / "scripts/prepare-source-release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class SourceReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        (self.repo / "README.md").write_text("Fixture source.\n")
        (self.repo / "run.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.repo / "run.sh").chmod(0o755)
        (self.repo / "release").mkdir()
        (self.repo / release.MANIFEST).write_text("README.md\nrun.sh\nrelease/source-files.txt\n")
        self.commit()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def commit(self):
        self.git("add", ".")
        self.git("-c", "user.name=Release Fixture", "-c", "user.email=fixture@example.invalid",
                 "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture")

    def package(self, name="output", **kwargs):
        return release.package(self.repo, self.root / name, "v0.1.0-alpha.1", **kwargs)

    def test_reproducible_committed_source_excludes_untracked_state_and_metadata(self):
        (self.repo / "artifacts").mkdir()
        (self.repo / "artifacts/local-state.txt").write_text("Never distribute this fixture.")
        first = self.package("first")
        self.package("second")
        for item in (self.root / "first").iterdir():
            self.assertEqual(item.read_bytes(), (self.root / "second" / item.name).read_bytes())
        with tarfile.open(self.root / "first" / first["archive"], "r:gz") as archive:
            members = archive.getmembers()
            self.assertEqual(len(members), 3)
            self.assertTrue(all(member.isfile() and member.uid == member.gid == member.mtime == 0
                                and not member.uname and not member.gname for member in members))
            self.assertTrue(all("artifacts" not in member.name and "/.git/" not in member.name
                                for member in members))
            script = next(member for member in members if member.name.endswith("/run.sh"))
            self.assertEqual(script.mode, 0o755)
        for line in (self.root / "first/SHA256SUMS").read_text().splitlines():
            digest, filename = line.split("  ")
            self.assertEqual(digest, hashlib.sha256((self.root / "first" / filename).read_bytes()).hexdigest())
        manifest = json.loads(next((self.root / "first").glob("*-manifest.json")).read_text())
        self.assertEqual(manifest["source_commit"], self.git("rev-parse", "HEAD").decode().strip())
        for entry in manifest["files"]:
            self.assertEqual(entry["sha256"], hashlib.sha256((self.repo / entry["path"]).read_bytes()).hexdigest())

    def test_dirty_tracked_source_is_rejected(self):
        (self.repo / "README.md").write_text("Pending change.\n")
        with self.assertRaisesRegex(ValueError, "tracked changes"):
            self.package()
        self.assertFalse((self.root / "output").exists())

    def test_unreviewed_tracked_file_is_rejected(self):
        (self.repo / "unexpected.txt").write_text("Not reviewed.\n")
        self.commit()
        with self.assertRaisesRegex(ValueError, "reviewed source manifest"):
            self.package()

    def test_committed_symlink_is_rejected_even_when_listed(self):
        (self.repo / "linked").symlink_to("README.md")
        with (self.repo / release.MANIFEST).open("a") as stream:
            stream.write("linked\n")
        self.commit()
        with self.assertRaisesRegex(ValueError, "regular files"):
            self.package()

    def test_oversized_source_is_rejected(self):
        (self.repo / "README.md").write_bytes(b"x" * (release.MAX_FILE_BYTES + 1))
        self.commit()
        with self.assertRaisesRegex(ValueError, "Oversized"):
            self.package()

    def test_existing_release_is_not_overwritten(self):
        self.package()
        before = (self.root / "output/SHA256SUMS").read_bytes()
        with self.assertRaises(FileExistsError):
            self.package()
        self.assertEqual(before, (self.root / "output/SHA256SUMS").read_bytes())

    def test_version_cannot_escape_output_or_claim_stable_release(self):
        for version in ("../escape", "v1.0.0", "v0.1.0-alpha.1/extra"):
            with self.subTest(version=version), self.assertRaises(ValueError):
                release.package(self.repo, self.root / "output", version)


if __name__ == "__main__":
    unittest.main()
