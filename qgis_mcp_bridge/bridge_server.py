"""HTTP bridge that executes PyQGIS code on the QGIS main thread.

Architecture
------------
An external MCP server speaks JSON-over-HTTP to this bridge. HTTP requests are
served on background threads, but anything touching the QGIS GUI / API MUST run
on the Qt main thread. So every request is marshalled onto the main thread via a
``QTimer``-drained queue (the ``MainThreadDispatcher``) and the serving thread
blocks until the result is ready.

No third-party dependencies: only stdlib + PyQt + qgis, all present inside QGIS.
"""

import base64
import io
import json
import os
import queue
import secrets
import threading
import traceback
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qgis.PyQt.QtCore import QObject, QTimer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
# Optional shared secret. When the QGIS_MCP_TOKEN env var is set, every request
# must carry a matching X-QGIS-MCP-Token header. Unset = open (localhost only).
TOKEN_ENV = "QGIS_MCP_TOKEN"


# --------------------------------------------------------------------------- #
# Main-thread marshalling
# --------------------------------------------------------------------------- #
class MainThreadDispatcher(QObject):
    """Runs callables on the Qt main thread and returns their result.

    Must be constructed on the main thread (its QTimer lives there). Background
    threads call :meth:`submit`, which blocks until the main thread runs the job.
    """

    def __init__(self, interval_ms: int = 15):
        super().__init__()
        self._queue: "queue.Queue" = queue.Queue()
        self._timer = QTimer()
        self._timer.timeout.connect(self._drain)
        self._timer.start(interval_ms)

    def submit(self, fn, timeout: float = 120.0):
        evt = threading.Event()
        box = {}
        self._queue.put((fn, box, evt))
        if not evt.wait(timeout):
            return {"ok": False, "error": "timeout waiting for QGIS main thread"}
        return box["result"]

    def _drain(self):
        while True:
            try:
                fn, box, evt = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                box["result"] = fn()
            except Exception as exc:  # pragma: no cover - defensive
                box["result"] = {
                    "ok": False,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            finally:
                evt.set()

    def stop(self):
        self._timer.stop()


# --------------------------------------------------------------------------- #
# PyQGIS execution (runs on main thread)
# --------------------------------------------------------------------------- #
def _build_namespace():
    """Build the execution namespace exposed to submitted code."""
    import qgis
    import qgis.core as qcore
    from qgis.utils import iface

    ns = {"__name__": "__qgis_mcp__", "qgis": qgis, "iface": iface}
    # Expose every Qgs* class without a wall of explicit imports.
    for name in dir(qcore):
        if name.startswith("Qgs"):
            ns[name] = getattr(qcore, name)
    try:
        import processing

        ns["processing"] = processing
    except Exception:
        pass
    return ns


def exec_code(code: str) -> dict:
    """Execute *code* and capture stdout + a result value.

    A single expression is ``eval``'d and its value returned; a statement block
    is ``exec``'d and a ``result`` variable, if set, is returned. Stdout (e.g.
    ``print``) is always captured.
    """
    ns = _build_namespace()
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            try:
                value = eval(compile(code, "<qgis-mcp>", "eval"), ns)
                result_repr = repr(value)
            except SyntaxError:
                exec(compile(code, "<qgis-mcp>", "exec"), ns)
                result_repr = repr(ns["result"]) if "result" in ns else None
        return {
            "ok": True,
            "stdout": buf.getvalue(),
            "result": result_repr,
            "error": None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "stdout": buf.getvalue(),
            "result": None,
            "error": str(exc),
            "traceback": traceback.format_exc(),
        }


def render_canvas(width: int = 0, height: int = 0) -> dict:
    """Grab the current map canvas as a base64 PNG.

    Uses a temp file for the PNG encode to sidestep QBuffer/QIODevice enum
    differences between Qt5 and Qt6.
    """
    import os
    import tempfile

    from qgis.PyQt.QtCore import Qt
    from qgis.utils import iface

    # Qt6 nests these enums; Qt5 exposes them flat. Support both.
    try:
        keep = Qt.AspectRatioMode.KeepAspectRatio
        smooth = Qt.TransformationMode.SmoothTransformation
    except AttributeError:
        keep = Qt.KeepAspectRatio
        smooth = Qt.SmoothTransformation

    canvas = iface.mapCanvas()
    pix = canvas.grab()
    if width and height:
        pix = pix.scaled(int(width), int(height), keep, smooth)

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        pix.save(path, "PNG")
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return {
        "ok": True,
        "width": pix.width(),
        "height": pix.height(),
        "image_base64": base64.b64encode(data).decode("ascii"),
    }


def qgis_info() -> dict:
    from qgis.core import Qgis

    return {"ok": True, "qgis_version": Qgis.QGIS_VERSION}


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
def _make_handler(dispatcher: MainThreadDispatcher):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # silence console spam
            pass

        def _send(self, payload: dict, status: int = 200):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _authorized(self) -> bool:
            """Reject browser-originated and unauthenticated requests.

            This bridge executes arbitrary code, so we harden the localhost
            surface against drive-by attacks: (1) any request carrying an
            Origin or Referer header is refused — a legitimate MCP client does
            not send those, but a malicious web page (incl. DNS-rebinding)
            would; (2) when QGIS_MCP_TOKEN is set, an exact-match
            X-QGIS-MCP-Token header is required.
            """
            if self.headers.get("Origin") or self.headers.get("Referer"):
                self._send({"ok": False, "error": "forbidden: cross-origin"}, 403)
                return False
            token = os.environ.get(TOKEN_ENV, "").strip()
            if token:
                provided = (self.headers.get("X-QGIS-MCP-Token") or "").strip()
                if not secrets.compare_digest(
                    provided.encode("utf-8"), token.encode("utf-8")
                ):
                    self._send({"ok": False, "error": "forbidden: bad token"}, 403)
                    return False
            return True

        def do_GET(self):
            if not self._authorized():
                return
            if self.path == "/ping":
                self._send(dispatcher.submit(qgis_info))
            else:
                self._send({"ok": False, "error": "not found"}, 404)

        def do_POST(self):
            if not self._authorized():
                return
            try:
                data = self._read_json()
            except Exception as exc:
                self._send({"ok": False, "error": f"bad json: {exc}"}, 400)
                return

            if self.path == "/execute":
                code = data.get("code", "")
                self._send(dispatcher.submit(lambda: exec_code(code)))
            elif self.path == "/render":
                w = data.get("width", 0)
                h = data.get("height", 0)
                self._send(dispatcher.submit(lambda: render_canvas(w, h)))
            else:
                self._send({"ok": False, "error": "not found"}, 404)

    return Handler


class BridgeServer:
    """Owns the dispatcher + threaded HTTP server. Construct on the main thread."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._dispatcher = MainThreadDispatcher()
        self._httpd = ThreadingHTTPServer(
            (host, port), _make_handler(self._dispatcher)
        )
        self._thread = None

    def start(self):
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="qgis-mcp-bridge", daemon=True
        )
        self._thread.start()

    def stop(self):
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._dispatcher.stop()
