# -*- coding: utf-8 -*-
"""
RasterViz — dedicated dock panel (Layout-native method).

This replaces the old approach of embedding the settings inside QGIS's
native "Item Properties" panel. Instead, one instance of this dock
widget is added automatically to every Layout Designer window (see
qrviz.py), docked on the right, exactly like the native "Items",
"Undo History" etc. panels — so it shows up in the Layout window's
Panels menu and is visible right away, with no manual setup needed.

The panel tracks the layout's current selection: whenever a RasterViz
Colorbar item is selected on the layout, its settings are loaded here;
otherwise the panel shows a placeholder with a one-click "Add Colorbar"
button, so the feature is easy to find immediately after installing
the plugin.
"""

import math
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QWidget,
    QDockWidget,
    QVBoxLayout,
    QFormLayout,
    QComboBox,
    QDoubleSpinBox,
    QSpinBox,
    QCheckBox,
    QLineEdit,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QFontComboBox,
)
from qgis.PyQt.QtGui import QColor, QFont, QPixmap
from qgis.gui import (
    QgsMapLayerComboBox,
    QgsColorRampButton,
    QgsColorButton,
)
from qgis.core import (
    QgsMapLayer,
    QgsMapLayerProxyModel,
    QgsLayoutPoint,
    QgsUnitTypes,
    QgsColorRampShader,
    QgsRasterShader,
    QgsSingleBandPseudoColorRenderer,
    QgsGradientColorRamp,
    QgsStyle,
    QgsMessageLog,
    Qgis,
)

from .colorbar_item import (
    QRVizColorbarItem,
    QRVIZ_COLORBAR_ITEM_TYPE,
    TICK_OUTSIDE,
    TICK_INSIDE,
    BAR_CONTINUOUS,
    BAR_DISCRETE,
)


class QRVizColorbarPanel(QDockWidget):
    """One instance lives in each open Layout Designer window."""

    # Combo index <-> QgsColorRampShader.Type, for the "Interpolation" combo
    # (labelled Discrete/Linear/Exact, matching the layer's own Symbology
    # tab, where "Linear" is QGIS's UI name for QgsColorRampShader.Interpolated).
    _INTERP_INDEX_TO_TYPE = {
        0: QgsColorRampShader.Discrete,
        1: QgsColorRampShader.Interpolated,
        2: QgsColorRampShader.Exact,
    }
    _INTERP_TYPE_TO_INDEX = {v: k for k, v in _INTERP_INDEX_TO_TYPE.items()}

    def __init__(self, designer):
        super().__init__("RasterViz", designer.window())
        self.setObjectName("QRVizColorbarPanel")
        self.designer = designer
        self.layout_ = designer.layout()
        self.item = None
        self._loading = False

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self.setWidget(self._scroll)

        self._body = None
        self._show_empty_state()

        if self.layout_ is not None:
            self.layout_.selectionChanged.connect(self._on_selection_changed)
        self._sync_to_selection()

    # ---- lifecycle -----------------------------------------------------
    def closePanel(self):
        """Called by the plugin on unload / designer close to disconnect
        cleanly before the dock widget itself is deleted."""
        if self.layout_ is not None:
            try:
                self.layout_.selectionChanged.disconnect(self._on_selection_changed)
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"RasterViz: could not disconnect selectionChanged signal: {e}",
                    "RasterViz",
                    Qgis.Warning,
                )
        self._disconnect_item_size_signal(self.item)

    def _disconnect_item_size_signal(self, item):
        if item is None:
            return
        try:
            item.sizePositionChanged.disconnect(self._on_item_size_changed)
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not disconnect sizePositionChanged signal: {e}",
                "RasterViz",
                Qgis.Warning,
            )

    # ---- selection tracking ---------------------------------------------
    def _on_selection_changed(self):
        self._sync_to_selection()

    def _sync_to_selection(self):
        item = self._find_selected_colorbar_item()
        if item is self.item:
            return
        self._disconnect_item_size_signal(self.item)
        self.item = item
        if item is None:
            self._show_empty_state()
        else:
            self._show_item_form(item)

    def _find_selected_colorbar_item(self):
        if self.layout_ is None:
            return None
        try:
            for it in self.layout_.selectedLayoutItems():
                if it.type() == QRVIZ_COLORBAR_ITEM_TYPE:
                    return it
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: error while scanning selected layout items: {e}",
                "RasterViz",
                Qgis.Warning,
            )
        return None

    # ---- empty state (no colorbar item selected) ------------------------
    def _show_empty_state(self):
        body = QWidget()
        v = QVBoxLayout(body)
        v.setContentsMargins(12, 16, 12, 16)
        v.addStretch(1)
        btn_add = QPushButton("+ Add Colorbar")
        btn_add.clicked.connect(self._on_add_item)
        v.addWidget(btn_add)
        v.addStretch(2)
        self._set_body(body)

    def _on_add_item(self):
        if self.layout_ is None:
            return
        item = QRVizColorbarItem(self.layout_)
        self.layout_.addLayoutItem(item)
        # Drop it near the top-left of page 1 (in mm) so it's easy to spot
        # immediately rather than landing at (0, 0) under the page corner.
        try:
            item.attemptMove(QgsLayoutPoint(15, 15, QgsUnitTypes.LayoutMillimeters), page=0)
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not move new colorbar item to its default "
                f"position: {e}",
                "RasterViz",
                Qgis.Warning,
            )
        self.layout_.setSelectedItem(item)
        self._sync_to_selection()

    # ---- item form --------------------------------------------------------
    def _set_body(self, widget):
        # QScrollArea.setWidget() takes ownership of the new widget and
        # already deletes whatever widget it previously held — calling
        # deleteLater() on the old one ourselves double-deletes it and
        # crashes with "wrapped C/C++ object ... has been deleted".
        self._scroll.setWidget(widget)
        self._body = widget

    def _plugin_icon_pixmap(self, size=20):
        """Small plugin logo (icon.png) shown next to the item heading at
        the top of the form, scaled down to sit neatly next to the text."""
        try:
            path = os.path.join(os.path.dirname(__file__), "icon.png")
            if not os.path.exists(path):
                return None
            pix = QPixmap(path)
            if pix.isNull():
                return None
            return pix.scaled(
                size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        except Exception:
            return None

    def _show_item_form(self, item):
        body = QWidget()
        outer = QVBoxLayout(body)
        outer.setContentsMargins(6, 6, 6, 6)

        # Single heading row (logo + item name) — this used to be two
        # separate lines ("Colorbar (layer)" and then a second, hardcoded
        # "Colorbar" right underneath, on top of the "RasterViz" the dock's
        # own tab/title bar already shows above the panel), which just
        # repeated the same word three times for no reason.
        header_row = QHBoxLayout()
        icon_pixmap = self._plugin_icon_pixmap()
        if icon_pixmap is not None:
            icon_label = QLabel()
            icon_label.setPixmap(icon_pixmap)
            header_row.addWidget(icon_label)
        header = QLabel(item.displayName())
        header.setStyleSheet("font-weight: bold; font-size: 12px;")
        header_row.addWidget(header, 1)
        outer.addLayout(header_row)

        # First form chunk: layer + orientation. Position & Size follows
        # immediately after Orientation (see generic_widget below) since
        # that's the setting people reach for right after picking an
        # orientation — no more scrolling past it at the very top.
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)

        self.cb_layer = QgsMapLayerComboBox()
        self.cb_layer.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self.cb_layer.setAllowEmptyLayer(True)
        self.cb_layer.layerChanged.connect(self._on_layer_changed)
        form.addRow("Raster layer:", self.cb_layer)

        self.btn_refresh = QPushButton("Refresh from layer")
        self.btn_refresh.clicked.connect(self._on_refresh)
        form.addRow("", self.btn_refresh)

        # Real QGIS colour ramp picker — the same dialog/button used on the
        # layer's own Symbology tab (QgsColorRampButton), wired straight to
        # the LINKED RASTER's actual renderer. Picking a ramp here rebuilds
        # that layer's shader, so both the map canvas and this colorbar
        # update together — this is not a colorbar-only preview colour.
        ramp_row = QHBoxLayout()
        self.btn_ramp = QgsColorRampButton()
        self.btn_ramp.setColorRampDialogTitle("Raster colour ramp")
        self.btn_ramp.colorRampChanged.connect(self._on_ramp_changed)
        ramp_row.addWidget(self.btn_ramp, 1)
        self.btn_ramp_reverse = QPushButton("Reverse")
        self.btn_ramp_reverse.setToolTip(
            "Reverse this colour ramp on the linked raster layer itself "
            "(updates the map canvas too, not just this colorbar)."
        )
        self.btn_ramp_reverse.clicked.connect(self._on_reverse_layer_ramp)
        ramp_row.addWidget(self.btn_ramp_reverse)
        form.addRow("Raster colour ramp:", ramp_row)

        # Just the two pieces of the raster's own Min/Max Value Settings
        # that actually change what's drawn (the value range fed into the
        # colour ramp) plus how it's sampled between stops — not the full
        # Layer Properties > Symbology tab (no percentile/std-dev presets,
        # statistics extent or accuracy picker; those tune HOW the range
        # gets computed, not the range/interpolation itself, and are rare
        # to need again once a range is set).
        minmax_row = QHBoxLayout()
        self.sp_min = QDoubleSpinBox()
        self.sp_min.setRange(-1e12, 1e12)
        self.sp_min.setDecimals(6)
        self.sp_min.setKeyboardTracking(False)
        self.sp_min.valueChanged.connect(self._on_min_changed)
        minmax_row.addWidget(self.sp_min, 1)
        minmax_row.addWidget(QLabel("Max:"))
        self.sp_max = QDoubleSpinBox()
        self.sp_max.setRange(-1e12, 1e12)
        self.sp_max.setDecimals(6)
        self.sp_max.setKeyboardTracking(False)
        self.sp_max.valueChanged.connect(self._on_max_changed)
        minmax_row.addWidget(self.sp_max, 1)
        form.addRow("Min:", minmax_row)

        self.cb_interp = QComboBox()
        self.cb_interp.addItems(["Discrete", "Linear", "Exact"])
        self.cb_interp.currentIndexChanged.connect(self._on_interp_changed)
        form.addRow("Interpolation:", self.cb_interp)

        self.cb_orient = QComboBox()
        self.cb_orient.addItems(["horizontal", "vertical"])
        self.cb_orient.currentTextChanged.connect(self._on_orient_changed)
        form.addRow("Orientation:", self.cb_orient)

        # Direct Width/Height — plain and to the point, wired straight to
        # attemptResize() + an explicit refresh()/update(). This exists
        # alongside the native Position and Size section below because the
        # native widget's own Width/Height fields (behind its lock-aspect
        # toggle and reference-point anchoring) were reported to update the
        # item's outer frame without the drawn bar catching up to match —
        # these two fields are the reliable path for resizing the bar itself.
        self.sp_width = QDoubleSpinBox()
        self.sp_width.setRange(1.0, 2000.0)
        self.sp_width.setDecimals(2)
        self.sp_width.setSuffix(" mm")
        self.sp_width.valueChanged.connect(self._on_width_height_changed)
        form.addRow("Width:", self.sp_width)

        self.sp_height = QDoubleSpinBox()
        self.sp_height.setRange(1.0, 2000.0)
        self.sp_height.setDecimals(2)
        self.sp_height.setSuffix(" mm")
        self.sp_height.valueChanged.connect(self._on_width_height_changed)
        form.addRow("Height:", self.sp_height)

        outer.addLayout(form)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        outer.addWidget(sep)

        # Second form chunk: everything else about the colorbar's look.
        form2 = QFormLayout()
        form2.setLabelAlignment(Qt.AlignLeft)

        self.cb_end = QComboBox()
        self.cb_end.addItems([
            "Box (standard)",
            "Right/Top pointed (max)",
            "Left/Bottom pointed (min)",
            "Both pointed",
        ])
        self.cb_end.currentIndexChanged.connect(self._on_end_changed)
        form2.addRow("End style:", self.cb_end)

        self.chk_rounded = QCheckBox("Rounded corners")
        self.chk_rounded.toggled.connect(self._on_rounded_changed)
        form2.addRow("", self.chk_rounded)

        self.sp_corner_radius = QDoubleSpinBox()
        self.sp_corner_radius.setRange(0.0, 20.0)
        self.sp_corner_radius.setSuffix(" mm")
        self.sp_corner_radius.setSingleStep(0.5)
        self.sp_corner_radius.valueChanged.connect(self._on_corner_radius_changed)
        form2.addRow("Corner radius:", self.sp_corner_radius)

        self.cb_bar_style = QComboBox()
        self.cb_bar_style.addItems(["Continuous (gradient)", "Discrete (stepped)"])
        self.cb_bar_style.setToolTip(
            "Discrete draws the bar as tick_count - 1 solid colour blocks "
            "with a divider at every tick, instead of a smooth gradient."
        )
        self.cb_bar_style.currentIndexChanged.connect(self._on_bar_style_changed)
        form2.addRow("Bar style:", self.cb_bar_style)

        self.sp_color_steps = QSpinBox()
        self.sp_color_steps.setRange(2, 256)
        self.sp_color_steps.valueChanged.connect(self._on_color_steps_changed)
        form2.addRow("Number of colors:", self.sp_color_steps)

        self.chk_reverse = QCheckBox("Reverse colour order")
        self.chk_reverse.setToolTip(
            "Reverses the colour order. When the linked raster is styled as "
            "Singleband pseudocolor, this reverses that layer's own colour "
            "ramp too (same effect as the 'Reverse' button above), so the "
            "map canvas and this printed colorbar always stay in sync."
        )
        self.chk_reverse.toggled.connect(self._on_reverse_changed)
        form2.addRow("", self.chk_reverse)

        self.sp_ticks = QSpinBox()
        self.sp_ticks.setRange(2, 30)
        self.sp_ticks.valueChanged.connect(self._on_ticks_changed)
        form2.addRow("Tick count:", self.sp_ticks)

        self.sp_decimals = QSpinBox()
        self.sp_decimals.setRange(0, 8)
        self.sp_decimals.valueChanged.connect(self._on_decimals_changed)
        form2.addRow("Tick decimals:", self.sp_decimals)

        self.cb_tick_pos = QComboBox()
        self.cb_tick_pos.addItems(["Outside", "Inside"])
        self.cb_tick_pos.currentIndexChanged.connect(self._on_tick_pos_changed)
        form2.addRow("Tick position:", self.cb_tick_pos)

        self.sp_tick_padding = QDoubleSpinBox()
        self.sp_tick_padding.setRange(0.0, 20.0)
        self.sp_tick_padding.setDecimals(2)
        self.sp_tick_padding.setSuffix(" mm")
        self.sp_tick_padding.setSingleStep(0.5)
        self.sp_tick_padding.valueChanged.connect(self._on_tick_padding_changed)
        form2.addRow("Tick padding:", self.sp_tick_padding)

        self.cb_tick_font = QFontComboBox()
        self.cb_tick_font.currentFontChanged.connect(self._on_tick_font_family_changed)
        form2.addRow("Tick font family:", self.cb_tick_font)

        tick_style = QHBoxLayout()
        self.sp_tick_size = QSpinBox()
        self.sp_tick_size.setRange(6, 40)
        self.sp_tick_size.valueChanged.connect(self._on_tick_size_changed)
        self.chk_tick_bold = QCheckBox("Bold")
        self.chk_tick_bold.toggled.connect(self._on_tick_bold_changed)
        self.chk_tick_italic = QCheckBox("Italic")
        self.chk_tick_italic.toggled.connect(self._on_tick_italic_changed)
        self.btn_tick_color = QgsColorButton()
        self.btn_tick_color.setAllowOpacity(True)
        self.btn_tick_color.colorChanged.connect(self._on_tick_color_changed)
        tick_style.addWidget(self.sp_tick_size)
        tick_style.addWidget(self.chk_tick_bold)
        tick_style.addWidget(self.chk_tick_italic)
        tick_style.addWidget(self.btn_tick_color, 1)
        form2.addRow("Tick font:", tick_style)

        self.le_label = QLineEdit()
        self.le_label.textChanged.connect(self._on_label_text_changed)
        form2.addRow("Label text:", self.le_label)

        self.sp_label_padding = QDoubleSpinBox()
        self.sp_label_padding.setRange(0.0, 20.0)
        self.sp_label_padding.setDecimals(2)
        self.sp_label_padding.setSuffix(" mm")
        self.sp_label_padding.setSingleStep(0.5)
        self.sp_label_padding.valueChanged.connect(self._on_label_padding_changed)
        form2.addRow("Label padding:", self.sp_label_padding)

        self.cb_label_font = QFontComboBox()
        self.cb_label_font.currentFontChanged.connect(self._on_label_font_family_changed)
        form2.addRow("Label font family:", self.cb_label_font)

        label_style = QHBoxLayout()
        self.sp_label_size = QSpinBox()
        self.sp_label_size.setRange(6, 40)
        self.sp_label_size.valueChanged.connect(self._on_label_size_changed)
        self.chk_label_bold = QCheckBox("Bold")
        self.chk_label_bold.toggled.connect(self._on_label_bold_changed)
        self.chk_label_italic = QCheckBox("Italic")
        self.chk_label_italic.toggled.connect(self._on_label_italic_changed)
        self.btn_label_color = QgsColorButton()
        self.btn_label_color.setAllowOpacity(True)
        self.btn_label_color.colorChanged.connect(self._on_label_color_changed)
        label_style.addWidget(self.sp_label_size)
        label_style.addWidget(self.chk_label_bold)
        label_style.addWidget(self.chk_label_italic)
        label_style.addWidget(self.btn_label_color, 1)
        form2.addRow("Label font:", label_style)

        outer.addLayout(form2)

        # Native Position & Size / Rotation / Item ID / Rendering / Variables
        # block removed on request — position is set by dragging the item
        # on the layout canvas (or the native Item Properties tab, if that
        # block is ever wanted again), and resizing stays covered by the
        # Width/Height fields above, which already stay in sync with
        # dragging the item's own handles via sizePositionChanged below.
        try:
            item.sizePositionChanged.connect(self._on_item_size_changed)
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not connect sizePositionChanged signal: {e}",
                "RasterViz",
                Qgis.Warning,
            )

        outer.addStretch(1)

        body.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._set_body(body)

        self._loading = True
        self._load_from_item(item)
        self._loading = False

    def _load_from_item(self, item):
        self.cb_layer.setLayer(item.linkedLayer())
        self._load_ramp_button_from_layer(item.linkedLayer())
        self.cb_orient.setCurrentText(item.orientation())
        # Width/Height show the BAR's own size, never the item's total
        # frame (item.rect()) — the frame is auto-calculated around the bar
        # plus ticks/labels and isn't something the user edits directly.
        self.sp_width.setValue(item.barWidth())
        self.sp_height.setValue(item.barHeight())
        self.cb_end.setCurrentIndex(item.endStyle())
        self.chk_rounded.setChecked(item.rounded())
        self.sp_corner_radius.setValue(item.cornerRadius())
        self.sp_corner_radius.setEnabled(item.rounded())
        self.cb_bar_style.setCurrentIndex(1 if item.barStyle() == BAR_DISCRETE else 0)
        self.sp_color_steps.setValue(item.colorSteps())
        self.chk_reverse.setChecked(item.reverseColors())
        self.sp_ticks.setValue(item.tickCount())
        self.sp_decimals.setValue(item.decimals())
        self.cb_tick_pos.setCurrentIndex(1 if item.tickPosition() == TICK_INSIDE else 0)
        self.sp_tick_padding.setValue(item.tickPadding())
        self.sp_tick_size.setValue(item.tickSize())
        self.chk_tick_bold.setChecked(item.tickBold())
        self.chk_tick_italic.setChecked(item.tickItalic())
        self.btn_tick_color.setColor(item.tickColor())
        self._set_font_combo(self.cb_tick_font, item.tickFontFamily())
        self.le_label.setText(item.labelText())
        self.sp_label_padding.setValue(item.labelPadding())
        self.sp_label_size.setValue(item.labelSize())
        self.chk_label_bold.setChecked(item.labelBold())
        self.chk_label_italic.setChecked(item.labelItalic())
        self.btn_label_color.setColor(item.labelColor())
        self._set_font_combo(self.cb_label_font, item.labelFontFamily())

    def _set_font_combo(self, combo, family):
        # An empty family means "use the default application font" — leave
        # the combo on whatever it already shows rather than forcing it to
        # a specific (and possibly misleading) entry.
        if family:
            combo.blockSignals(True)
            combo.setCurrentFont(QFont(family))
            combo.blockSignals(False)

    # ---- slots (each guarded so programmatic reloads don't re-fire) ----
    def _on_layer_changed(self, layer):
        if not self._loading and self.item is not None:
            self.item.setLinkedLayer(layer)
            self._ensure_pseudocolor_default_style(layer)
        self._load_ramp_button_from_layer(layer)

    def _ensure_pseudocolor_default_style(self, layer):
        """Picking a raster here is meant to immediately give access to the
        panel's colour-ramp/reverse controls, which only work against a
        Singleband pseudocolor renderer. Rasters often come in styled as
        Singleband gray (or some other render type) by default, which left
        those controls disabled until the user went into the layer's own
        Symbology tab and switched render type manually first. Switch a
        freshly linked raster to Singleband pseudocolor automatically —
        with a sensible default ramp/range from the band's own statistics —
        unless it's already styled that way."""
        if layer is None or layer.type() != QgsMapLayer.RasterLayer:
            return
        renderer = layer.renderer()
        if isinstance(renderer, QgsSingleBandPseudoColorRenderer):
            return
        band = 1
        try:
            if renderer is not None:
                band = renderer.band()
        except Exception:
            band = 1
        try:
            stats = layer.dataProvider().bandStatistics(band)
            vmin, vmax = stats.minimumValue, stats.maximumValue
            if not (vmax > vmin):
                raise ValueError
        except Exception:
            vmin, vmax = 0.0, 1.0
        ramp = QgsStyle.defaultStyle().colorRamp("Viridis")
        if ramp is None:
            ramp = QgsGradientColorRamp(QColor(0, 0, 0), QColor(255, 255, 255))
        shader = QgsColorRampShader(
            vmin, vmax, ramp.clone(),
            QgsColorRampShader.Interpolated, QgsColorRampShader.Continuous,
        )
        shader.classifyColorRamp()
        raster_shader = QgsRasterShader()
        raster_shader.setRasterShaderFunction(shader)
        new_renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), band, raster_shader)
        new_renderer.setClassificationMin(vmin)
        new_renderer.setClassificationMax(vmax)
        layer.setRenderer(new_renderer)
        try:
            layer.emitStyleChanged()
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not emit styleChanged after applying the raster's new renderer: {e}",
                "RasterViz",
                Qgis.Warning,
            )
        layer.triggerRepaint()
        self._refresh_map_canvas()

    # ---- raster colour ramp (real QGIS symbology, linked to the raster) --
    def _current_pseudocolor_renderer(self):
        """(layer, renderer) for the linked raster if — and only if — it's
        currently styled as Singleband pseudocolor; (layer, None) or
        (None, None) otherwise. The ramp button only makes sense for that
        renderer type (gray/paletted rasters have no colour ramp to pick)."""
        layer = self.item.linkedLayer() if self.item is not None else None
        if layer is None:
            return None, None
        renderer = layer.renderer()
        if not isinstance(renderer, QgsSingleBandPseudoColorRenderer):
            return layer, None
        return layer, renderer

    def _load_ramp_button_from_layer(self, layer):
        renderer = layer.renderer() if layer is not None else None
        ramp = None
        vmin = vmax = None
        interp_index = 1  # Linear, i.e. QgsColorRampShader.Interpolated
        if isinstance(renderer, QgsSingleBandPseudoColorRenderer):
            shader = renderer.shader()
            ramp_shader = shader.rasterShaderFunction() if shader else None
            src_ramp = ramp_shader.sourceColorRamp() if ramp_shader else None
            if src_ramp is not None:
                ramp = src_ramp.clone()
            if ramp_shader is not None:
                try:
                    interp_index = self._INTERP_TYPE_TO_INDEX.get(
                        ramp_shader.colorRampType(), 1)
                except Exception as e:
                    QgsMessageLog.logMessage(
                        f"RasterViz: could not read interpolation type from the raster colour ramp shader: {e}",
                        "RasterViz",
                        Qgis.Warning,
                    )
            vmin, vmax = self._robust_classification_range(layer, renderer, renderer.band())
        can_edit = ramp is not None
        if ramp is None:
            # Nothing to show yet (no layer linked, or it isn't styled as
            # pseudocolor) — fall back to a placeholder ramp just so the
            # button isn't blank; it stays disabled either way.
            ramp = QgsStyle.defaultStyle().colorRamp("Viridis")
        self.btn_ramp.blockSignals(True)
        self.btn_ramp.setColorRamp(ramp)
        self.btn_ramp.blockSignals(False)
        self.btn_ramp.setEnabled(can_edit)
        self.btn_ramp_reverse.setEnabled(can_edit)

        self.sp_min.blockSignals(True)
        self.sp_max.blockSignals(True)
        self.sp_min.setValue(vmin if vmin is not None else 0.0)
        self.sp_max.setValue(vmax if vmax is not None else 1.0)
        self.sp_min.blockSignals(False)
        self.sp_max.blockSignals(False)
        self.sp_min.setEnabled(can_edit)
        self.sp_max.setEnabled(can_edit)

        self.cb_interp.blockSignals(True)
        self.cb_interp.setCurrentIndex(interp_index)
        self.cb_interp.blockSignals(False)
        self.cb_interp.setEnabled(can_edit)

    def _robust_classification_range(self, layer, renderer, band):
        """(vmin, vmax) to carry over to a rebuilt renderer, robust against
        a renderer whose classificationMin()/Max() already came back NaN
        (see _apply_ramp_to_layer for why that happens)."""
        vmin, vmax = renderer.classificationMin(), renderer.classificationMax()
        if (vmin is not None and vmax is not None
                and not math.isnan(vmin) and not math.isnan(vmax) and vmax > vmin):
            return vmin, vmax
        # classificationMin/Max were unusable — fall back to the existing
        # shader's own colour ramp item list (still has the real range even
        # if classificationMin/Max were never explicitly set on it).
        shader = renderer.shader()
        ramp_shader = shader.rasterShaderFunction() if shader else None
        items = ramp_shader.colorRampItemList() if ramp_shader else None
        if items:
            v0, v1 = items[0].value, items[-1].value
            if v1 > v0:
                return v0, v1
        # Last resort: compute it from the raster band itself.
        try:
            stats = layer.dataProvider().bandStatistics(band)
            if stats.maximumValue > stats.minimumValue:
                return stats.minimumValue, stats.maximumValue
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not compute a fallback value range from band statistics: {e}",
                "RasterViz",
                Qgis.Warning,
            )
        return 0.0, 1.0

    def _apply_ramp_to_layer(self, ramp):
        self._apply_style_to_layer(ramp=ramp)

    def _apply_style_to_layer(self, ramp=None, vmin=None, vmax=None, interp=None):
        """Push a colour ramp / value range / interpolation mode onto the
        LINKED RASTER's own renderer (not just this colorbar's preview
        copy) — whichever of the three the caller passes; anything left
        as None keeps whatever the layer already has. Then repaint the
        canvas and this layout's Map item(s). The colorbar item is already
        listening to the layer's styleChanged signal, so it picks up the
        new colours/range on its own."""
        layer, renderer = self._current_pseudocolor_renderer()
        if renderer is None:
            return
        band = renderer.band()
        if ramp is None:
            ramp = self.btn_ramp.colorRamp()
            if ramp is None:
                return
        if vmin is None or vmax is None:
            cur_vmin, cur_vmax = self._robust_classification_range(layer, renderer, band)
            if vmin is None:
                vmin = cur_vmin
            if vmax is None:
                vmax = cur_vmax
        if not (vmax > vmin):
            return
        if interp is None:
            try:
                shader0 = renderer.shader()
                ramp_shader0 = shader0.rasterShaderFunction() if shader0 else None
                interp = ramp_shader0.colorRampType() if ramp_shader0 else QgsColorRampShader.Interpolated
            except Exception:
                interp = QgsColorRampShader.Interpolated
        shader = QgsColorRampShader(
            vmin, vmax, ramp.clone(),
            interp, QgsColorRampShader.Continuous,
        )
        shader.classifyColorRamp()
        raster_shader = QgsRasterShader()
        raster_shader.setRasterShaderFunction(shader)
        new_renderer = QgsSingleBandPseudoColorRenderer(layer.dataProvider(), band, raster_shader)
        # THE BUG: QgsSingleBandPseudoColorRenderer does NOT pick up
        # classificationMin/Max from the shader/constructor automatically —
        # leaving these unset means the new renderer's own
        # classificationMin()/Max() report NaN. Because "nan <= nan" is
        # False, the colorbar's own "vmax <= vmin -> fall back" guard never
        # fires, so that NaN sails straight through into every sampled
        # colour (all fully transparent -> the bar looks empty) and every
        # tick label (formats to the literal text "nan"). Setting these
        # explicitly, from the SAME vmin/vmax just used to build the
        # shader, is the actual fix.
        new_renderer.setClassificationMin(vmin)
        new_renderer.setClassificationMax(vmax)
        layer.setRenderer(new_renderer)
        try:
            layer.emitStyleChanged()
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: could not emit styleChanged after applying the raster's new renderer: {e}",
                "RasterViz",
                Qgis.Warning,
            )
        layer.triggerRepaint()
        self._refresh_map_canvas()

    def _refresh_map_canvas(self):
        """Force an immediate redraw of both the main map canvas AND any
        Map item(s) inside this same Print Layout after a ramp change.
        layer.triggerRepaint() alone only invalidates the layer's cached
        render and schedules a repaint on whatever canvases are listening —
        it does not itself force that repaint to happen right away, and
        the Layout's own Map item caches its rendered image separately
        from the main canvas, so without this a colour change here still
        needed a manual "Refresh view" in the Layout Designer before the
        printed map caught up, even though the colorbar itself updated
        immediately."""
        try:
            from qgis.utils import iface as qgis_iface
        except Exception:
            qgis_iface = None
        if qgis_iface is not None:
            try:
                canvas = qgis_iface.mapCanvas()
                if canvas is not None:
                    canvas.refresh()
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"RasterViz: could not refresh the main map canvas: {e}",
                    "RasterViz",
                    Qgis.Warning,
                )
        self._refresh_layout_maps()

    def _refresh_layout_maps(self):
        """Invalidate and immediately redraw any QgsLayoutItemMap item(s)
        living in this colorbar's own layout, so the printed map reflects
        a ramp/style change right away instead of only on the next manual
        "Refresh view"."""
        if self.item is None:
            return
        layout = self.item.layout()
        if layout is None:
            return
        try:
            from qgis.core import QgsLayoutItemMap
        except Exception:
            return
        try:
            map_items = layout.items()
        except Exception:
            return
        for it in map_items:
            if isinstance(it, QgsLayoutItemMap):
                try:
                    it.invalidateCache()
                except Exception as e:
                    QgsMessageLog.logMessage(
                        f"RasterViz: could not invalidate a Layout Map item's cache: {e}",
                        "RasterViz",
                        Qgis.Warning,
                    )
                try:
                    it.redraw()
                except Exception as e:
                    QgsMessageLog.logMessage(
                        f"RasterViz: could not redraw a Layout Map item: {e}",
                        "RasterViz",
                        Qgis.Warning,
                    )

    def _on_ramp_changed(self):
        if self._loading or self.item is None:
            return
        ramp = self.btn_ramp.colorRamp()
        if ramp is not None:
            self._apply_ramp_to_layer(ramp)

    def _on_reverse_layer_ramp(self):
        if self.item is None:
            return
        _layer, renderer = self._current_pseudocolor_renderer()
        if renderer is None:
            return
        ramp = self.btn_ramp.colorRamp()
        if ramp is None:
            return
        ramp.invert()  # mutates in place (QgsColorRamp.invert() returns None)
        self.btn_ramp.blockSignals(True)
        self.btn_ramp.setColorRamp(ramp)
        self.btn_ramp.blockSignals(False)
        self._apply_ramp_to_layer(ramp)

    def _on_refresh(self):
        if self.item is not None:
            self.item.refreshData()

    def _on_min_changed(self, v):
        if self._loading or self.item is None:
            return
        if v < self.sp_max.value():
            self._apply_style_to_layer(vmin=v)

    def _on_max_changed(self, v):
        if self._loading or self.item is None:
            return
        if v > self.sp_min.value():
            self._apply_style_to_layer(vmax=v)

    def _on_interp_changed(self, idx):
        if self._loading or self.item is None:
            return
        interp = self._INTERP_INDEX_TO_TYPE.get(idx, QgsColorRampShader.Interpolated)
        self._apply_style_to_layer(interp=interp)

    def _on_orient_changed(self, text):
        if not self._loading and self.item is not None:
            self.item.setOrientation(text)

    def _on_width_height_changed(self, _value):
        if self._loading or self.item is None:
            return
        # setBarWidth()/setBarHeight() set the BAR size and internally
        # recalculate + resize the FRAME around it in one step (see
        # QRVizColorbarItem._resize_frame_to_contents()), so there's no
        # separate frame-then-bar sequencing to get out of order here the
        # way a raw attemptResize() on the frame would have needed.
        self.item.setBarWidth(self.sp_width.value())
        self.item.setBarHeight(self.sp_height.value())

    def _on_item_size_changed(self):
        """Keep the Width/Height fields in sync when the item's selection
        changes size for any other reason (e.g. the native Position and
        Size section below, or dragging its handles on the canvas). These
        always reflect the BAR's own size, never the item's total frame —
        resizing the frame directly this way does not itself change the
        bar, so the fields simply continue to show the current bar size."""
        if self.item is None:
            return
        self._loading = True
        try:
            self.sp_width.setValue(self.item.barWidth())
            self.sp_height.setValue(self.item.barHeight())
        finally:
            self._loading = False

    def _on_end_changed(self, idx):
        if not self._loading and self.item is not None:
            self.item.setEndStyle(idx)

    def _on_rounded_changed(self, v):
        self.sp_corner_radius.setEnabled(v)
        if not self._loading and self.item is not None:
            self.item.setRounded(v)

    def _on_corner_radius_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setCornerRadius(v)

    def _on_color_steps_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setColorSteps(v)

    def _on_bar_style_changed(self, idx):
        if not self._loading and self.item is not None:
            self.item.setBarStyle(BAR_DISCRETE if idx == 1 else BAR_CONTINUOUS)

    def _on_reverse_changed(self, v):
        if self._loading or self.item is None:
            return
        _layer, renderer = self._current_pseudocolor_renderer()
        if renderer is not None:
            # Pseudocolor: reverse the actual raster ramp (same action as
            # the "Reverse" button next to Raster colour ramp above) so the
            # map canvas and this printed colorbar always show the colours
            # running the same direction — everything stays linked, instead
            # of this checkbox only flipping the printed bar's own preview.
            ramp = self.btn_ramp.colorRamp()
            if ramp is not None:
                ramp.invert()  # mutates in place
                self.btn_ramp.blockSignals(True)
                self.btn_ramp.setColorRamp(ramp)
                self.btn_ramp.blockSignals(False)
                self._apply_ramp_to_layer(ramp)
        else:
            # No raster ramp to reverse (unlinked, or styled e.g. as Gray) —
            # fall back to flipping just this colorbar's own preview order.
            self.item.setReverseColors(v)

    def _on_ticks_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setTickCount(v)

    def _on_decimals_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setDecimals(v)

    def _on_tick_pos_changed(self, idx):
        if not self._loading and self.item is not None:
            self.item.setTickPosition(TICK_INSIDE if idx == 1 else TICK_OUTSIDE)

    def _on_tick_padding_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setTickPadding(v)

    def _on_tick_size_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setTickSize(v)

    def _on_tick_bold_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setTickBold(v)

    def _on_tick_italic_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setTickItalic(v)

    def _on_tick_color_changed(self, color):
        if not self._loading and self.item is not None:
            self.item.setTickColor(color)

    def _on_label_color_changed(self, color):
        if not self._loading and self.item is not None:
            self.item.setLabelColor(color)

    def _on_tick_font_family_changed(self, font):
        if not self._loading and self.item is not None:
            self.item.setTickFontFamily(font.family())

    def _on_label_font_family_changed(self, font):
        if not self._loading and self.item is not None:
            self.item.setLabelFontFamily(font.family())

    def _on_label_text_changed(self, text):
        if not self._loading and self.item is not None:
            self.item.setLabelText(text)

    def _on_label_padding_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setLabelPadding(v)

    def _on_label_size_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setLabelSize(v)

    def _on_label_bold_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setLabelBold(v)

    def _on_label_italic_changed(self, v):
        if not self._loading and self.item is not None:
            self.item.setLabelItalic(v)
