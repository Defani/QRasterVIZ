# -*- coding: utf-8 -*-
"""
RasterViz — QGIS plugin (Layout-native method).

This is RasterViz's Layout-native method: instead of opening a
separate Matplotlib-backed dialog window, it adds a single native
Layout item ("RasterViz") to the Layout Designer's Add Item toolbar/
menu. All of its settings live in a dedicated "RasterViz" dock panel —
added automatically to every Layout Designer window on the right-hand
side, exactly like QGIS's own "Items" / "Undo History" panels — rather
than in the generic Item Properties tab. It draws a colour stretch bar
with a configurable number of ticks, reading its colours and value
range live from a raster layer's own symbology, so it stays in sync
with whatever is shown on the map canvas.

This is an alternative method, not a replacement for the classic
dialog-based RasterViz workflow (colormap gallery, RGB composite, web
basemaps, single-window PNG/SVG/TIFF/PDF export). Reach for this one
when a scientific-style colorbar needs to live directly inside a
native QGIS Print Layout, alongside QGIS's own native North Arrow,
Scale Bar, Grid and Legend items.

License: GNU GPL v2 or later
Repository: https://github.com/Defani/QRasterVIZ
"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QDockWidget
from qgis.core import QgsApplication, QgsMessageLog, Qgis
from qgis.gui import QgsGui, QgsLayoutItemAbstractGuiMetadata

from .colorbar_item import QRVIZ_COLORBAR_ITEM_TYPE, QRVizColorbarItemMetadata
from .colorbar_panel import QRVizColorbarPanel


class QRVizColorbarItemGuiMetadata(QgsLayoutItemAbstractGuiMetadata):
    """GUI-side registration: just the toolbar/menu icon used when adding
    the item to a layout. Deliberately no Item Properties tab widget —
    every setting is edited instead in the dedicated "RasterViz" dock
    panel that the plugin keeps docked in each Layout window."""

    def __init__(self, icon):
        super().__init__(QRVIZ_COLORBAR_ITEM_TYPE, "RasterViz")
        self._icon = icon

    def creationIcon(self):
        return self._icon

    def createItemWidget(self, item):
        return None


class QRVIZPlugin:
    def __init__(self, iface):
        self.iface = iface
        self._core_metadata = None
        self._gui_metadata = None
        self._panels = {}  # QgsLayoutDesignerInterface -> QRVizColorbarPanel

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()

        # No custom/domain-specific colour ramps are registered — the
        # "Raster colour ramp" picker (QgsColorRampButton on the panel)
        # shows QGIS's own built-in ramps only.
        registry = QgsApplication.instance().layoutItemRegistry()
        if registry.itemMetadata(QRVIZ_COLORBAR_ITEM_TYPE) is None:
            self._core_metadata = QRVizColorbarItemMetadata()
            registry.addLayoutItemType(self._core_metadata)

        gui_registry = QgsGui.instance().layoutItemGuiRegistry()
        if gui_registry.itemMetadata(QRVIZ_COLORBAR_ITEM_TYPE) is None:
            self._gui_metadata = QRVizColorbarItemGuiMetadata(icon)
            gui_registry.addLayoutItemGuiMetadata(self._gui_metadata)

        # Add the dock panel to every Layout window, present and future,
        # so it's visible immediately after install without the user
        # having to dig through the Panels menu.
        self.iface.layoutDesignerOpened.connect(self._on_designer_opened)
        self.iface.layoutDesignerWillBeClosed.connect(self._on_designer_closing)
        for designer in self.iface.openLayoutDesigners():
            self._on_designer_opened(designer)

    def _on_designer_opened(self, designer):
        if designer in self._panels:
            return
        try:
            panel = QRVizColorbarPanel(designer)
            designer.addDockWidget(Qt.RightDockWidgetArea, panel)
            self._tabify_with_existing(designer, panel)
            self._panels[designer] = panel
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not create/dock the RasterViz panel for a "
                f"Layout window: {e}",
                "RasterViz",
                Qgis.Warning,
            )

    def _tabify_with_existing(self, designer, panel):
        """addDockWidget() alone drops a brand-new dock widget into its own
        row in that area, stacked below whatever's already docked there —
        so on a fresh install RasterViz showed up split off underneath the
        native Items / Item Properties / Layout / Guides tab group instead
        of joining it, and the user had to notice and drag its tab up
        themselves to get the layout in the screenshot. Tabify it with
        whichever of those native docks is found so it lands directly in
        that same tab row, right away, with no manual rearranging."""
        try:
            window = designer.window()
            if window is None:
                return
            docks = window.findChildren(QDockWidget)
            preferred_titles = (
                "Items", "Item Properties", "Layout", "Guides", "Undo History",
            )
            target = None
            for title in preferred_titles:
                for d in docks:
                    if d is not panel and d.isVisible() and d.windowTitle() == title:
                        target = d
                        break
                if target is not None:
                    break
            if target is None:
                # None of the expected native docks were found (e.g. a
                # different QGIS version/locale) — fall back to any other
                # visible dock already sharing the right-hand area, so
                # RasterViz still lands tabbed with *something* rather than
                # stacked on its own.
                for d in docks:
                    if (d is not panel and d.isVisible()
                            and window.dockWidgetArea(d) == Qt.RightDockWidgetArea):
                        target = d
                        break
            if target is not None:
                window.tabifyDockWidget(target, panel)
                panel.show()
                panel.raise_()
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not tabify the RasterViz panel with an "
                f"existing dock: {e}",
                "RasterViz",
                Qgis.Warning,
            )

    def _on_designer_closing(self, designer):
        panel = self._panels.pop(designer, None)
        if panel is not None:
            panel.closePanel()
            try:
                designer.removeDockWidget(panel)
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"RasterViz: could not remove the RasterViz panel from a "
                    f"closing Layout window: {e}",
                    "RasterViz",
                    Qgis.Warning,
                )
            panel.deleteLater()

    def unload(self):
        try:
            self.iface.layoutDesignerOpened.disconnect(self._on_designer_opened)
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not disconnect layoutDesignerOpened signal "
                f"on unload: {e}",
                "RasterViz",
                Qgis.Warning,
            )
        try:
            self.iface.layoutDesignerWillBeClosed.disconnect(self._on_designer_closing)
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not disconnect layoutDesignerWillBeClosed "
                f"signal on unload: {e}",
                "RasterViz",
                Qgis.Warning,
            )
        for designer in list(self._panels.keys()):
            self._on_designer_closing(designer)
        self._panels.clear()

        # QGIS's layout item registries are process-wide singletons with no
        # public "remove a single type" API, the same as core item types —
        # so, like other layout-item plugins, this only releases our own
        # references; a full QGIS restart is what actually clears it.
        self._core_metadata = None
        self._gui_metadata = None
