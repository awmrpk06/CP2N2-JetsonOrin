"""HTTP control-plane process used by the A6 distributed testbed."""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from adapters.remote_edge_adapter import RemoteEdgeAdapter
from core.orchestrator import CP2N2Orchestrator
from core.task_model import TaskRequest
from testbed.config import NetworkProfile, load_network_profile
from testbed.context import (
    PARENT_SPAN_HEADER,
    TRACE_ID_HEADER,
    bind_trace,
    reset_trace,
)
from testbed.observability import JsonlSpanRecorder


class ControlPlaneServiceState:
    def __init__(
        self,
        *,
        adapter_url: str,
        profile: NetworkProfile,
        trace_path: str | Path | None,
    ) -> None:
        self.profile = profile
        self.recorder = JsonlSpanRecorder(trace_path, "control-plane")
        self.orchestrator = CP2N2Orchestrator()
        self.orchestrator.register_adapter(RemoteEdgeAdapter(adapter_url))


class ControlPlaneHandler(BaseHTTPRequestHandler):
    state: ControlPlaneServiceState

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        if self.path == "/resources":
            self._send_json(
                200,
                {"resources": self.state.orchestrator.discover_resource_contracts()},
            )
            return
        self._send_json(404, {"error": f"Unknown endpoint: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/execute":
            self._send_json(404, {"error": f"Unknown endpoint: {self.path}"})
            return
        payload = self._read_json_body()
        trace_id = self.headers.get(TRACE_ID_HEADER, uuid4().hex)
        parent_span_id = self.headers.get(PARENT_SPAN_HEADER)
        attributes: dict[str, Any] = {
            "method": "POST",
            "path": self.path,
            "client_id": payload.get("task", {}).get("client_id"),
        }
        with self.state.recorder.span(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            operation="execute_task",
            profile_id=self.state.profile.profile_id,
            attributes=attributes,
        ) as span_id:
            tokens = bind_trace(trace_id, span_id)
            try:
                task = TaskRequest.model_validate(payload["task"]).model_copy(
                    update={"correlation_id": trace_id}
                )
                result = self.state.orchestrator.execute_task(task)
                attributes["success"] = result.success
                attributes["error_code"] = (
                    None if result.error is None else str(result.error.code)
                )
                attributes["status_code"] = 200
                self._send_json(200, result.model_dump(mode="json"))
            except Exception as exc:
                attributes["status_code"] = 500
                attributes["error_type"] = type(exc).__name__
                self._send_json(
                    500,
                    {"error": type(exc).__name__, "message": str(exc)},
                )
            finally:
                reset_trace(tokens)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _read_json_body(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b"{}"
        return json.loads(raw_body.decode("utf-8"))

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--adapter-url", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--trace-path")
    args = parser.parse_args()

    profile = load_network_profile(args.profile)
    ControlPlaneHandler.state = ControlPlaneServiceState(
        adapter_url=args.adapter_url,
        profile=profile,
        trace_path=args.trace_path,
    )
    server = ThreadingHTTPServer((args.host, args.port), ControlPlaneHandler)
    print(
        f"Control plane listening on http://{args.host}:{args.port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
