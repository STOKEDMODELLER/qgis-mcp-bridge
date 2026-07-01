# QGIS MCP Bridge

Control QGIS from an AI assistant (e.g. Claude) over the **Model Context
Protocol**. A small HTTP server runs *inside* QGIS and executes PyQGIS on the Qt
main thread; a companion MCP server exposes that to any MCP client.

```
Claude (Desktop / Code)
        │  MCP (stdio)
        ▼
mcp_server/qgis_mcp_server.py        ← separate process, no PyQGIS
        │  JSON over HTTP (127.0.0.1:9876)
        ▼
qgis_mcp_bridge  (QGIS plugin)       ← runs code on the Qt main thread
        │
        ▼
PyQGIS  (iface, QgsProject, processing, …)
```

QGIS objects live only inside QGIS's embedded Python on its GUI thread, so the
plugin is the only thing that can touch them; the MCP server is the adapter the
AI client speaks to.

## Components

| Path | Runs where | Deps |
|------|-----------|------|
| `qgis_mcp_bridge/` | Inside QGIS | none (stdlib + PyQt + qgis) |
| `mcp_server/` | Separate process | `mcp[cli]` |

## Install the plugin

**From ZIP:** download `qgis_mcp_bridge.zip` from the releases/repo, then in QGIS:
**Plugins → Manage and Install Plugins → Install from ZIP**. It listens on
`127.0.0.1:9876` and autostarts; toggle it from **Plugins → QGIS MCP Bridge**.

Verify: `curl http://127.0.0.1:9876/ping`

## Register the MCP server with Claude

**Claude Code:**

```bash
claude mcp add qgis-bridge -- uv run --with 'mcp[cli]' \
  python /absolute/path/to/mcp_server/qgis_mcp_server.py
```

**Claude Desktop** — in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "qgis-bridge": {
      "command": "uv",
      "args": ["run", "--with", "mcp[cli]", "python",
               "/absolute/path/to/mcp_server/qgis_mcp_server.py"]
    }
  }
}
```

## Tools

| Tool | Description |
|------|-------------|
| `ping` | Health check + QGIS version |
| `run_pyqgis(code)` | Execute any PyQGIS on the main thread |
| `render_map(width, height)` | Return the canvas as a PNG |
| `list_layers()` | Loaded layers (id, name, type, CRS, source, feature count) |
| `project_info()` | Project file, CRS, layer count, extent, scale |
| `list_algorithms(filter)` | Processing algorithm ids |

`run_pyqgis` pre-loads `iface`, `qgis`, `processing`, and every `Qgs*` class.

## Security

- Binds to **127.0.0.1 only**.
- **Arbitrary code execution by design** — anything the QGIS Python console can
  do, this can do. Use only with trusted, local MCP clients.
- Toggle off anytime from **Plugins → QGIS MCP Bridge**.

## License

MIT — see [LICENSE](LICENSE).
