#!/usr/bin/env python3
"""Verify, export, or stage the retained Quake2 core; never build or deploy."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "release/os30-quake2"
sys.dont_write_bytecode = True


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or str(path) != name or "\\" in name:
        raise ValueError("Unsafe member: " + name)
    return name


def cleanpath(path):
    path = Path(path).absolute()
    if ".." in path.parts or any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError("Symlink or noncanonical destination: " + str(path))
    return path


def read_profile():
    profile = json.loads((PROFILE / "profile.json").read_text())
    if profile["schema"] != 1 or profile["profile"] != "os30-quake2":
        raise ValueError("Unknown release profile")
    for name, expected in profile["files"].items():
        path = PROFILE / safe_name(name)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Missing regular profile file: " + name)
        if path.stat().st_size != expected["bytes"] or sha(path) != expected["sha256"]:
            raise ValueError("Profile checksum mismatch: " + name)
    archive = safe_name(profile["archive"])
    if archive not in profile["files"]:
        raise ValueError("Qualification archive must be checksummed")
    return profile


def unpack(archive, destination):
    """Extract regular files only; avoid tar extraction and link semantics."""
    seen = set()
    total = 0
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream:
            name = safe_name(member.name)
            total += member.size
            if name in seen or not member.isfile() or member.size < 0 or total > 512 * 1024 * 1024:
                raise ValueError("Invalid/duplicate/oversize archive member: " + name)
            seen.add(name)
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            source = stream.extractfile(member)
            if source is None:
                raise ValueError("Unreadable archive member: " + name)
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)


def invoke(script, *args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    # Respect TMPDIR; callers in restricted environments can select a writable path.
    subprocess.run([sys.executable, str(script), *map(str, args)], check=True, env=env)


def verify_payload(payload, profile):
    invoke(payload / "verify.py", payload)
    actual = json.loads((payload / "qualification.json").read_text())
    if actual != profile["qualification"]:
        raise ValueError("Qualification differs from supported profile")
    if sha(payload / "core/os30.rbf_r") != actual["bitstream_sha256"]:
        raise ValueError("Wrong paired core image")
    if sha(payload / "app/app.elf") != actual["app_sha256"]:
        raise ValueError("Wrong paired application")


def add_notices(destination):
    shutil.copytree(PROFILE / "licenses", destination / "release-notices")
    for source, name in [("profile.json", "shipping-profile.json"),
                         ("README.txt", "SHIPPING.txt"),
                         ("hardware-confirmation.json", "hardware-confirmation.json")]:
        shutil.copyfile(PROFILE / source, destination / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["verify", "export", "stage"])
    parser.add_argument("--output", type=Path, help="New directory; never overwrites an existing tree")
    args = parser.parse_args()
    if args.action == "verify" and args.output is not None:
        parser.error("--output applies to export/stage")
    destination = cleanpath(args.output or ROOT / "build/releases" /
                            ("os30-quake2-sources" if args.action == "stage" else "os30-quake2"))
    if args.action != "verify" and (destination.exists() or destination.is_relative_to(PROFILE)):
        parser.error("Output must be a new directory outside the retained profile")
    profile = read_profile()
    scratch = ROOT / "build/release-checks"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="os30-quake2-", dir=scratch) as tmp:
        payload = Path(tmp) / "payload"
        payload.mkdir()
        unpack(PROFILE / profile["archive"], payload)
        verify_payload(payload, profile)
        if args.action == "export":
            shutil.copytree(payload, destination / "qualification")
            add_notices(destination)
        elif args.action == "stage":
            invoke(payload / "stage.py", payload, destination)
            add_notices(destination)
    print("PASS: retained Quake2 I64 core/app pair; 90.69 MHz estimate, configured 100 MHz.")
    if args.action != "verify":
        print(destination)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print("Release validation failed: " + str(error), file=sys.stderr)
        sys.exit(1)
