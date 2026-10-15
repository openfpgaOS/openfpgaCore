#!/usr/bin/env python3
"""Offline release packaging guards; no Quartus, compiler, or SD access."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("core_release", Path(__file__).resolve().parents[1] / "core_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def archive(self, members):
        path = self.root / "test.tar.gz"
        with tarfile.open(path, "w:gz") as output:
            for name, kind in members:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.size = 1 if kind == tarfile.REGTYPE else 0
                entry.linkname = "../outside" if kind == tarfile.SYMTYPE else ""
                output.addfile(entry, io.BytesIO(b"x") if entry.size else None)
        return path

    def test_export_refuses_existing_directory_before_verification(self):
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "user-data"
        marker.write_text("keep")
        with patch("sys.argv", ["core_release", "export", "--output", str(existing)]):
            with self.assertRaises(SystemExit):
                release.main()
        self.assertEqual(marker.read_text(), "keep")

    def test_archive_rejects_traversal(self):
        with self.assertRaises(ValueError):
            release.unpack(self.archive([("../outside", tarfile.REGTYPE)]), self.root / "out")
        self.assertFalse((self.root / "outside").exists())

    def test_archive_rejects_symlink(self):
        with self.assertRaises(ValueError):
            release.unpack(self.archive([("link", tarfile.SYMTYPE)]), self.root / "out")

    def test_archive_rejects_duplicate(self):
        with self.assertRaises(ValueError):
            release.unpack(self.archive([("same", tarfile.REGTYPE)] * 2), self.root / "out")

    def test_destination_rejects_symlink_parent(self):
        actual = self.root / "actual"
        actual.mkdir()
        alias = self.root / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        with self.assertRaises(ValueError):
            release.cleanpath(alias / "new")
        self.assertFalse((actual / "new").exists())

    def test_profile_rejects_modified_archive(self):
        archive = self.root / "qualification.tar.gz"
        archive.write_bytes(b"original")
        profile = {"schema": 1, "profile": "os30-quake2", "archive": archive.name,
                   "files": {archive.name: {"bytes": 8, "sha256": hashlib.sha256(b"original").hexdigest()}}}
        (self.root / "profile.json").write_text(json.dumps(profile))
        archive.write_bytes(b"modified")
        with patch.object(release, "PROFILE", self.root), self.assertRaises(ValueError):
            release.read_profile()

    def test_profile_cannot_execute_unpinned_archive(self):
        profile = {"schema": 1, "profile": "os30-quake2", "archive": "unchecked.tar.gz", "files": {}}
        (self.root / "profile.json").write_text(json.dumps(profile))
        with patch.object(release, "PROFILE", self.root), self.assertRaises(ValueError):
            release.read_profile()


if __name__ == "__main__":
    unittest.main()
