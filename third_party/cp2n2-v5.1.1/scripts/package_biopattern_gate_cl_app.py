"""Build the self-contained CL app and emit a deterministic checksum manifest."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).parents[1].resolve()
APP = ROOT / "cl-apps" / "cp2n2-biopattern-gate"
ARCHIVE = APP.parent / f"{APP.name}.zip"
MANIFEST = APP.parent / f"{APP.name}.package-manifest.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sync_biopattern_gate_cl_app.py")],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-X", "utf8", "-m", "cl.app.pack", str(APP)],
        cwd=ROOT,
        check=True,
    )
    archive_bytes = ARCHIVE.read_bytes()
    with zipfile.ZipFile(ARCHIVE) as bundle:
        entries = {
            info.filename: {
                "sha256": sha256_bytes(bundle.read(info.filename)),
                "size_bytes": info.file_size,
            }
            for info in sorted(bundle.infolist(), key=lambda item: item.filename)
        }
    manifest = {
        "manifest_version": "1.0",
        "application_id": APP.name,
        "application_version": json.loads(
            (APP / "info.json").read_text(encoding="utf-8")
        )["version"],
        "archive": {
            "filename": ARCHIVE.name,
            "sha256": sha256_bytes(archive_bytes),
            "size_bytes": len(archive_bytes),
        },
        "entries": entries,
    }
    with MANIFEST.open("w", encoding="utf-8", newline="\n") as manifest_file:
        manifest_file.write(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
    print(f"wrote {MANIFEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
