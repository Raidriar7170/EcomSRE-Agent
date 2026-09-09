"""Private fixed-document transport for the existing Product control gateway.

Runs only on the control network. It accepts the two pre-bound documents, not
paths, commands or arbitrary flag values. Product API/Worker have no route here.
"""

from datetime import UTC, datetime
import hashlib
import time
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = Path("/control")


def main() -> None:
    baseline = json.loads((ROOT / "baseline.json").read_bytes())
    fault = json.loads((ROOT / "fault.json").read_bytes())
    path = ROOT / "flags" / "demo.flagd.json"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            if self.path != "/read":
                self.send_error(404)
                return
            body = json.dumps(
                {"flags": json.loads(path.read_bytes())["flags"]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/write":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length < 100000:
                self.send_error(400)
                return
            value = json.loads(self.rfile.read(length))
            if set(value) != {"data"} or value["data"] not in (baseline, fault):
                self.send_error(403)
                return
            if path.is_symlink():
                self.send_error(409)
                return

            def audit(stage):
                row = {
                    "stage": stage,
                    "utc": datetime.now(UTC).isoformat(),
                    "monotonic_ns": time.monotonic_ns(),
                    "clock_scope": "flag-transport-process",
                    "peer": self.client_address[0],
                    "controller": self.headers.get("X-EcomSRE-Controller"),
                    "document": "baseline" if value["data"] == baseline else "fault",
                    "document_sha256": hashlib.sha256(
                        json.dumps(
                            value["data"], sort_keys=True, separators=(",", ":")
                        ).encode()
                    ).hexdigest(),
                }
                with (ROOT / "write-audit.jsonl").open("a") as log:
                    log.write(json.dumps(row, sort_keys=True) + "\n")
                    log.flush()
                    os.fsync(log.fileno())

            audit("INTENT")
            temporary = path.with_suffix(".next")
            with temporary.open("x") as stream:
                json.dump(value["data"], stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            audit("APPLIED")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    HTTPServer(("0.0.0.0", 8080), Handler).serve_forever()


if __name__ == "__main__":
    main()
