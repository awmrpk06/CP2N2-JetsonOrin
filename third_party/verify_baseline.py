"""Verify the frozen CP2N2 source and portable Git bundle without external packages."""

from pathlib import Path
import hashlib
import json


def main():
    root = Path(__file__).resolve().parent
    manifest = json.loads((root / "cp2n2-v5.1.1.manifest.json").read_text(encoding="utf-8"))
    snapshot = root / manifest["snapshot_directory"]
    expected = {entry["path"] for entry in manifest["files"]}
    actual = {path.relative_to(snapshot).as_posix() for path in snapshot.rglob("*") if path.is_file()}
    if actual != expected:
        raise SystemExit(f"Source file-set mismatch: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
    for entry in manifest["files"]:
        data = (snapshot / entry["path"]).read_bytes()
        blob = b"blob " + str(len(data)).encode() + b"\0" + data
        if (len(data) != entry["size_bytes"]
                or hashlib.sha256(data).hexdigest() != entry["sha256"]
                or hashlib.sha1(blob).hexdigest() != entry["git_blob_sha1"]):
            raise SystemExit(f"Source checksum mismatch: {entry['path']}")
    bundle = manifest["git_bundle"]
    data = (root / bundle["path"]).read_bytes()
    if len(data) != bundle["size_bytes"] or hashlib.sha256(data).hexdigest() != bundle["sha256"]:
        raise SystemExit("Git bundle checksum mismatch")
    print(f"Verified {len(expected)} unchanged source files and Git bundle for {manifest['release']} ({manifest['upstream_commit']}).")


if __name__ == "__main__":
    main()
