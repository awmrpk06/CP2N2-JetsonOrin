"""Helpers to launch and stop a remote backend service for evaluation."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


@dataclass
class ServiceHandle:
    process: subprocess.Popen[str]
    base_url: str

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_remote_edge_service(project_root: Path, host: str = "127.0.0.1") -> ServiceHandle:
    port = _find_free_port()
    script_path = project_root / "remote" / "edge_service.py"
    process = subprocess.Popen(
        [sys.executable, str(script_path), "--host", host, "--port", str(port)],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://{host}:{port}"
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            with urlopen(base_url + "/health", timeout=0.5) as response:
                if response.status == 200:
                    return ServiceHandle(process=process, base_url=base_url)
        except URLError:
            time.sleep(0.1)
        except Exception:
            time.sleep(0.1)

    stdout, stderr = process.communicate(timeout=2)
    raise RuntimeError(
        "Remote edge service did not become ready. "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
