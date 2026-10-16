"""Same-origin server for listen_check.html: the page, the vendored hls.js, the real player file,
and /hls/* forwarded to the media server (so the page sees what Caddy would serve, without CORS).
An https upstream is reached without certificate verification (it is for the internal-CA stack).

    python listen_check_server.py <upstream> [port]
    e.g. python listen_check_server.py https://10.110.11.31:8444 8200
"""

import pathlib
import ssl
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
ASSETS = HERE.parents[2] / "clients" / "elder-app" / "assets"
UP = sys.argv[1].rstrip("/")
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8200
CTX = ssl._create_unverified_context() if UP.startswith("https") else None
FILES = {
    "/listen_check.html": (HERE / "listen_check.html", "text/html"),
    "/vendor/hls.min.js": (ASSETS / "vendor" / "hls.min.js", "text/javascript"),
    "/satsang_player.js": (ASSETS / "satsang_player.js", "text/javascript"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in FILES:
            file, ctype = FILES[path]
            data, code = file.read_bytes(), 200
        elif path.startswith("/hls/"):
            try:
                reply = urllib.request.urlopen(UP + path, timeout=10, context=CTX)
                data, code = reply.read(), reply.status
                ctype = reply.headers.get("Content-Type", "application/octet-stream")
            except urllib.error.HTTPError as err:
                data, code, ctype = err.read(), err.code, "text/plain"
            except Exception as err:  # noqa: BLE001 - a dead upstream is reported to the page as 502
                data, code, ctype = str(err).encode(), 502, "text/plain"
            print(time.strftime("%H:%M:%S"), code, path[-44:], f"{len(data)}B", flush=True)
        else:
            data, code, ctype = b"not found", 404, "text/plain"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
