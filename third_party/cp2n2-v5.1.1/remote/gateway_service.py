"""HTTP gateway process with deterministic A6 link-fault injection."""

from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from testbed.config import NetworkProfile, load_network_profile
from testbed.context import PARENT_SPAN_HEADER, TRACE_ID_HEADER
from testbed.faults import DeterministicFaultEngine
from testbed.observability import JsonlSpanRecorder


class GatewayState:
    def __init__(
        self,
        *,
        control_url: str,
        profile: NetworkProfile,
        trace_path: str | Path | None,
        timeout_s: float,
    ) -> None:
        self.control_url = control_url.rstrip("/")
        self.profile = profile
        self.timeout_s = timeout_s
        self.agent_faults = DeterministicFaultEngine(profile, "agent_gateway")
        self.control_faults = DeterministicFaultEngine(profile, "gateway_control")
        self.recorder = JsonlSpanRecorder(trace_path, "gateway")


class GatewayHandler(BaseHTTPRequestHandler):
    state: GatewayState

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": f"Unknown endpoint: {self.path}"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/execute":
            self._send_json(404, {"error": f"Unknown endpoint: {self.path}"})
            return
        raw_body = self._read_body()
        trace_id = self.headers.get(TRACE_ID_HEADER, uuid4().hex)
        parent_span_id = self.headers.get(PARENT_SPAN_HEADER)
        attributes: dict[str, Any] = {"method": "POST", "path": self.path}
        with self.state.recorder.span(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            operation="forward_execute",
            profile_id=self.state.profile.profile_id,
            attributes=attributes,
        ) as span_id:
            for link_name, engine in (
                ("agent_gateway", self.state.agent_faults),
                ("gateway_control", self.state.control_faults),
            ):
                decision = engine.decide(trace_id, "POST /execute")
                attributes[f"{link_name}_delay_ms"] = decision.delay_ms
                attributes[f"{link_name}_dropped"] = decision.dropped
                attributes[f"{link_name}_partitioned"] = decision.partitioned
                engine.apply(decision)
                if decision.dropped:
                    attributes["status_code"] = 503
                    self._send_json(
                        503,
                        {
                            "error": "injected_network_fault",
                            "link": link_name,
                            "fault": (
                                "partition" if decision.partitioned else "loss"
                            ),
                        },
                    )
                    return

            request = Request(
                self.state.control_url + "/execute",
                data=raw_body,
                method="POST",
                headers={
                    "Content-Type": "application/json",
                    TRACE_ID_HEADER: trace_id,
                    PARENT_SPAN_HEADER: span_id,
                },
            )
            try:
                with urlopen(request, timeout=self.state.timeout_s) as response:
                    body = response.read()
                    attributes["status_code"] = response.status
                    self._send_raw(response.status, body)
            except HTTPError as exc:
                body = exc.read()
                attributes["status_code"] = exc.code
                self._send_raw(exc.code, body)
            except (URLError, TimeoutError) as exc:
                attributes["status_code"] = 502
                attributes["upstream_error"] = type(exc).__name__
                self._send_json(
                    502,
                    {"error": "upstream_unavailable", "message": str(exc)},
                )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _read_body(self) -> bytes:
        content_length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(content_length) if content_length > 0 else b"{}"

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        self._send_raw(status_code, json.dumps(payload).encode("utf-8"))

    def _send_raw(self, status_code: int, encoded: bytes) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--control-url", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--trace-path")
    parser.add_argument("--timeout-s", type=float, default=10.0)
    args = parser.parse_args()

    profile = load_network_profile(args.profile)
    GatewayHandler.state = GatewayState(
        control_url=args.control_url,
        profile=profile,
        trace_path=args.trace_path,
        timeout_s=args.timeout_s,
    )
    server = ThreadingHTTPServer((args.host, args.port), GatewayHandler)
    print(f"Gateway listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
