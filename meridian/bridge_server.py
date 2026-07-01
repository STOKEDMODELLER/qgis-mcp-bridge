"""Meridian (Lite) — safe command bridge for QGIS.

A localhost HTTP server runs inside QGIS and dispatches a FIXED set of vetted
commands to PyQGIS on the Qt main thread. Unlike the full "developer edition"
bridge, this build contains NO eval/exec/compile — every action maps to a
specific, reviewed handler. That makes it suitable for the official QGIS plugin
repository's security scan.

No third-party dependencies: only stdlib + PyQt + qgis, all present inside QGIS.
"""

import base64
import json
import os
import queue
import secrets
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from qgis.PyQt.QtCore import QObject, QTimer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9876
AUTH_ENV = "QGIS_MCP_TOKEN"


# --------------------------------------------------------------------------- #
# Main-thread marshalling
# --------------------------------------------------------------------------- #
class MainThreadDispatcher(QObject):
    """Runs callables on the Qt main thread and returns their result."""

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
# Helpers (run on main thread)
# --------------------------------------------------------------------------- #
def _project():
    from qgis.core import QgsProject

    return QgsProject.instance()


def _iface():
    from qgis.utils import iface

    return iface


def _get_layer(params):
    lid = params.get("layer_id")
    layer = _project().mapLayer(lid)
    if layer is None:
        raise ValueError(f"no layer with id {lid!r}")
    return layer


def _jsonable(value):
    if isinstance(value, (int, float, str, bool, type(None))):
        return value
    return str(value)


def _layer_type(layer):
    return layer.__class__.__name__


# --------------------------------------------------------------------------- #
# Command handlers — each is a specific, reviewed operation (no code exec)
# --------------------------------------------------------------------------- #
def cmd_ping(params):
    from qgis.core import Qgis

    return {"ok": True, "qgis_version": Qgis.QGIS_VERSION}


def cmd_project_info(params):
    p = _project()
    canvas = _iface().mapCanvas()
    e = canvas.extent()
    return {
        "ok": True,
        "project_file": p.fileName(),
        "title": p.title(),
        "crs": p.crs().authid(),
        "layer_count": len(p.mapLayers()),
        "extent": [e.xMinimum(), e.yMinimum(), e.xMaximum(), e.yMaximum()],
        "scale": canvas.scale(),
    }


def cmd_list_layers(params):
    layers = []
    for lid, lyr in _project().mapLayers().items():
        layers.append(
            {
                "id": lid,
                "name": lyr.name(),
                "type": _layer_type(lyr),
                "crs": lyr.crs().authid(),
                "source": lyr.source(),
                "feature_count": (
                    lyr.featureCount() if hasattr(lyr, "featureCount") else None
                ),
            }
        )
    return {"ok": True, "layers": layers}


def cmd_layer_info(params):
    lyr = _get_layer(params)
    info = {
        "ok": True,
        "id": lyr.id(),
        "name": lyr.name(),
        "type": _layer_type(lyr),
        "crs": lyr.crs().authid(),
        "source": lyr.source(),
    }
    if hasattr(lyr, "fields"):
        info["fields"] = [f.name() for f in lyr.fields()]
    if hasattr(lyr, "featureCount"):
        info["feature_count"] = lyr.featureCount()
    return info


def cmd_add_vector_layer(params):
    from qgis.core import QgsVectorLayer

    path = params["path"]
    name = params.get("name") or path
    provider = params.get("provider", "ogr")
    layer = QgsVectorLayer(path, name, provider)
    if not layer.isValid():
        return {"ok": False, "error": f"invalid vector layer: {path}"}
    _project().addMapLayer(layer)
    return {"ok": True, "id": layer.id(), "name": layer.name()}


def cmd_add_raster_layer(params):
    from qgis.core import QgsRasterLayer

    path = params["path"]
    name = params.get("name") or path
    layer = QgsRasterLayer(path, name)
    if not layer.isValid():
        return {"ok": False, "error": f"invalid raster layer: {path}"}
    _project().addMapLayer(layer)
    return {"ok": True, "id": layer.id(), "name": layer.name()}


def cmd_remove_layer(params):
    lyr = _get_layer(params)
    _project().removeMapLayer(lyr.id())
    return {"ok": True, "removed": params.get("layer_id")}


def cmd_set_active_layer(params):
    lyr = _get_layer(params)
    _iface().setActiveLayer(lyr)
    return {"ok": True, "active": lyr.id()}


def cmd_set_layer_visibility(params):
    lyr = _get_layer(params)
    visible = bool(params.get("visible", True))
    node = _project().layerTreeRoot().findLayer(lyr.id())
    if node is None:
        return {"ok": False, "error": "layer not in layer tree"}
    node.setItemVisibilityChecked(visible)
    return {"ok": True, "id": lyr.id(), "visible": visible}


def cmd_zoom_to_layer(params):
    lyr = _get_layer(params)
    iface = _iface()
    iface.setActiveLayer(lyr)
    iface.zoomToActiveLayer()
    return {"ok": True, "zoomed_to": lyr.id()}


def cmd_get_features(params):
    lyr = _get_layer(params)
    limit = int(params.get("limit", 20))
    include_geom = bool(params.get("include_geometry", False))
    field_names = [f.name() for f in lyr.fields()]
    out = []
    for i, feat in enumerate(lyr.getFeatures()):
        if i >= limit:
            break
        row = {"id": feat.id(), "attributes": {n: _jsonable(feat[n]) for n in field_names}}
        if include_geom and feat.hasGeometry():
            row["geometry_wkt"] = feat.geometry().asWkt(precision=6)
        out.append(row)
    return {"ok": True, "count": len(out), "features": out}


def cmd_set_project_crs(params):
    from qgis.core import QgsCoordinateReferenceSystem

    authid = params["authid"]
    crs = QgsCoordinateReferenceSystem(authid)
    if not crs.isValid():
        return {"ok": False, "error": f"invalid CRS: {authid}"}
    _project().setCrs(crs)
    return {"ok": True, "crs": crs.authid()}


def cmd_save_project(params):
    path = params.get("path")
    ok = _project().write(path) if path else _project().write()
    return {"ok": bool(ok), "path": path or _project().fileName()}


def cmd_load_project(params):
    path = params["path"]
    ok = _project().read(path)
    return {"ok": bool(ok), "path": path}


def cmd_list_algorithms(params):
    from qgis.core import QgsApplication

    flt = (params.get("filter") or "").lower()
    algs = []
    for a in QgsApplication.processingRegistry().algorithms():
        if not flt or flt in a.id().lower() or flt in a.displayName().lower():
            algs.append({"id": a.id(), "name": a.displayName()})
    algs.sort(key=lambda x: x["id"])
    return {"ok": True, "count": len(algs), "algorithms": algs}


def cmd_run_algorithm(params):
    """Run a NAMED Processing algorithm with a parameter dict.

    This executes vetted QGIS Processing algorithms only — not arbitrary code.
    """
    import processing

    alg_id = params["algorithm_id"]
    parameters = params.get("parameters", {})
    result = processing.run(alg_id, parameters)
    return {"ok": True, "result": {k: _jsonable(v) for k, v in result.items()}}


def cmd_render_map(params):
    from qgis.PyQt.QtCore import Qt

    try:
        keep = Qt.AspectRatioMode.KeepAspectRatio
        smooth = Qt.TransformationMode.SmoothTransformation
    except AttributeError:
        keep = Qt.KeepAspectRatio
        smooth = Qt.SmoothTransformation

    import os as _os
    import tempfile

    width = int(params.get("width", 0))
    height = int(params.get("height", 0))
    pix = _iface().mapCanvas().grab()
    if width and height:
        pix = pix.scaled(width, height, keep, smooth)

    fd, path = tempfile.mkstemp(suffix=".png")
    _os.close(fd)
    try:
        pix.save(path, "PNG")
        with open(path, "rb") as fh:
            data = fh.read()
    finally:
        try:
            _os.remove(path)
        except OSError:
            pass
    return {
        "ok": True,
        "width": pix.width(),
        "height": pix.height(),
        "image_base64": base64.b64encode(data).decode("ascii"),
    }


COMMANDS = {
    "ping": cmd_ping,
    "project_info": cmd_project_info,
    "list_layers": cmd_list_layers,
    "layer_info": cmd_layer_info,
    "add_vector_layer": cmd_add_vector_layer,
    "add_raster_layer": cmd_add_raster_layer,
    "remove_layer": cmd_remove_layer,
    "set_active_layer": cmd_set_active_layer,
    "set_layer_visibility": cmd_set_layer_visibility,
    "zoom_to_layer": cmd_zoom_to_layer,
    "get_features": cmd_get_features,
    "set_project_crs": cmd_set_project_crs,
    "save_project": cmd_save_project,
    "load_project": cmd_load_project,
    "list_algorithms": cmd_list_algorithms,
    "run_algorithm": cmd_run_algorithm,
    "render_map": cmd_render_map,
}


def handle_command(action, params):
    fn = COMMANDS.get(action)
    if fn is None:
        return {"ok": False, "error": f"unknown action: {action!r}"}
    try:
        return fn(params or {})
    except KeyError as exc:
        return {"ok": False, "error": f"missing parameter: {exc}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "traceback": traceback.format_exc()}


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
def _make_handler(dispatcher: MainThreadDispatcher):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, payload: dict, status: int = 200):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if self.headers.get("Origin") or self.headers.get("Referer"):
                self._send({"ok": False, "error": "forbidden: cross-origin"}, 403)
                return False
            token = os.environ.get(AUTH_ENV, "").strip()
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
                self._send(dispatcher.submit(lambda: cmd_ping({})))
            else:
                self._send({"ok": False, "error": "not found"}, 404)

        def do_POST(self):
            if not self._authorized():
                return
            if self.path != "/command":
                self._send({"ok": False, "error": "not found"}, 404)
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            except Exception as exc:
                self._send({"ok": False, "error": f"bad json: {exc}"}, 400)
                return
            action = data.get("action", "")
            params = data.get("params", {})
            self._send(dispatcher.submit(lambda: handle_command(action, params)))

    return Handler


class BridgeServer:
    """Owns the dispatcher + threaded HTTP server. Construct on the main thread."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self._dispatcher = MainThreadDispatcher()
        self._httpd = ThreadingHTTPServer((host, port), _make_handler(self._dispatcher))
        self._thread = None

    def start(self):
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="meridian-bridge", daemon=True
        )
        self._thread.start()

    def stop(self):
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        finally:
            self._dispatcher.stop()
