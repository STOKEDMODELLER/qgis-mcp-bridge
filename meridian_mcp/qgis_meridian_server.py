"""MCP server for Meridian (Lite) — the curated, no-exec QGIS bridge.

Speaks MCP to the AI client and forwards each tool call as JSON-over-HTTP to the
Meridian plugin's /command endpoint. Imports no PyQGIS. Run with:

    uv run --with 'mcp[cli]' python qgis_meridian_server.py
"""

import base64
import json
import os
import urllib.error
import urllib.request

from mcp.server.fastmcp import FastMCP, Image

BRIDGE = os.environ.get("QGIS_MCP_BRIDGE", "http://127.0.0.1:9876")
TIMEOUT = float(os.environ.get("QGIS_MCP_TIMEOUT", "120"))

mcp = FastMCP("meridian")


def _command(action: str, params: dict | None = None) -> dict:
    payload = json.dumps({"action": action, "params": params or {}}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("QGIS_MCP_TOKEN", "").strip()
    if token:
        headers["X-QGIS-MCP-Token"] = token
    req = urllib.request.Request(
        BRIDGE + "/command", data=payload, method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        return {
            "ok": False,
            "error": f"Cannot reach Meridian bridge at {BRIDGE}: {exc}. "
            "Is QGIS open with the Meridian plugin enabled?",
        }


def _json(action, params=None) -> str:
    return json.dumps(_command(action, params), indent=2)


@mcp.tool()
def ping() -> str:
    """Check QGIS is running and reachable; returns the QGIS version."""
    return _json("ping")


@mcp.tool()
def project_info() -> str:
    """Active project: file, CRS, layer count, extent, scale."""
    return _json("project_info")


@mcp.tool()
def list_layers() -> str:
    """List loaded layers (id, name, type, CRS, source, feature count)."""
    return _json("list_layers")


@mcp.tool()
def layer_info(layer_id: str) -> str:
    """Details for one layer, including field names."""
    return _json("layer_info", {"layer_id": layer_id})


@mcp.tool()
def add_vector_layer(path: str, name: str = "", provider: str = "ogr") -> str:
    """Add a vector layer from a data source path."""
    return _json("add_vector_layer", {"path": path, "name": name, "provider": provider})


@mcp.tool()
def add_raster_layer(path: str, name: str = "") -> str:
    """Add a raster layer from a file path."""
    return _json("add_raster_layer", {"path": path, "name": name})


@mcp.tool()
def remove_layer(layer_id: str) -> str:
    """Remove a layer from the project by id."""
    return _json("remove_layer", {"layer_id": layer_id})


@mcp.tool()
def set_active_layer(layer_id: str) -> str:
    """Make a layer the active layer."""
    return _json("set_active_layer", {"layer_id": layer_id})


@mcp.tool()
def set_layer_visibility(layer_id: str, visible: bool = True) -> str:
    """Show or hide a layer in the layer tree."""
    return _json("set_layer_visibility", {"layer_id": layer_id, "visible": visible})


@mcp.tool()
def zoom_to_layer(layer_id: str) -> str:
    """Zoom the map canvas to a layer's extent."""
    return _json("zoom_to_layer", {"layer_id": layer_id})


@mcp.tool()
def get_features(layer_id: str, limit: int = 20, include_geometry: bool = False) -> str:
    """Read up to `limit` features (attributes, optional WKT geometry)."""
    return _json(
        "get_features",
        {"layer_id": layer_id, "limit": limit, "include_geometry": include_geometry},
    )


@mcp.tool()
def set_project_crs(authid: str) -> str:
    """Set the project CRS by authority id, e.g. 'EPSG:4326'."""
    return _json("set_project_crs", {"authid": authid})


@mcp.tool()
def save_project(path: str = "") -> str:
    """Save the project (to `path`, or the current file if empty)."""
    return _json("save_project", {"path": path})


@mcp.tool()
def load_project(path: str) -> str:
    """Open a .qgz/.qgs project file."""
    return _json("load_project", {"path": path})


@mcp.tool()
def list_algorithms(filter: str = "") -> str:
    """List Processing algorithm ids (optionally filtered by substring)."""
    return _json("list_algorithms", {"filter": filter})


@mcp.tool()
def run_algorithm(algorithm_id: str, parameters: dict) -> str:
    """Run a NAMED Processing algorithm with a parameter dict (no arbitrary code)."""
    return _json("run_algorithm", {"algorithm_id": algorithm_id, "parameters": parameters})


@mcp.tool()
def render_map(width: int = 0, height: int = 0) -> Image:
    """Render the current map canvas to a PNG so you can see it."""
    resp = _command("render_map", {"width": width, "height": height})
    if not resp.get("ok"):
        raise RuntimeError(resp.get("error", "render failed"))
    return Image(data=base64.b64decode(resp["image_base64"]), format="png")


if __name__ == "__main__":
    mcp.run()
