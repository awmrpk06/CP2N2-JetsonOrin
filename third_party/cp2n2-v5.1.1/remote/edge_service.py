"""Small HTTP service exposing a remote edge-style backend."""

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

from adapters.edge_adapter import EdgeAdapter
from core.task_model import TaskRequest
from descriptors.capability_schema import Locality, ResetMode, TelemetryField
from testbed.config import NetworkProfile, load_network_profile
from testbed.context import PARENT_SPAN_HEADER, TRACE_ID_HEADER
from testbed.faults import DeterministicFaultEngine
from testbed.observability import JsonlSpanRecorder


class RemoteEdgeServiceState:
    def __init__(
        self,
        *,
        profile: NetworkProfile | None = None,
        trace_path: str | Path | None = None,
        expose_age_of_information: bool = False,
    ) -> None:
        self.adapter = EdgeAdapter(backend_id="remote-edge-backend")
        descriptor = self.adapter.describe().model_copy(deep=True)
        descriptor.display_name = "Remote Edge Twin Backend"
        descriptor.policy.locality = Locality.FOG
        descriptor.custom_metadata["endpoint_kind"] = "http_remote"
        descriptor.custom_metadata["endpoint_transport"] = "http"
        self.expose_age_of_information = expose_age_of_information
        if expose_age_of_information:
            descriptor.telemetry.supports_age_of_information = True
            if not any(
                metric.name == "age_of_information_ms"
                for metric in descriptor.telemetry.metrics
            ):
                descriptor.telemetry.metrics.append(
                    TelemetryField(
                        name="age_of_information_ms",
                        units="ms",
                        description="Injected or measured telemetry age.",
                        lower_is_better=True,
                    )
                )
        self.descriptor = descriptor
        self.profile = profile or NetworkProfile(
            profile_id="baseline-inline",
            description="No injected faults.",
        )
        self.faults = DeterministicFaultEngine(self.profile, "control_adapter")
        self.recorder = JsonlSpanRecorder(trace_path, "adapter-service")


class EdgeServiceHandler(BaseHTTPRequestHandler):
    state: RemoteEdgeServiceState

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._handle_traced("GET", self.path, None)

    def do_POST(self) -> None:  # noqa: N802
        payload = self._read_json_body()
        self._handle_traced("POST", self.path, payload)

    def _dispatch(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> tuple[int, dict[str, Any]]:
        payload = payload or {}
        if method == "GET" and path == "/describe":
            return 200, self.state.descriptor.model_dump(mode="json")
        if method == "GET" and path == "/telemetry":
            telemetry = self.state.adapter.collect_telemetry()
            telemetry["endpoint_kind"] = "http_remote"
            if self.state.expose_age_of_information:
                telemetry["age_of_information_ms"] = (
                    self.state.profile.telemetry_staleness_ms
                )
            return 200, telemetry
        if method == "POST" and path == "/prepare":
            task = TaskRequest.model_validate(payload["task"])
            result = self.state.adapter.prepare(task)
            return 200, result.model_dump(mode="json")
        if method == "POST" and path == "/invoke":
            task = TaskRequest.model_validate(payload["task"])
            result = self.state.adapter.invoke(task)
            return 200, result.model_dump(mode="json")
        if method == "POST" and path == "/reset":
            mode_raw = payload.get("mode")
            mode = ResetMode(mode_raw) if mode_raw else None
            success = self.state.adapter.reset(mode=mode)
            return 200, {"success": success}
        if method == "POST" and path == "/recalibrate":
            success = self.state.adapter.recalibrate()
            return 200, {"success": success}
        return 404, {"error": f"Unknown endpoint: {path}"}

    def _handle_traced(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> None:
        trace_id = self.headers.get(TRACE_ID_HEADER, uuid4().hex)
        parent_span_id = self.headers.get(PARENT_SPAN_HEADER)
        attributes: dict[str, Any] = {"method": method, "path": path}
        with self.state.recorder.span(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            operation=f"{method} {path}",
            profile_id=self.state.profile.profile_id,
            attributes=attributes,
        ):
            if path != "/describe":
                decision = self.state.faults.decide(trace_id, f"{method} {path}")
                attributes["fault_delay_ms"] = decision.delay_ms
                attributes["fault_dropped"] = decision.dropped
                attributes["fault_partitioned"] = decision.partitioned
                self.state.faults.apply(decision)
                if decision.dropped:
                    attributes["status_code"] = 503
                    self._send_json(
                        503,
                        {
                            "error": "injected_network_fault",
                            "fault": (
                                "partition" if decision.partitioned else "loss"
                            ),
                        },
                    )
                    return
            try:
                status_code, response = self._dispatch(method, path, payload)
            except Exception as exc:
                attributes["status_code"] = 500
                self._send_json(
                    500,
                    {"error": type(exc).__name__, "message": str(exc)},
                )
                return
            attributes["status_code"] = status_code
            self._send_json(status_code, response)

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
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--profile")
    parser.add_argument("--trace-path")
    args = parser.parse_args()

    profile = (
        load_network_profile(args.profile)
        if args.profile
        else NetworkProfile(
            profile_id="baseline-inline",
            description="No injected faults.",
        )
    )
    EdgeServiceHandler.state = RemoteEdgeServiceState(
        profile=profile,
        trace_path=args.trace_path,
        expose_age_of_information=args.profile is not None,
    )
    server = ThreadingHTTPServer((args.host, args.port), EdgeServiceHandler)
    print(f"Remote edge service listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
