"""Meridian plugin entry point: toggles the safe command bridge on/off."""

from qgis.PyQt.QtWidgets import QAction

from .bridge_server import DEFAULT_HOST, DEFAULT_PORT, BridgeServer

MENU = "Meridian"


class MeridianPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.server = None
        self.action = None

    def initGui(self):
        self.action = QAction("Meridian (listening)", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.toggled.connect(self._on_toggle)
        self.iface.addPluginToMenu(MENU, self.action)
        self._start()

    def unload(self):
        self._stop()
        if self.action is not None:
            self.iface.removePluginMenu(MENU, self.action)
            self.action = None

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
                "Meridian", f"Could not bind {DEFAULT_HOST}:{DEFAULT_PORT} ({exc})"
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
            self.action.setText("Meridian (listening)")
            self.action.blockSignals(False)
        self.iface.messageBar().pushSuccess(
            "Meridian", f"Bridge listening on {DEFAULT_HOST}:{DEFAULT_PORT}"
        )

    def _stop(self):
        if self.server is not None:
            self.server.stop()
            self.server = None
        if self.action:
            self.action.blockSignals(True)
            self.action.setChecked(False)
            self.action.setText("Meridian (stopped)")
            self.action.blockSignals(False)
