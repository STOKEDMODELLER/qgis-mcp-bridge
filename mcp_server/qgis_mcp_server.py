"""MCP server exposing the running QGIS instance to Claude.

This process speaks the Model Context Protocol to Claude (stdio transport) and
forwards every tool call as JSON-over-HTTP to the QGIS MCP Bridge plugin, which
runs the work on the QGIS main thread.

It imports NO PyQGIS — it only needs `mcp` and the standard library. Run it with:

    uvx --from mcp[cli] --with-editable . python qgis_mcp_server.py
    # or simply, after `pip install mcp`:
    python qgis_mcp_server.py
"""

import base64
import json
import os
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP, Image

BRIDGE = os.environ.get("QGIS_MCP_BRIDGE", "http://127.0.0.1:9876")
TIMEOUT = float(os.environ.get("QGIS_MCP_TIMEOUT", "120"))

mcp = FastMCP("qgis")


def _request(method: str, path: str, payload: dict | None = None) -> dict:
    url = BRIDGE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "error": f"Cannot reach QGIS bridge at {BRIDGE}: {exc}. "
            "Is QGIS open with the MCP Bridge plugin enabled?",
        }


@mcp.tool()
def ping() -> str:
    """Check that QGIS is running and the bridge is reachable. Returns the QGIS version."""
    return json.dumps(_request("GET", "/ping"), indent=2)


@mcp.tool()
def run_pyqgis(code: str) -> str:
    """Execute arbitrary PyQGIS code inside the running QGIS process.

    The code runs on the QGIS main thread with these names already available:
    `iface` (QgisInterface), `qgis`, `processing`, and every `Qgs*` class from
    qgis.core (e.g. QgsProject, QgsVectorLayer, QgsRasterLayer, QgsPointXY).

    Returning a value: write a single expression (its value is returned), OR set
    a variable named `result` in a multi-line block. `print(...)` output is
    captured separately as stdout.

    Examples:
        QgsProject.instance().mapLayers().keys()          # single expression
        layer = QgsVectorLayer(path, "roads", "ogr"); QgsProject.instance().addMapLayer(layer); result = layer.isValid()

    Returns a JSON string: {ok, stdout, result, error, traceback}.
    """
    return json.dumps(_request("POST", "/execute", {"code": code}), indent=2)


@mcp.tool()
def render_map(width: int = 0, height: int = 0) -> Image:
    """Render the current QGIS map canvas to a PNG image so you can see it.

    Pass width and height to scale (keeping aspect ratio); 0,0 = native size.
    Call this after changing layers, styling, or the map extent to verify results.
    """
    resp = _request("POST", "/render", {"width": width, "height": height})
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "render failed"))
    return Image(data=base64.b64decode(resp["image_base64"]), format="png")


@mcp.tool()
def list_layers() -> str:
    """List all layers currently loaded in the QGIS project (id, name, type, source)."""
    code = (
        "result = [\n"
        "    {\n"
        "        'id': lid,\n"
        "        'name': lyr.name(),\n"
        "        'type': lyr.__class__.__name__,\n"
        "        'crs': lyr.crs().authid(),\n"
        "        'source': lyr.source(),\n"
        "        'feature_count': (lyr.featureCount() if hasattr(lyr, 'featureCount') else None),\n"
        "    }\n"
        "    for lid, lyr in QgsProject.instance().mapLayers().items()\n"
        "]"
    )
    return json.dumps(_request("POST", "/execute", {"code": code}), indent=2)


@mcp.tool()
def project_info() -> str:
    """Summarize the active QGIS project: file path, CRS, layer count, map extent."""
    code = (
        "p = QgsProject.instance()\n"
        "c = iface.mapCanvas()\n"
        "e = c.extent()\n"
        "result = {\n"
        "    'project_file': p.fileName(),\n"
        "    'title': p.title(),\n"
        "    'crs': p.crs().authid(),\n"
        "    'layer_count': len(p.mapLayers()),\n"
        "    'extent': [e.xMinimum(), e.yMinimum(), e.xMaximum(), e.yMaximum()],\n"
        "    'scale': c.scale(),\n"
        "}"
    )
    return json.dumps(_request("POST", "/execute", {"code": code}), indent=2)


@mcp.tool()
def list_algorithms(filter: str = "") -> str:
    """List available Processing algorithm ids (optionally filtered by substring).

    Use the returned ids with processing.run(id, params) via run_pyqgis.
    """
    code = (
        "from qgis.core import QgsApplication\n"
        f"flt = {filter!r}.lower()\n"
        "result = sorted(\n"
        "    a.id() for a in QgsApplication.processingRegistry().algorithms()\n"
        "    if not flt or flt in a.id().lower() or flt in a.displayName().lower()\n"
        ")"
    )
    return json.dumps(_request("POST", "/execute", {"code": code}), indent=2)


if __name__ == "__main__":
    mcp.run()
