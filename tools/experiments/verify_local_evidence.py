#!/usr/bin/env python3
"""Read-only hash check of locally retained experiment payloads."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    inventory = root / "tools/experiments/LOCAL_EVIDENCE_20261014.json"
    data = json.loads(inventory.read_text())
    assert data["schema"] == 1
    seen = set()
    failures = []
    total = 0
    for name, size, expected in data["local_only_files"]:
        relative = PurePosixPath(name)
        assert not relative.is_absolute() and ".." not in relative.parts
        assert relative.parts[:2] == ("tools", "experiments")
        assert str(relative) == name and name not in seen
        assert isinstance(size, int) and size >= 0
        assert len(expected) == 64 and all(c in "0123456789abcdef" for c in expected)
        seen.add(name)
        path = root / name
        if any(part.is_symlink() for part in [path, *path.parents] if part != root):
            failures.append([name, "symlink"])
            continue
        if not path.is_file():
            failures.append([name, "missing"])
            continue
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(block)
        if path.stat().st_size != size or h.hexdigest() != expected:
            failures.append([name, "size or SHA256 mismatch"])
        total += size
    assert len(seen) == sum(row["local_only_files"] for row in data["scope"])
    assert total == sum(row["local_only_bytes"] for row in data["scope"]) or failures
    print(json.dumps({"status": "FAIL" if failures else "PASS",
                      "files": len(seen), "expected_bytes": sum(
                          row["local_only_bytes"] for row in data["scope"]),
                      "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
