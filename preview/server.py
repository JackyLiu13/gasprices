#!/usr/bin/env python3
"""Local server for the panel preview and layout editor.

    python3 preview/server.py            # then open the URL it prints

Serves the repository root, so the page can read tests/vectors.csv and
firmware/gasprices/layout.json straight off disk with no build step and no copies
to keep in sync. Adds exactly two things on top of static files:

    PUT  /firmware/gasprices/layout.json   save the dragged layout
    POST /generate                         run gen_layout.py to refresh layout.h

Standard library only, matching the backend's no-dependency rule, and bound to
localhost: it writes to your working tree and runs a generator, so it has no
business listening on the network.
"""

import argparse
import http.server
import json
import pathlib
import subprocess
import sys
from urllib.parse import urlparse

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAYOUT = ROOT / "firmware/gasprices/layout.json"
LAYOUT_URL = "/firmware/gasprices/layout.json"
GENERATOR = ROOT / "preview/tools/gen_layout.py"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        # An editor that serves you a cached render of the layout you just
        # changed is worse than no editor.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # --- helpers ---

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local_only(self):
        """Reject cross-origin writes.

        The only client is the page this server itself served. Anything else
        reaching a write endpoint means a browser somewhere was talked into
        posting at localhost, so refuse rather than edit the working tree.
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        host = urlparse(origin).hostname
        if host in ("localhost", "127.0.0.1", "::1"):
            return True
        self._json(403, {"error": f"cross-origin request from {origin} refused"})
        return False

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return None
        return self.rfile.read(n)

    # --- writes ---

    def do_PUT(self):
        if not self._local_only():
            return
        if urlparse(self.path).path != LAYOUT_URL:
            self._json(404, {"error": f"only {LAYOUT_URL} is writable"})
            return

        raw = self._body()
        if not raw:
            self._json(400, {"error": "empty body"})
            return
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            self._json(400, {"error": f"not valid JSON: {e}"})
            return
        for key in ("panel", "elements"):
            if key not in parsed:
                self._json(400, {"error": f'no "{key}" key — refusing to write'})
                return

        LAYOUT.write_text(json.dumps(parsed, indent=2) + "\n")
        self._json(200, {"ok": True, "wrote": LAYOUT_URL,
                         "elements": len(parsed["elements"])})

    def do_POST(self):
        if not self._local_only():
            return
        if urlparse(self.path).path != "/generate":
            self._json(404, {"error": "only POST /generate"})
            return

        r = subprocess.run([sys.executable, str(GENERATOR)],
                           cwd=str(ROOT), capture_output=True, text=True)
        self._json(200 if r.returncode == 0 else 500, {
            "ok": r.returncode == 0,
            "output": (r.stdout + r.stderr).strip(),
        })

    def log_message(self, fmt, *args):
        # One line per write; static GETs are just noise while dragging.
        if self.command in ("PUT", "POST"):
            sys.stderr.write(f"{self.command} {self.path} — {fmt % args}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()

    if not LAYOUT.is_file():
        sys.exit(f"missing {LAYOUT.relative_to(ROOT)}")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"serving {ROOT}")
    print(f"open http://127.0.0.1:{args.port}/preview/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
