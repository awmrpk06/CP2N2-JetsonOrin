"""Verify the included baseline, measured runner and scientific trial archives."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    root = Path(__file__).resolve().parents[1]
    metadata = json.loads((root / "artifact-manifest.json").read_text(encoding="utf-8"))
    subprocess.run([sys.executable, str(root / "third_party/verify_baseline.py")], check=True)
    modules = metadata["measured_runner_provenance"]["files_sha256"]
    for name, expected in modules.items():
        if sha256(root / name) != expected:
            raise SystemExit(f"Measured runner checksum mismatch: {name}")
    manifests = files = 0
    for directory in metadata["campaign_directories"]:
        campaign = root / directory
        if not campaign.is_dir():
            raise SystemExit(f"Missing campaign: {directory}")
        for path in campaign.rglob("reproduction-manifest.json"):
            manifests += 1
            manifest = json.loads(path.read_text(encoding="utf-8"))
            for name, expected in manifest.items():
                if sha256(path.parent / name) != expected:
                    raise SystemExit(f"Trial archive checksum mismatch: {path.parent / name}")
                files += 1
    if not manifests:
        raise SystemExit("No scientific trial manifests were found")
    print(f"Verified {len(modules)} measured evaluation modules, {manifests} trial manifests and {files} recorded files.")


if __name__ == "__main__":
    main()
