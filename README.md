# Meridian — QGIS MCP Bridge

**Give QGIS its bearing.** Meridian lets an AI assistant (e.g. Claude) control
QGIS over the **Model Context Protocol**. A small HTTP server runs *inside* QGIS
and executes PyQGIS on the Qt main thread; a companion MCP server exposes that to
any MCP client.

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

> ⚠️ **Read [Security](#security) first.** Meridian executes arbitrary Python by
> design. It is distributed here on GitHub (Install-from-ZIP) — **not** on the
> official QGIS plugin repository — precisely because that capability is a
> deliberate, trust-based feature.

## Install the plugin (Install from ZIP)

1. Download **`qgis_mcp_bridge.zip`** from the [latest release](https://github.com/STOKEDMODELLER/qgis-mcp-bridge/releases/latest).
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP** → select the file → **Install Plugin**.
3. It binds to `127.0.0.1:9876` and autostarts. Toggle it from **Plugins → Meridian — QGIS MCP Bridge**.

Verify: `curl http://127.0.0.1:9876/ping`

## Register the MCP server with Claude

**Claude Code:**

```bash
claude mcp add meridian -- uv run --with 'mcp[cli]' \
  python /absolute/path/to/mcp_server/qgis_mcp_server.py
```

**Claude Desktop** — in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "meridian": {
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

Meridian is a **remote-code-execution surface by design** — anything the QGIS
Python console can do, a connected client can do, including touching the file
system. Treat it accordingly.

Built-in protections:

- **Localhost only.** Binds to `127.0.0.1`; never exposed off the machine.
- **No cross-origin.** Any request carrying an `Origin`/`Referer` header is
  refused (blocks malicious web pages and DNS-rebinding drive-by attacks). A
  legitimate MCP client sends neither.
- **Optional shared secret.** Set `QGIS_MCP_TOKEN` in QGIS's environment and the
  same value for the MCP server; every request must then present a matching
  `X-QGIS-MCP-Token` header.

```bash
# Example: require a token
export QGIS_MCP_TOKEN="$(openssl rand -hex 16)"   # set before launching QGIS
# set the SAME value in the environment that runs the MCP server
```

Recommendations: run only with MCP clients you trust; enable the token on shared
machines; toggle the bridge off (**Plugins → Meridian — QGIS MCP Bridge**) when
you're not using it.

## License

MIT — see [LICENSE](LICENSE).
