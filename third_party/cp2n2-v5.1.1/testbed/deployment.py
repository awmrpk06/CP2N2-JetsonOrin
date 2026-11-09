"""Launch and stop the versioned local multi-process A6 topology."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO
from urllib.error import URLError
from urllib.request import urlopen


@dataclass
class ManagedService:
    service_id: str
    process: subprocess.Popen[str]
    base_url: str
    log_handle: IO[str]

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.log_handle.close()


@dataclass
class DistributedTestbed:
    adapter: ManagedService
    control_plane: ManagedService
    gateway: ManagedService

    @property
    def gateway_url(self) -> str:
        return self.gateway.base_url

    def stop(self) -> None:
        for service in (self.gateway, self.control_plane, self.adapter):
            service.stop()


def _free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def _wait_until_ready(service: ManagedService, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if service.process.poll() is not None:
            raise RuntimeError(
                f"{service.service_id} exited with code "
                f"{service.process.returncode}; inspect its service log"
            )
        try:
            with urlopen(service.base_url + "/health", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            time.sleep(0.05)
    raise RuntimeError(f"{service.service_id} did not become ready")


def _start(
    *,
    service_id: str,
    module: str,
    host: str,
    port: int,
    arguments: list[str],
    project_root: Path,
    log_dir: Path,
) -> ManagedService:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_handle = (log_dir / f"{service_id}.log").open(
        "w", encoding="utf-8"
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            module,
            "--host",
            host,
            "--port",
            str(port),
            *arguments,
        ],
        cwd=str(project_root),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    service = ManagedService(
        service_id=service_id,
        process=process,
        base_url=f"http://{host}:{port}",
        log_handle=log_handle,
    )
    _wait_until_ready(service)
    return service


def start_local_distributed_testbed(
    *,
    project_root: Path,
    profile_path: Path,
    output_dir: Path,
    host: str = "127.0.0.1",
    timeout_s: float = 10.0,
) -> DistributedTestbed:
    """Start adapter, control plane, and gateway as independent processes."""

    traces_dir = output_dir / "traces"
    logs_dir = output_dir / "service-logs"
    services: list[ManagedService] = []
    try:
        adapter = _start(
            service_id="adapter-service",
            module="remote.edge_service",
            host=host,
            port=_free_port(host),
            arguments=[
                "--profile",
                str(profile_path),
                "--trace-path",
                str(traces_dir / "adapter-service.jsonl"),
            ],
            project_root=project_root,
            log_dir=logs_dir,
        )
        services.append(adapter)
        control = _start(
            service_id="control-plane",
            module="remote.control_plane_service",
            host=host,
            port=_free_port(host),
            arguments=[
                "--adapter-url",
                adapter.base_url,
                "--profile",
                str(profile_path),
                "--trace-path",
                str(traces_dir / "control-plane.jsonl"),
            ],
            project_root=project_root,
            log_dir=logs_dir,
        )
        services.append(control)
        gateway = _start(
            service_id="gateway",
            module="remote.gateway_service",
            host=host,
            port=_free_port(host),
            arguments=[
                "--control-url",
                control.base_url,
                "--profile",
                str(profile_path),
                "--trace-path",
                str(traces_dir / "gateway.jsonl"),
                "--timeout-s",
                str(timeout_s),
            ],
            project_root=project_root,
            log_dir=logs_dir,
        )
        services.append(gateway)
        return DistributedTestbed(
            adapter=adapter,
            control_plane=control,
            gateway=gateway,
        )
    except Exception:
        for service in reversed(services):
            service.stop()
        raise
