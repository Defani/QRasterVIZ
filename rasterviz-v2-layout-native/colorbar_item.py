# -*- coding: utf-8 -*-
"""
RasterViz — a native QGIS Layout item (Layout-native method).

This is the entire job of the plugin: draw a continuous colour stretch bar
("colorbar") with a configurable number of ticks, living directly inside
QGIS's own Layout Designer (Add Item toolbar + native Item Properties
panel) — not a separate dialog/window.

Colours and the value range (vmin/vmax) are read live from a raster
layer's own renderer (Singleband pseudocolor or Singleband gray), i.e.
exactly what QGIS itself is already drawing on the canvas for that layer.
There is no independent colormap engine here on purpose: whatever the
user sets on the layer's Symbology tab (colour ramp, min/max, classes)
IS the source of truth, and the colorbar re-reads it automatically
whenever that layer's style changes (layer.styleChanged signal) — that's
what keeps it "interactive" with the canvas.

Zero third-party dependencies: only PyQGIS + PyQt5.
"""

import math

from qgis.PyQt.QtCore import Qt, QRectF, QPointF
from qgis.PyQt.QtGui import QColor, QFont, QPen, QPolygonF, QPainterPath
from qgis.core import (
    QgsApplication,
    QgsLayoutItem,
    QgsLayoutItemRegistry,
    QgsLayoutItemAbstractMetadata,
    QgsLayoutSize,
    QgsMessageLog,
    QgsProject,
    QgsRenderContext,
    QgsSingleBandPseudoColorRenderer,
    QgsSingleBandGrayRenderer,
    QgsUnitTypes,
    QgsMapLayer,
    QgsTextFormat,
    QgsTextRenderer,
    Qgis,
)

# Unique plugin item type id. QgsLayoutItemRegistry.PluginItem is the base
# offset every third-party layout item type must build on top of; +2201 is
# just an arbitrary, unlikely-to-collide offset picked for this plugin.
QRVIZ_COLORBAR_ITEM_TYPE = QgsLayoutItemRegistry.PluginItem + 2201

# End-style options for the bar (mirrors matplotlib's colorbar "extend").
END_BOX = 0
END_POINT_MAX = 1   # pointed at the max end only (right / top)
END_POINT_MIN = 2   # pointed at the min end only (left / bottom)
END_POINT_BOTH = 3


TICK_OUTSIDE = "outside"
TICK_INSIDE = "inside"

# Bar style options.
BAR_CONTINUOUS = "continuous"   # smooth gradient (existing behaviour)
BAR_DISCRETE = "discrete"       # stepped blocks, one per tick interval,
                                 # with a divider + a labelled tick at
                                 # every block boundary ("full" ticks)


def _sample_pseudocolor(renderer, n=256):
    """(vmin, vmax, [QColor, ...]) sampled from a live
    QgsSingleBandPseudoColorRenderer's shader, or None if unavailable."""
    try:
        shader = renderer.shader()
        if shader is None:
            return None
        ramp_shader = shader.rasterShaderFunction()
        if ramp_shader is None:
            return None
        items = ramp_shader.colorRampItemList()
        if not items:
            return None
        try:
            vmin = renderer.classificationMin()
            vmax = renderer.classificationMax()
        except Exception:
            vmin, vmax = None, None
        # NaN is not None, and "nan <= nan" is ALWAYS False (in both Python
        # and C++), so a plain "vmax <= vmin" check silently lets a NaN
        # min/max straight through instead of triggering this fallback.
        # That was the "colour bar goes blank + ticks show nan" bug: a
        # renderer built without an explicit classificationMin/Max set
        # (e.g. right after swapping colour ramps) reports NaN here, and
        # every value/colour computed from it below is NaN too.
        if (vmin is None or vmax is None
                or math.isnan(vmin) or math.isnan(vmax) or vmax <= vmin):
            vmin, vmax = items[0].value, items[-1].value
        if vmin is None or vmax is None or math.isnan(vmin) or math.isnan(vmax) or vmax <= vmin:
            return None
        colors = []
        for i in range(n):
            v = vmin + (vmax - vmin) * (i / (n - 1))
            ok, r, g, b, a = ramp_shader.shade(v)
            colors.append(QColor(r, g, b, a) if ok else QColor(0, 0, 0, 0))
        return vmin, vmax, colors
    except Exception:
        return None


def _sample_gray(renderer, n=256):
    """(vmin, vmax, [QColor, ...]) black-to-white ramp from a live
    QgsSingleBandGrayRenderer's contrast enhancement, or None."""
    try:
        ce = renderer.contrastEnhancement()
        if ce is None:
            return None
        vmin, vmax = ce.minimumValue(), ce.maximumValue()
        if vmin is None or vmax is None or math.isnan(vmin) or math.isnan(vmax) or vmax <= vmin:
            return None
        colors = []
        for i in range(n):
            g = int(round(255 * (i / (n - 1))))
            colors.append(QColor(g, g, g, 255))
        return vmin, vmax, colors
    except Exception:
        return None


class QRVizColorbarItem(QgsLayoutItem):
    """Resizable / draggable native layout item drawing a colour stretch
    bar with tick marks, sourced live from a linked raster layer.

    GEOMETRY MODEL
    --------------
    There are two distinct geometries here, mirroring how native QGIS
    layout items such as QgsLayoutItemLegend work:

    * BAR geometry (``self._bar_width_mm`` / ``self._bar_height_mm``) —
      the size of the actual colour gradient rectangle only, in
      millimetres. This is what the panel's Width/Height fields mean, and
      it never changes on its own.

    * FRAME geometry (``self.rect()``, i.e. the QgsLayoutItem's own scene
      rect) — the *total* item footprint, i.e. the bar plus whatever room
      the current tick marks / tick labels / axis label / padding / end
      points need around it. This is always DERIVED from the bar size and
      the current style settings via ``_resize_frame_to_contents()`` — it
      is never edited directly, and the bar is never inferred from it by
      subtraction. Whenever a property that affects that footprint changes
      (font size, decimals, tick count, label text, padding, end style,
      orientation, ...), the frame is recalculated once and resized with
      ``attemptResize()``.

    Both are stored in millimetres (QgsLayoutSize / plain mm floats) —
    never in screen or device pixels — so neither one depends on the
    Layout Designer canvas's current zoom level. Canvas zoom only affects
    the *painter* transform used at draw() time (see ``_mm()`` and
    ``_reference_rc()`` below); it must never leak into stored geometry.
    """

    def __init__(self, layout):
        super().__init__(layout)
        self._layer_id = None
        self._orientation = "horizontal"      # "horizontal" | "vertical"
        # BAR geometry only (mm) — see class docstring. 120 x 7 mm is a
        # sensible non-square starting size for the default (horizontal)
        # orientation, matching what the Width/Height fields will show.
        self._bar_width_mm = 120.0
        self._bar_height_mm = 7.0
        self._tick_count = 10
        self._decimals = 2
        self._end_style = END_BOX
        self._label_text = ""
        self._label_size = 10
        self._label_bold = True
        self._label_italic = False
        self._tick_size = 9
        self._tick_bold = False
        self._tick_italic = False
        self._label_padding = 3.0             # mm
        self._tick_padding = 0.0              # mm — gap between bar edge and tick label
        # extra length added on top of _tick_padding to get the actual tick
        # mark's line length; bumped up (was 1.2) and given a real stroke
        # width via _tick_pen_width() so ticks read clearly instead of as
        # thin hairlines, especially in vertical orientation / on export.
        self._tick_extra_len_mm = 2.2
        # Real, independently-editable colours (not just a black/white
        # toggle) for the tick numbers/outside-tick lines and for the axis
        # label, plus an optional font family override for each (empty
        # string = QGIS/Qt's default application font).
        self._tick_color = QColor(20, 20, 20)
        self._label_color = QColor(20, 20, 20)
        self._tick_font_family = ""
        self._label_font_family = ""
        self._color_steps = 64                 # number of colour patches sampled
        self._bar_style = BAR_CONTINUOUS       # BAR_CONTINUOUS | BAR_DISCRETE
        self._reverse = False
        self._tick_position = TICK_OUTSIDE
        self._rounded = False                  # rounded corners on/off
        self._corner_radius = 2.0              # mm, used only when _rounded
        self._cached = None                    # (vmin, vmax, [QColor,...])
        self.setBackgroundEnabled(False)
        self._connect_layer()
        # Build the initial FRAME from the initial BAR size + style above,
        # instead of hard-resizing the frame directly (that was the "Width/
        # Height only grows the frame" bug: the item's own rect() was being
        # treated as the bar size everywhere downstream).
        self._resize_frame_to_contents()

    # ---- identity ----------------------------------------------------
    def type(self):
        return QRVIZ_COLORBAR_ITEM_TYPE

    def icon(self):
        return QgsApplication.getThemeIcon('/mIconLayoutItemLegend.svg')

    def displayName(self):
        lyr = self.linkedLayer()
        return "Colorbar ({})".format(lyr.name()) if lyr else "Colorbar"

    # ---- layer link ----------------------------------------------------
    def linkedLayer(self):
        if not self._layer_id:
            return None
        return QgsProject.instance().mapLayer(self._layer_id)

    def setLinkedLayer(self, layer):
        self._disconnect_layer()
        self._layer_id = layer.id() if layer else None
        self._connect_layer()
        self.refreshData()

    def _connect_layer(self):
        lyr = self.linkedLayer()
        if lyr is not None:
            try:
                lyr.styleChanged.connect(self.refreshData)
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"RasterViz: could not connect styleChanged signal on layer "
                    f"{lyr.id()}: {e}",
                    "RasterViz",
                    Qgis.Warning,
                )

    def _disconnect_layer(self):
        lyr = self.linkedLayer()
        if lyr is not None:
            try:
                lyr.styleChanged.disconnect(self.refreshData)
            except Exception as e:
                QgsMessageLog.logMessage(
                    f"RasterViz: could not disconnect styleChanged signal on "
                    f"layer {lyr.id()}: {e}",
                    "RasterViz",
                    Qgis.Warning,
                )

    def refreshData(self):
        """Re-read colours/range from the linked layer's *current*
        renderer. Connected to the layer's styleChanged signal, so editing
        that layer's Symbology tab on the canvas updates this colorbar
        automatically — no manual re-render step."""
        self._cached = None
        lyr = self.linkedLayer()
        if lyr is not None and lyr.type() == QgsMapLayer.RasterLayer:
            renderer = lyr.renderer()
            if isinstance(renderer, QgsSingleBandPseudoColorRenderer):
                self._cached = _sample_pseudocolor(renderer, self._color_steps)
            elif isinstance(renderer, QgsSingleBandGrayRenderer):
                self._cached = _sample_gray(renderer, self._color_steps)
        if self._cached and self._reverse:
            vmin, vmax, colors = self._cached
            self._cached = (vmin, vmax, list(reversed(colors)))
        # vmin/vmax can change the widest tick label's width (e.g. "-1234.56"
        # vs "0.12") even with the same decimals/tick-count setting, so the
        # frame footprint needs to be re-derived whenever the underlying
        # layer data changes too, not only when a style property changes.
        self._resize_frame_to_contents()
        self.refresh()
        self.update()

    # ---- properties used by the Item Properties widget ----------------
    def orientation(self):
        return self._orientation

    def setOrientation(self, v):
        changed = v != self._orientation
        self._orientation = v
        if changed:
            # Swap the BAR's own width/height (not the frame's) so it stays
            # a sensible long/short shape after flipping orientation,
            # instead of keeping whatever square-ish bar it happened to
            # have. The frame is then rebuilt around that swapped bar.
            self._fix_bar_aspect_for_orientation()
        self._resize_frame_to_contents()

    def _fix_bar_aspect_for_orientation(self):
        w, h = self._bar_width_mm, self._bar_height_mm
        if self._orientation == "horizontal" and h > w:
            self._bar_width_mm, self._bar_height_mm = h, w
        elif self._orientation == "vertical" and w > h:
            self._bar_width_mm, self._bar_height_mm = h, w

    # ---- BAR geometry (the actual gradient rectangle only) -------------
    def barWidth(self):
        return self._bar_width_mm

    def barHeight(self):
        return self._bar_height_mm

    def setBarWidth(self, v):
        """Sets the BAR's width in mm and rebuilds the frame around it.
        This is the method the panel's Width field must call — never
        attemptResize() directly, which would resize the FRAME instead."""
        self._bar_width_mm = max(0.1, float(v))
        self._resize_frame_to_contents()

    def setBarHeight(self, v):
        """Sets the BAR's height in mm and rebuilds the frame around it.
        This is the method the panel's Height field must call — never
        attemptResize() directly, which would resize the FRAME instead."""
        self._bar_height_mm = max(0.1, float(v))
        self._resize_frame_to_contents()

    def colorSteps(self):
        return self._color_steps

    def setColorSteps(self, v):
        self._color_steps = max(2, int(v))
        self.refreshData()

    def barStyle(self):
        return self._bar_style

    def setBarStyle(self, v):
        self._bar_style = v if v in (BAR_CONTINUOUS, BAR_DISCRETE) else BAR_CONTINUOUS
        self._resize_frame_to_contents()

    def reverseColors(self):
        return self._reverse

    def setReverseColors(self, v):
        self._reverse = bool(v)
        self.refreshData()

    def tickPosition(self):
        return self._tick_position

    def setTickPosition(self, v):
        self._tick_position = v
        self._resize_frame_to_contents()

    def tickCount(self):
        return self._tick_count

    def setTickCount(self, v):
        self._tick_count = max(2, int(v))
        self._resize_frame_to_contents()

    def decimals(self):
        return self._decimals

    def setDecimals(self, v):
        self._decimals = max(0, int(v))
        self._resize_frame_to_contents()

    def endStyle(self):
        return self._end_style

    def setEndStyle(self, v):
        self._end_style = int(v)
        self._resize_frame_to_contents()

    def labelText(self):
        return self._label_text

    def setLabelText(self, v):
        self._label_text = v
        self._resize_frame_to_contents()

    def labelSize(self):
        return self._label_size

    def setLabelSize(self, v):
        self._label_size = int(v)
        self._resize_frame_to_contents()

    def labelBold(self):
        return self._label_bold

    def setLabelBold(self, v):
        self._label_bold = bool(v)
        self._resize_frame_to_contents()

    def labelItalic(self):
        return self._label_italic

    def setLabelItalic(self, v):
        self._label_italic = bool(v)
        self._resize_frame_to_contents()

    def tickSize(self):
        return self._tick_size

    def setTickSize(self, v):
        self._tick_size = int(v)
        self._resize_frame_to_contents()

    def tickBold(self):
        return self._tick_bold

    def setTickBold(self, v):
        self._tick_bold = bool(v)
        self._resize_frame_to_contents()

    def tickItalic(self):
        return self._tick_italic

    def setTickItalic(self, v):
        self._tick_italic = bool(v)
        self._resize_frame_to_contents()

    def rounded(self):
        return self._rounded

    def setRounded(self, v):
        self._rounded = bool(v)
        self._resize_frame_to_contents()

    def cornerRadius(self):
        return self._corner_radius

    def setCornerRadius(self, v):
        self._corner_radius = max(0.0, float(v))
        self._resize_frame_to_contents()

    def labelPadding(self):
        return self._label_padding

    def setLabelPadding(self, v):
        self._label_padding = max(0.0, float(v))
        self._resize_frame_to_contents()

    def tickPadding(self):
        return self._tick_padding

    def setTickPadding(self, v):
        self._tick_padding = max(0.0, float(v))
        self._resize_frame_to_contents()

    # ---- kept for backwards compatibility with older panel/API callers;
    # now implemented as a shortcut over the real tick/label colours below
    # instead of the only way to set them.
    def textLight(self):
        return self._tick_color == QColor(255, 255, 255) and self._label_color == QColor(255, 255, 255)

    def setTextLight(self, v):
        c = QColor(255, 255, 255) if v else QColor(20, 20, 20)
        self.setTickColor(c)
        self.setLabelColor(c)

    def tickColor(self):
        return QColor(self._tick_color)

    def setTickColor(self, color):
        self._tick_color = QColor(color)
        self.update()

    def labelColor(self):
        return QColor(self._label_color)

    def setLabelColor(self, color):
        self._label_color = QColor(color)
        self.update()

    def tickFontFamily(self):
        return self._tick_font_family

    def setTickFontFamily(self, family):
        self._tick_font_family = family or ""
        self._resize_frame_to_contents()

    def labelFontFamily(self):
        return self._label_font_family

    def setLabelFontFamily(self, family):
        self._label_font_family = family or ""
        self._resize_frame_to_contents()

    @staticmethod
    def _color_at_t(colors, t):
        """Nearest sampled colour for a fractional position t in [0, 1]
        along the (already vmin..vmax evenly-sampled) `colors` list. Used
        to pick one representative colour per block in discrete/stepped
        bar style, instead of re-sampling the raster shader directly."""
        n = len(colors)
        idx = int(round(t * (n - 1)))
        idx = max(0, min(n - 1, idx))
        return colors[idx]

    def _discrete_block_colors(self, colors):
        """One colour per block for BAR_DISCRETE style: `tick_count - 1`
        blocks, each coloured with the ramp's colour at its own midpoint
        — so a block sits between two consecutive ticks and its colour
        represents the value range that block spans."""
        num_blocks = max(1, self._tick_count - 1)
        return [self._color_at_t(colors, (i + 0.5) / num_blocks) for i in range(num_blocks)]

    def _tick_pen_width(self, rc):
        """Stroke width for tick lines, in this call's painter units.
        A bare `painter.setPen(QColor(...))` uses Qt's default width-0
        cosmetic pen, which is always exactly 1 *device* pixel regardless
        of the render context's scale — that's why ticks looked fine on
        screen but razor-thin/inconsistent on layout export. Deriving the
        width from mm keeps it proportional at any zoom/DPI, same as the
        bar outline geometry."""
        return self._mm(rc, 0.28)

    # ---- drawing --------------------------------------------------------
    def draw(self, context):
        rc = context.renderContext()
        painter = rc.painter()
        if painter is None:
            return
        rect = self.rect()  # TOTAL FRAME, in millimetres (item-local coords)
        painter.save()
        try:
            if not self._cached:
                self._draw_placeholder(painter, rect)
                return
            vmin, vmax, colors = self._cached
            # Convert the persisted mm-based FRAME and BAR sizes into THIS
            # paint call's own painter units via the render context that
            # QGIS handed us for this call — this is what keeps preview
            # (at any canvas zoom) and export (at any DPI) all drawing the
            # exact same physical geometry: every number below comes from
            # the SAME rc, so they all scale together. Nothing here is
            # computed from canvas zoom, a QGraphicsView transform, or a
            # cached pixel size from a previous paint.
            frame = QRectF(
                self._mm(rc, rect.left()), self._mm(rc, rect.top()),
                self._mm(rc, rect.width()), self._mm(rc, rect.height()),
            )
            bar_w = self._mm(rc, self._bar_width_mm)
            bar_h = self._mm(rc, self._bar_height_mm)
            if self._orientation == "horizontal":
                self._draw_horizontal(painter, rc, frame, bar_w, bar_h, vmin, vmax, colors)
            else:
                self._draw_vertical(painter, rc, frame, bar_w, bar_h, vmin, vmax, colors)
        finally:
            painter.restore()

    def _draw_placeholder(self, painter, rect):
        painter.setPen(QColor(120, 120, 120))
        font = QFont()
        font.setItalic(True)
        font.setPointSizeF(9)
        painter.setFont(font)
        msg = ("No raster linked" if not self.linkedLayer()
               else "Layer has no continuous (pseudocolor/gray) renderer")
        painter.drawText(rect, int(Qt.AlignCenter | Qt.TextWordWrap), msg)

    def _mm(self, rc, mm):
        return rc.convertToPainterUnits(mm, QgsUnitTypes.RenderMillimeters)

    def _reference_rc(self):
        """A QgsRenderContext whose 'painter units' ARE millimetres
        (scaleFactor = 1.0, QGIS's own default). This is deliberately NOT
        the live canvas/export render context — it exists only so that
        _horizontal_content_footprint()/_vertical_content_footprint() can
        be reused, unchanged, for the FRAME-SIZE calculation in
        _resize_frame_to_contents(): calling them with this reference
        context makes every self._mm(rc, x) and text-measurement call
        return a value expressed directly in millimetres, independent of
        the Layout Designer's current canvas zoom or of any export DPI.
        That independence is exactly what keeps the frame's stored size
        (in QgsLayoutSize/mm) from ever being contaminated by canvas zoom,
        a QGraphicsView transform, or device pixels — the anti-patterns
        the zoom bug came from.
        """
        rc = QgsRenderContext()
        rc.setScaleFactor(1.0)
        return rc

    def _end_point_size(self, rc, bar_long_units, bar_thick_units):
        """Size (in the given rc's units) of the pointed-end triangles,
        derived purely from the BAR's own dimensions — never from the
        frame — so the same figure is obtained whether this is called
        during frame-size calculation (mm) or during draw() (painter
        units)."""
        if self._end_style == END_BOX:
            return 0.0
        return min(bar_thick_units * 0.7, bar_long_units * 0.05)

    def _horizontal_content_footprint(self, rc, vmin, vmax, bar_w_units, bar_h_units):
        """Extra space (top, bottom, left, right), in the given rc's own
        units, that ticks/labels/padding/end-points need around a
        horizontal bar of size bar_w_units x bar_h_units. Used both to
        size the FRAME (with a millimetre-scale reference rc) and to place
        the bar inside an already-sized frame at draw time (with the live
        rc) — the same formula, so the two always agree."""
        tick_fmt = self._text_format(self._tick_size, self._tick_bold, self._tick_italic, self._tick_color, self._tick_font_family)
        tick_h = self._text_height(rc, tick_fmt)
        tick_len = self._mm(rc, self._tick_padding + self._tick_extra_len_mm)
        inside = self._tick_position == TICK_INSIDE
        bottom = (0.0 if inside else tick_len) + tick_h

        if self._label_text:
            label_fmt = self._text_format(self._label_size, self._label_bold, self._label_italic, self._label_color, self._label_font_family)
            label_h = self._text_height(rc, label_fmt, self._label_text)
            bottom += self._mm(rc, self._label_padding) + label_h * 1.4

        top = self._mm(rc, 1.5)

        # Half of the widest tick label's width, so the first/last tick's
        # centred text doesn't overhang past the bar's own left/right edge
        # and get clipped by the frame.
        max_tick_w = 0.0
        if vmin is not None and vmax is not None and vmax > vmin:
            for i in range(self._tick_count):
                t = i / max(1, self._tick_count - 1)
                val = vmin + (vmax - vmin) * t
                txt = "{:.{}f}".format(val, self._decimals)
                max_tick_w = max(max_tick_w, self._text_width(rc, tick_fmt, txt))
        half_label_overhang = max_tick_w / 2.0

        point_h = self._end_point_size(rc, bar_w_units, bar_h_units)
        left = max(half_label_overhang, point_h if self._end_style in (END_POINT_MIN, END_POINT_BOTH) else 0.0)
        right = max(half_label_overhang, point_h if self._end_style in (END_POINT_MAX, END_POINT_BOTH) else 0.0)

        return top, bottom, left, right

    def _vertical_content_footprint(self, rc, vmin, vmax, bar_w_units, bar_h_units):
        """Mirror of _horizontal_content_footprint() for a vertical bar:
        returns (top, bottom, left, right) extra space, in the given rc's
        own units."""
        tick_fmt = self._text_format(self._tick_size, self._tick_bold, self._tick_italic, self._tick_color, self._tick_font_family)
        tick_len = self._mm(rc, self._tick_padding + self._tick_extra_len_mm)
        inside = self._tick_position == TICK_INSIDE
        max_tick_w = 0.0
        if vmin is not None and vmax is not None and vmax > vmin:
            for i in range(self._tick_count):
                t = i / max(1, self._tick_count - 1)
                val = vmin + (vmax - vmin) * t
                txt = "{:.{}f}".format(val, self._decimals)
                max_tick_w = max(max_tick_w, self._text_width(rc, tick_fmt, txt))
        right = (0.0 if inside else tick_len) + max_tick_w + self._mm(rc, 1.0)

        if self._label_text:
            label_fmt = self._text_format(self._label_size, self._label_bold, self._label_italic, self._label_color, self._label_font_family)
            label_h = self._text_height(rc, label_fmt, self._label_text)
            # rotated -90 degrees, so the label's font HEIGHT becomes its
            # horizontal footprint, not its (much larger) text width.
            left = self._mm(rc, self._label_padding) + label_h
        else:
            left = self._mm(rc, 2.0)

        point_w = self._end_point_size(rc, bar_h_units, bar_w_units)
        top = point_w if self._end_style in (END_POINT_MAX, END_POINT_BOTH) else 0.0
        bottom = point_w if self._end_style in (END_POINT_MIN, END_POINT_BOTH) else 0.0

        return top, bottom, left, right

    def _resize_frame_to_contents(self):
        """Recalculate the TOTAL FRAME size from the current BAR size plus
        whatever the current style settings need around it, and resize the
        QgsLayoutItem to match — the content-driven counterpart to native
        QGIS methods such as QgsLayoutItemLegend::adjustBoxSize().

        Called once whenever a property that affects the footprint
        changes (see the setters above) — never from draw(), and never
        continuously/recursively. Uses _reference_rc() (scaleFactor = 1.0)
        so the millimetre figure that ends up in attemptResize() is always
        the same, regardless of the Layout Designer's current canvas zoom.
        """
        rc = self._reference_rc()
        vmin, vmax = (self._cached[0], self._cached[1]) if self._cached else (0.0, 1.0)
        bar_w, bar_h = self._bar_width_mm, self._bar_height_mm
        if self._orientation == "horizontal":
            top, bottom, left, right = self._horizontal_content_footprint(rc, vmin, vmax, bar_w, bar_h)
        else:
            top, bottom, left, right = self._vertical_content_footprint(rc, vmin, vmax, bar_w, bar_h)
        frame_w_mm = bar_w + left + right
        frame_h_mm = bar_h + top + bottom
        try:
            self.attemptResize(QgsLayoutSize(frame_w_mm, frame_h_mm, QgsUnitTypes.LayoutMillimeters))
        except Exception as e:
            QgsMessageLog.logMessage(
                f"RasterViz: attemptResize({frame_w_mm:.2f}, {frame_h_mm:.2f}) "
                f"failed: {e}",
                "RasterViz",
                Qgis.Warning,
            )
        self.update()

    def _text_format(self, size_pt, bold, italic, color, family=""):
        """A QgsTextFormat instead of a raw QFont. This is the actual fix
        for the "text and bar go out of sync at different zoom levels"
        bug: QFont.setPixelSize() bakes in a literal device-pixel count
        computed once, which does NOT necessarily track the render
        context's current scale the same way our mm-based bar geometry
        does — that's exactly what made the two drift apart as the canvas
        was zoomed in Layout Designer. QgsTextRenderer/QgsTextFormat is
        what QGIS's own native items (Legend, Scale Bar, titles) use
        internally, so text sized/measured through it tracks the same
        zoom- and export-DPI-aware scaling as everything else drawn via
        the same QgsRenderContext, with no separate pixel math to keep
        in sync ourselves.
        """
        fmt = QgsTextFormat()
        f = QFont(family) if family else QFont()
        f.setBold(bold)
        f.setItalic(italic)
        fmt.setFont(f)
        fmt.setSize(size_pt)
        fmt.setSizeUnit(QgsUnitTypes.RenderPoints)
        fmt.setColor(color)
        return fmt

    def _text_height(self, rc, fmt, sample="0"):
        return QgsTextRenderer.textHeight(rc, fmt, [sample], QgsTextRenderer.Rect)

    def _text_width(self, rc, fmt, text):
        return QgsTextRenderer.textWidth(rc, fmt, [text])

    def _draw_horizontal(self, painter, rc, frame, bar_w, bar_h, vmin, vmax, colors):
        # `frame` is the TOTAL FRAME (self.rect(), already converted to this
        # call's painter units). `bar_w`/`bar_h` are the BAR's own, fixed
        # physical size (also already converted) — they are never derived
        # from the frame, and the bar is never stretched to fill it; the
        # frame was already sized to fit exactly around them (see
        # _resize_frame_to_contents()). Here we only figure out WHERE, inside
        # that frame, the bar and its ticks/labels land.
        tick_fmt = self._text_format(self._tick_size, self._tick_bold, self._tick_italic, self._tick_color, self._tick_font_family)
        tick_h = self._text_height(rc, tick_fmt)
        tick_len = self._mm(rc, self._tick_padding + self._tick_extra_len_mm)
        inside = self._tick_position == TICK_INSIDE
        label_fmt = None
        label_h = 0.0
        if self._label_text:
            label_fmt = self._text_format(self._label_size, self._label_bold, self._label_italic, self._label_color, self._label_font_family)
            label_h = self._text_height(rc, label_fmt, self._label_text)

        top_margin, _bottom, left_margin, _right_margin = self._horizontal_content_footprint(
            rc, vmin, vmax, bar_w, bar_h
        )

        point_h = self._end_point_size(rc, bar_w, bar_h)
        left_pad = point_h if self._end_style in (END_POINT_MIN, END_POINT_BOTH) else 0.0
        right_pad = point_h if self._end_style in (END_POINT_MAX, END_POINT_BOTH) else 0.0

        bar_x0 = frame.left() + left_margin
        bar_x1 = bar_x0 + bar_w
        bar_y0 = frame.top() + top_margin
        bar_y1 = bar_y0 + bar_h

        bar_rect = QRectF(bar_x0, bar_y0, bar_w, bar_h)
        radius_px = self._mm(rc, self._corner_radius) if self._rounded else 0.0
        if radius_px > 0:
            radius_px = min(radius_px, bar_h / 2.0, bar_w / 2.0)

        discrete = self._bar_style == BAR_DISCRETE
        fill_colors = self._discrete_block_colors(colors) if discrete else colors
        n = len(fill_colors)
        seg_w = bar_w / n
        painter.save()
        if radius_px > 0:
            clip_path = QPainterPath()
            clip_path.addRoundedRect(bar_rect, radius_px, radius_px)
            painter.setClipPath(clip_path)
        for i, c in enumerate(fill_colors):
            x = bar_x0 + i * seg_w
            painter.setBrush(c)
            if discrete:
                # Visible divider between blocks — one per tick, so every
                # block boundary lines up exactly with a labelled tick
                # ("full" ticks) instead of a smooth, borderless gradient.
                pen = QPen(QColor(40, 40, 40))
                pen.setWidthF(self._mm(rc, 0.15))
                painter.setPen(pen)
            else:
                painter.setPen(Qt.NoPen)
            painter.drawRect(QRectF(x, bar_y0, seg_w + (0.0 if discrete else 0.75), bar_h))
        painter.restore()

        if left_pad:
            tri = QPolygonF([QPointF(bar_x0, bar_y0), QPointF(bar_x0, bar_y1),
                              QPointF(bar_x0 - point_h, (bar_y0 + bar_y1) / 2)])
            painter.setBrush(fill_colors[0])
            painter.drawPolygon(tri)
        if right_pad:
            tri = QPolygonF([QPointF(bar_x1, bar_y0), QPointF(bar_x1, bar_y1),
                              QPointF(bar_x1 + point_h, (bar_y0 + bar_y1) / 2)])
            painter.setBrush(fill_colors[-1])
            painter.drawPolygon(tri)

        painter.setPen(QColor(40, 40, 40))
        painter.setBrush(Qt.NoBrush)
        if radius_px > 0:
            painter.drawRoundedRect(bar_rect, radius_px, radius_px)
        else:
            painter.drawRect(bar_rect)

        tick_pen_w = self._tick_pen_width(rc)
        for i in range(self._tick_count):
            t = i / (self._tick_count - 1)
            val = vmin + (vmax - vmin) * t
            x = bar_x0 + t * bar_w
            if inside:
                pen = QPen(QColor(255, 255, 255))
                pen.setWidthF(tick_pen_w)
                painter.setPen(pen)
                painter.drawLine(QPointF(x, bar_y1), QPointF(x, bar_y1 - min(tick_len, bar_h)))
            else:
                pen = QPen(self._tick_color)
                pen.setWidthF(tick_pen_w)
                painter.setPen(pen)
                painter.drawLine(QPointF(x, bar_y1), QPointF(x, bar_y1 + tick_len))
            txt = "{:.{}f}".format(val, self._decimals)
            label_top = bar_y1 + (tick_len if not inside else 0.0)
            label_box_w = max(bar_w, self._mm(rc, 12.0))
            label_rect = QRectF(x - label_box_w / 2.0, label_top, label_box_w, tick_h * 1.6)
            QgsTextRenderer.drawText(
                label_rect, 0, QgsTextRenderer.AlignCenter, [txt], rc, tick_fmt,
                True, QgsTextRenderer.AlignTop,
            )

        if self._label_text:
            base_offset = (tick_len if not inside else 0.0) + tick_h
            label_top = bar_y1 + base_offset + self._mm(rc, self._label_padding)
            QgsTextRenderer.drawText(
                QRectF(frame.left(), label_top, frame.width(), label_h * 1.4),
                0, QgsTextRenderer.AlignCenter, [self._label_text], rc, label_fmt,
                True, QgsTextRenderer.AlignTop,
            )

    def _draw_vertical(self, painter, rc, frame, bar_w, bar_h, vmin, vmax, colors):
        # Mirror of _draw_horizontal(): `frame` is the TOTAL FRAME, already
        # sized to fit around the fixed-size BAR (bar_w x bar_h, also
        # already in this call's painter units) plus its ticks/labels — we
        # only need to work out placement here, never bar size.
        tick_fmt = self._text_format(self._tick_size, self._tick_bold, self._tick_italic, self._tick_color, self._tick_font_family)
        tick_h = self._text_height(rc, tick_fmt)
        tick_len = self._mm(rc, self._tick_padding + self._tick_extra_len_mm)
        tick_pen_w = self._tick_pen_width(rc)
        inside = self._tick_position == TICK_INSIDE

        label_fmt = None
        label_h = 0.0
        if self._label_text:
            label_fmt = self._text_format(self._label_size, self._label_bold, self._label_italic, self._label_color, self._label_font_family)
            label_h = self._text_height(rc, label_fmt, self._label_text)

        top_pad, bottom_pad, reserved_left, _reserved_right = self._vertical_content_footprint(
            rc, vmin, vmax, bar_w, bar_h
        )
        point_w = self._end_point_size(rc, bar_h, bar_w)

        bar_y0 = frame.top() + top_pad
        bar_y1 = bar_y0 + bar_h
        bar_x0 = frame.left() + reserved_left
        bar_x1 = bar_x0 + bar_w

        bar_rect = QRectF(bar_x0, bar_y0, bar_w, bar_h)
        radius_px = self._mm(rc, self._corner_radius) if self._rounded else 0.0
        if radius_px > 0:
            radius_px = min(radius_px, bar_h / 2.0, bar_w / 2.0)

        discrete = self._bar_style == BAR_DISCRETE
        fill_colors = self._discrete_block_colors(colors) if discrete else colors
        n = len(fill_colors)
        seg_h = bar_h / n
        painter.save()
        if radius_px > 0:
            clip_path = QPainterPath()
            clip_path.addRoundedRect(bar_rect, radius_px, radius_px)
            painter.setClipPath(clip_path)
        # top of the bar = vmax, bottom = vmin (thermometer style)
        for i, c in enumerate(fill_colors):
            y = bar_y1 - (i + 1) * seg_h
            painter.setBrush(c)
            if discrete:
                pen = QPen(QColor(40, 40, 40))
                pen.setWidthF(self._mm(rc, 0.15))
                painter.setPen(pen)
            else:
                painter.setPen(Qt.NoPen)
            painter.drawRect(QRectF(bar_x0, y, bar_w, seg_h + (0.0 if discrete else 0.75)))
        painter.restore()

        if top_pad:
            tri = QPolygonF([QPointF(bar_x0, bar_y0), QPointF(bar_x1, bar_y0),
                              QPointF((bar_x0 + bar_x1) / 2, bar_y0 - point_w)])
            painter.setBrush(fill_colors[-1])
            painter.drawPolygon(tri)
        if bottom_pad:
            tri = QPolygonF([QPointF(bar_x0, bar_y1), QPointF(bar_x1, bar_y1),
                              QPointF((bar_x0 + bar_x1) / 2, bar_y1 + point_w)])
            painter.setBrush(fill_colors[0])
            painter.drawPolygon(tri)

        painter.setPen(QColor(40, 40, 40))
        painter.setBrush(Qt.NoBrush)
        if radius_px > 0:
            painter.drawRoundedRect(bar_rect, radius_px, radius_px)
        else:
            painter.drawRect(bar_rect)

        for i in range(self._tick_count):
            t = i / (self._tick_count - 1)
            val = vmin + (vmax - vmin) * t
            y = bar_y1 - t * bar_h
            if inside:
                pen = QPen(QColor(255, 255, 255))
                pen.setWidthF(tick_pen_w)
                painter.setPen(pen)
                painter.drawLine(QPointF(bar_x1, y), QPointF(bar_x1 - min(tick_len, bar_w), y))
            else:
                pen = QPen(self._tick_color)
                pen.setWidthF(tick_pen_w)
                painter.setPen(pen)
                painter.drawLine(QPointF(bar_x1, y), QPointF(bar_x1 + tick_len, y))
            txt = "{:.{}f}".format(val, self._decimals)
            label_x = bar_x1 + (tick_len if not inside else 0.0) + self._mm(rc, 0.5)
            # Use the QRectF overload (with a real vAlignment) instead of
            # the QPointF one: that overload's point is treated as the
            # text BASELINE, not its vertical center or top, so
            # hand-nudging the anchor (e.g. "y - tick_h/2") only ever
            # approximates centering and drifts the whole label column up
            # relative to its tick marks. A rect + AlignVCenter lets
            # QGIS's own metrics center it exactly on the tick's y.
            label_box_h = max(tick_h * 1.8, self._mm(rc, 3.0))
            label_rect = QRectF(label_x, y - label_box_h / 2.0, frame.right() - label_x, label_box_h)
            QgsTextRenderer.drawText(
                label_rect, 0, QgsTextRenderer.AlignLeft, [txt], rc, tick_fmt,
                True, QgsTextRenderer.AlignVCenter,
            )

        if self._label_text:
            # Same fix as the tick labels above: the QPointF overload of
            # drawText() takes no vAlignment/drawAsOutlines pair, so those
            # two extra args are dropped. AlignCenter here is the
            # *horizontal* alignment along the rotated (-90°) reading
            # direction, so with anchor_y already at the bar's vertical
            # midpoint this still centers the label along the bar's length.
            anchor_x = frame.left() + self._mm(rc, self._label_padding) + label_h / 2.0
            anchor_y = (bar_y0 + bar_y1) / 2.0
            QgsTextRenderer.drawText(
                QPointF(anchor_x, anchor_y), -90, QgsTextRenderer.AlignCenter,
                [self._label_text], rc, label_fmt,
            )

    # ---- persistence (so it survives saving/reopening the .qgz/.qgs) ---
    def writePropertiesToElement(self, element, document, context):
        ok = super().writePropertiesToElement(element, document, context)
        # BAR geometry (mm) is the authoritative, user-facing size and is
        # saved explicitly — the FRAME size (self.rect(), saved separately
        # by the base class) is treated as derived/rebuildable and is never
        # relied on as the source of truth for the bar on reload.
        element.setAttribute("qrvizBarWidth", self._bar_width_mm)
        element.setAttribute("qrvizBarHeight", self._bar_height_mm)
        element.setAttribute("qrvizLayerId", self._layer_id or "")
        element.setAttribute("qrvizOrientation", self._orientation)
        element.setAttribute("qrvizTickCount", self._tick_count)
        element.setAttribute("qrvizDecimals", self._decimals)
        element.setAttribute("qrvizEndStyle", self._end_style)
        element.setAttribute("qrvizLabelText", self._label_text)
        element.setAttribute("qrvizLabelSize", self._label_size)
        element.setAttribute("qrvizLabelBold", int(self._label_bold))
        element.setAttribute("qrvizLabelItalic", int(self._label_italic))
        element.setAttribute("qrvizTickSize", self._tick_size)
        element.setAttribute("qrvizTickBold", int(self._tick_bold))
        element.setAttribute("qrvizTickItalic", int(self._tick_italic))
        element.setAttribute("qrvizColorSteps", self._color_steps)
        element.setAttribute("qrvizBarStyle", self._bar_style)
        element.setAttribute("qrvizReverse", int(self._reverse))
        element.setAttribute("qrvizTickPosition", self._tick_position)
        element.setAttribute("qrvizRounded", int(self._rounded))
        element.setAttribute("qrvizCornerRadius", self._corner_radius)
        element.setAttribute("qrvizLabelPadding", self._label_padding)
        element.setAttribute("qrvizTickPadding", self._tick_padding)
        # qrvizTextLight is kept (derived) purely so a project saved with
        # this version can still be opened by an older copy of the plugin
        # and show *something* sensible instead of erroring out.
        element.setAttribute("qrvizTextLight", int(self.textLight()))
        element.setAttribute("qrvizTickColor", self._tick_color.name(QColor.HexArgb))
        element.setAttribute("qrvizLabelColor", self._label_color.name(QColor.HexArgb))
        element.setAttribute("qrvizTickFontFamily", self._tick_font_family)
        element.setAttribute("qrvizLabelFontFamily", self._label_font_family)
        return ok

    def readPropertiesFromElement(self, element, document, context):
        ok = super().readPropertiesFromElement(element, document, context)
        # Restore BAR dimensions first (see writePropertiesToElement). Older
        # project files saved before this refactor won't have these
        # attributes; for those, fall back to whatever frame size the base
        # class just restored, as a one-time best-effort migration — it will
        # only ever be used as a *starting point*, since the frame is
        # recalculated as derived geometry below via refreshData().
        legacy_rect = self.rect()
        self._bar_width_mm = float(element.attribute("qrvizBarWidth")) if element.hasAttribute("qrvizBarWidth") \
            else legacy_rect.width()
        self._bar_height_mm = float(element.attribute("qrvizBarHeight")) if element.hasAttribute("qrvizBarHeight") \
            else legacy_rect.height()
        self._layer_id = element.attribute("qrvizLayerId", "") or None
        self._orientation = element.attribute("qrvizOrientation", "horizontal")
        self._tick_count = int(element.attribute("qrvizTickCount", "5"))
        self._decimals = int(element.attribute("qrvizDecimals", "2"))
        self._end_style = int(element.attribute("qrvizEndStyle", "0"))
        self._label_text = element.attribute("qrvizLabelText", "")
        self._label_size = int(element.attribute("qrvizLabelSize", "10"))
        self._label_bold = bool(int(element.attribute("qrvizLabelBold", "1")))
        self._label_italic = bool(int(element.attribute("qrvizLabelItalic", "0")))
        self._tick_size = int(element.attribute("qrvizTickSize", "9"))
        self._tick_bold = bool(int(element.attribute("qrvizTickBold", "0")))
        self._tick_italic = bool(int(element.attribute("qrvizTickItalic", "0")))
        self._color_steps = int(element.attribute("qrvizColorSteps", "64"))
        self._bar_style = element.attribute("qrvizBarStyle", BAR_CONTINUOUS)
        if self._bar_style not in (BAR_CONTINUOUS, BAR_DISCRETE):
            self._bar_style = BAR_CONTINUOUS
        self._reverse = bool(int(element.attribute("qrvizReverse", "0")))
        self._tick_position = element.attribute("qrvizTickPosition", TICK_OUTSIDE)
        self._rounded = bool(int(element.attribute("qrvizRounded", "0")))
        self._corner_radius = float(element.attribute("qrvizCornerRadius", "2.0"))
        self._label_padding = float(element.attribute("qrvizLabelPadding", "3.0"))
        # Fallback default matches the __init__ default (0.0 mm) — keep
        # both in sync, so a fresh item and a reloaded item with no saved
        # value start with the same tick padding.
        self._tick_padding = float(element.attribute("qrvizTickPadding", "0.0"))
        # Colour: prefer the new per-tick/per-label QColor attributes; fall
        # back to migrating the old black/white-only qrvizTextLight flag
        # for projects saved before this version existed.
        legacy_light = bool(int(element.attribute("qrvizTextLight", "0")))
        legacy_color = "#ffffffff" if legacy_light else "#ff141414"
        self._tick_color = QColor(element.attribute("qrvizTickColor", legacy_color))
        self._label_color = QColor(element.attribute("qrvizLabelColor", legacy_color))
        self._tick_font_family = element.attribute("qrvizTickFontFamily", "")
        self._label_font_family = element.attribute("qrvizLabelFontFamily", "")
        self._connect_layer()
        self.refreshData()
        return ok


class QRVizColorbarItemMetadata(QgsLayoutItemAbstractMetadata):
    """Core (non-GUI) registration — needed so the item type is known to
    QgsLayoutItemRegistry for serialization/rendering even headlessly."""

    def __init__(self):
        super().__init__(QRVIZ_COLORBAR_ITEM_TYPE, "RasterViz")

    def createItem(self, layout):
        return QRVizColorbarItem(layout)
