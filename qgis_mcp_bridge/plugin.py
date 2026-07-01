"""QGIS plugin entry point: toggles the MCP bridge server on/off."""

from qgis.PyQt.QtWidgets import QAction

from .bridge_server import DEFAULT_HOST, DEFAULT_PORT, BridgeServer

MENU = "QGIS MCP Bridge"


class QgisMcpBridgePlugin:
    def __init__(self, iface):
        self.iface = iface
        self.server = None
        self.action = None

    # -- QGIS plugin lifecycle ------------------------------------------- #
    def initGui(self):
        self.action = QAction("MCP Bridge (listening)", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._on_toggle)
        self.iface.addPluginToMenu(MENU, self.action)
        # Autostart so Claude can connect as soon as QGIS is open.
        self._start()

    def unload(self):
        self._stop()
        if self.action is not None:
            self.iface.removePluginMenu(MENU, self.action)
            self.action = None

    # -- internals ------------------------------------------------------- #
    def _on_toggle(self, checked):
        if checked:
            self._start()
        else:
            self._stop()

    def _start(self):
        if self.server is not None:
            return
        try:
            self.server = BridgeServer(DEFAULT_HOST, DEFAULT_PORT)
            self.server.start()
        except OSError as exc:
            self.iface.messageBar().pushWarning(
                "QGIS MCP Bridge", f"Could not bind {DEFAULT_HOST}:{DEFAULT_PORT} ({exc})"
            )
            self.server = None
            if self.action:
                self.action.blockSignals(True)
                self.action.setChecked(False)
                self.action.blockSignals(False)
            return
        if self.action:
            self.action.blockSignals(True)
            self.action.setChecked(True)
            self.action.setText("MCP Bridge (listening)")
            self.action.blockSignals(False)
        self.iface.messageBar().pushSuccess(
            "QGIS MCP Bridge", f"Bridge listening on {DEFAULT_HOST}:{DEFAULT_PORT}"
        )

    def _stop(self):
        if self.server is not None:
            self.server.stop()
            self.server = None
        if self.action:
            self.action.blockSignals(True)
            self.action.setChecked(False)
            self.action.setText("MCP Bridge (stopped)")
            self.action.blockSignals(False)
