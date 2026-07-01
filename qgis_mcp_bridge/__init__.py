def classFactory(iface):
    from .plugin import QgisMcpBridgePlugin
    return QgisMcpBridgePlugin(iface)
