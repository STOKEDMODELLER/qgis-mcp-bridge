def classFactory(iface):
    from .plugin import MeridianPlugin
    return MeridianPlugin(iface)
