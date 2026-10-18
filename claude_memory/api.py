"""
Graph memory as a local HTTP API (stdlib only). Claude Code calls it with curl through the Bash tool.

    venv/bin/python claude_memory/api.py          # serves http://127.0.0.1:8765

    GET  /                       usage
    GET  /recall?query=&hops=2   facts (empty query = everything)
    POST /remember  {"subject", "relation", "object"}
    POST /forget    {"subject", "relation", "object"}

Binds to 127.0.0.1 only and has no auth: local development use.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from _common import open_memory

memory = open_memory()
USAGE = __doc__.strip()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, text: str) -> None:
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/":
            return self._send(200, USAGE)
        if url.path == "/recall":
            qs = parse_qs(url.query)
            return self._send(200, memory.recall(qs.get("query", [""])[0], hops=int(qs.get("hops", ["2"])[0])))
        self._send(404, "not found, see GET /")

    def do_POST(self):
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            args = (data["subject"], data["relation"], data["object"])
        except (ValueError, KeyError) as e:
            return self._send(400, f"bad request: need JSON with subject, relation, object ({e})")
        if self.path == "/remember":
            return self._send(200, memory.remember(*args))
        if self.path == "/forget":
            return self._send(200, memory.forget(*args))
        self._send(404, "not found, see GET /")

    def log_message(self, fmt, *args):  # keep stdout clean
        pass


if __name__ == "__main__":
    port = int(os.getenv("MEMORY_API_PORT", "8765"))
    print(f"graph memory API on http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
