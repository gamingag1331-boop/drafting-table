"""Local API for the Drafting Table app.

The app starts this server on 127.0.0.1 only. Every request needs the token from
~/.drafting_table_token. Changes run on the app's main thread, so they appear
on the board immediately and can be undone with Ctrl+Z.

Routes:
    GET  /health
    GET  /board                  list everything on the board (needs token)
    POST /items                  add items: {"items": [...]}
    POST /items/delete           remove items: {"ids": [...]}   (pinned items are skipped)
"""
import json
import os
import queue
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8765
TOKEN_FILE = os.path.join(os.path.expanduser("~"), ".drafting_table_token")


def get_token():
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            token = f.read().strip()
            if token:
                return token
    except OSError:
        pass
    token = secrets.token_urlsafe(24)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return token


def make_handler(bridge, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DraftingTable"

        def log_message(self, fmt, *args):
            pass

        def send_json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

        def read_json(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise ValueError("request too large")
            return json.loads(self.rfile.read(n) or b"{}")

        def run_on_board(self, fn):
            try:
                self.send_json(200, bridge.call(fn))
            except ValueError as err:
                self.send_json(400, {"error": str(err)})
            except TimeoutError as err:
                self.send_json(503, {"error": str(err)})
            except Exception as err:
                self.send_json(500, {"error": f"internal error: {err}"})

        def do_GET(self):
            if self.path == "/health":
                return self.send_json(200, {"ok": True})
            if not self.authorized():
                return self.send_json(401, {"error": "missing or wrong token"})
            if self.path == "/board":
                return self.run_on_board(bridge.app.api_board)
            self.send_json(404, {"error": "unknown route"})

        def do_POST(self):
            if not self.authorized():
                return self.send_json(401, {"error": "missing or wrong token"})
            try:
                data = self.read_json()
            except ValueError:
                return self.send_json(400, {"error": "body must be JSON"})
            if self.path == "/items":
                specs = data.get("items") if isinstance(data, dict) else data
                return self.run_on_board(lambda: bridge.app.api_add(specs))
            if self.path == "/items/delete":
                ids = data.get("ids") if isinstance(data, dict) else None
                return self.run_on_board(lambda: bridge.app.api_delete(ids))
            self.send_json(404, {"error": "unknown route"})

    return Handler


class Bridge:
    def __init__(self, app, root):
        self.app = app
        self.root = root
        self.jobs = queue.Queue()

    def pump(self):
        try:
            while True:
                fn, box, done = self.jobs.get_nowait()
                try:
                    box["result"] = fn()
                except Exception as err:
                    box["exc"] = err
                done.set()
        except queue.Empty:
            pass
        self.root.after(50, self.pump)

    def call(self, fn):
        box = {}
        done = threading.Event()
        self.jobs.put((fn, box, done))
        if not done.wait(5):
            raise TimeoutError("The board did not respond in time.")
        if "exc" in box:
            raise box["exc"]
        return box["result"]

    def start(self):
        token = get_token()
        server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(self, token))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.root.after(50, self.pump)
        return server
