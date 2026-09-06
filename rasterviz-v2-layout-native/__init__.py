# RasterViz — native QGIS Layout colorbar item (Layout-native method)
# License: GNU GPL v2 or later
# https://github.com/Defani/QRasterVIZ


def classFactory(iface):
    from .qrviz import QRVIZPlugin
    return QRVIZPlugin(iface)
