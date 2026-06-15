#!/usr/bin/env python3
"""Minimal scrape sidecar stub — serves cached JSON for FVE scrape-cache feed.

NOT a production scraper. Wire your own collector to write the same JSON shape.

  python3 scripts/fve_scrape_sidecar_stub.py &
  export FVE_SCRAPE_LINES_URL=http://127.0.0.1:8091/lines/{fixture_key}
  export FVE_FEED_MODE=separate
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

_SAMPLE = {
    "best_odds_1x2": {"home": 2.05, "draw": 3.5, "away": 3.4},
    "best_odds_source": {"home": "stub", "draw": "stub", "away": "stub"},
    "scrape_source": "fve_scrape_sidecar_stub",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if not self.path.startswith("/lines/"):
            self.send_response(404)
            self.end_headers()
            return
        key = unquote(self.path.split("/lines/", 1)[-1].split("?", 1)[0])
        body = json.dumps({**_SAMPLE, "fixture_key": key}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


def main() -> None:
    port = 8091
    server = HTTPServer(("127.0.0.1", port), Handler)
    print(f"scrape sidecar stub http://127.0.0.1:{port}/lines/<fixture_key>")
    server.serve_forever()


if __name__ == "__main__":
    main()
