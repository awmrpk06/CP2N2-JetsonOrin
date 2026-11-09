"""Serve the BioPattern Gate E3 replay dashboard on localhost."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import webbrowser


ROOT = Path(__file__).parents[1].resolve()
WEB_ROOT = (
    ROOT / "cl-apps" / "cp2n2-biopattern-gate" / "web"
).resolve()
DEMO_BUNDLE = (
    ROOT
    / "evaluation"
    / "fixtures"
    / "biopattern-gate-demo-success-v1.json"
).resolve()


def build_demo_page(fragment: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>CP2N2 BioPattern Gate · E3 Replay</title>
  <link rel="stylesheet" href="/assets/vis.css">
</head>
<body>
{fragment}
<script type="module">
  import {{ mountReplayDemo }} from "/assets/vis.mjs";
  const root = document.querySelector("[data-pattern-gate-root]");
  mountReplayDemo(root, "/data/demo.json");
</script>
</body>
</html>
"""


class DemoRequestHandler(BaseHTTPRequestHandler):
    server_version = "CP2N2Demo/1.0"

    def do_GET(self) -> None:  # noqa: N802
        routes = {
            "/assets/vis.css": (
                WEB_ROOT / "vis.css",
                "text/css; charset=utf-8",
            ),
            "/assets/vis.mjs": (
                WEB_ROOT / "vis.mjs",
                "text/javascript; charset=utf-8",
            ),
            "/data/demo.json": (
                DEMO_BUNDLE,
                "application/json; charset=utf-8",
            ),
        }
        if self.path in {"/", "/index.html"}:
            payload = build_demo_page(
                (WEB_ROOT / "vis.html").read_text(encoding="utf-8")
            ).encode("utf-8")
            self._respond(200, "text/html; charset=utf-8", payload)
            return
        if self.path == "/health":
            self._respond(200, "text/plain; charset=utf-8", b"ready\n")
            return
        route = routes.get(self.path)
        if route is None:
            self._respond(404, "text/plain; charset=utf-8", b"not found\n")
            return
        path, content_type = route
        self._respond(200, content_type, path.read_bytes())

    def log_message(self, format: str, *args: object) -> None:
        print(f"[demo] {format % args}")

    def _respond(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    if not DEMO_BUNDLE.exists():
        print(
            "Demo bundle is missing. Run "
            "'python scripts/export_biopattern_gate_demo_bundle.py'.",
            file=sys.stderr,
        )
        return 1
    server = ThreadingHTTPServer(
        (args.host, args.port),
        DemoRequestHandler,
    )
    url = f"http://{args.host}:{server.server_port}/"
    print(f"BioPattern Gate E3 replay: {url}")
    print("Press Ctrl+C to stop.")
    if not args.no_open:
        threading.Timer(0.25, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping demo.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
