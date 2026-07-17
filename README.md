

<img src="icon.png" alt="RasterViz Icon" width="80"/>

# **RasterViz** — QGIS Plugin

> 💡 **Background:** RasterViz started life as this QGIS plugin, was extended into a **[standalone desktop edition](https://github.com/Defani/RasterViz)** with a heavier feature set (multi-layer panel, vector overlays, web basemaps), and has now had that full feature set folded back into the QGIS plugin — so everything the standalone app can do, you now get natively inside QGIS, on top of your existing project layers.

![QGIS](https://img.shields.io/badge/QGIS-3.0%2B-589632?logo=qgis&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)
![PyQt](https://img.shields.io/badge/GUI-qgis.PyQt-41CD52?logo=qt&logoColor=white)
![Matplotlib](https://img.shields.io/badge/Matplotlib-11557c?logo=python&logoColor=white)
![GeoPandas](https://img.shields.io/badge/GeoPandas-139C5A?logo=pandas&logoColor=white)
![Rasterio](https://img.shields.io/badge/Rasterio-0052CC?logo=osgeo&logoColor=white)
![Contextily](https://img.shields.io/badge/Contextily-Basemaps-orange)
![Fiona](https://img.shields.io/badge/Fiona-Vector_I%2FO-yellow)
![QtAwesome](https://img.shields.io/badge/QtAwesome-Icons-EA4335)
![License](https://img.shields.io/badge/License-GPLv2%2B-blue)

**RasterViz** is a publication-quality raster & vector visualization plugin for QGIS, styled after `rasterio.show()`. Pick a colormap, choose a stretch, drop a legend, overlay vector data, add a web basemap, and export a print-ready figure — all through an interactive dialog docked right next to your QGIS project, with live preview at every step.

<img width="1920" height="1080" alt="RasterViz screenshot" src="https://github.com/user-attachments/assets/700d178c-47ea-484b-a7be-5b57b89a040b" />

---

## ✨ Key Features

* **🗂️ Interactive Layer Manager:** Manage any number of raster and vector layers in one panel — mix layers already in your QGIS project with ones opened directly from disk.
* **🛰️ Raster Rendering:** Single-band rendering (continuous & discrete/classified with per-class color, label, and decimals) plus RGB composite with independent per-band stretch.
* **🎨 Colormap Library:** Custom scientific palettes (NDVI, LST, mangrove/carbon stock, SAR backscatter, and more) alongside the full Matplotlib colormap set, with percentile / min-max / manual stretch.
* **🗺️ Vector Symbology:** Render Shapefiles, KML/KMZ, and GeoPackages with customizable fill, stroke, and categorized (by-field) colors.
* **🌐 Dynamic Basemaps:** Instantly overlay your data on Esri, OpenStreetMap, CartoDB, OpenTopoMap, NASA GIBS, and more, drawn behind your layers.
* **📐 Cartographic Tools:** Coordinate grid with DMS / DM / Decimal Degree / UTM tick formatting, scale bar, and a dynamic north arrow.
* **🖼️ Layout / Multi-Map Series:** Compose several maps together into one figure panel.
* **🌙 Dark / Light Mode:** Eye-friendly UI themes tailored for long hours of spatial analysis.
* **📸 High-Quality Export:** Save your map compositions as high-resolution PNG (300 DPI), TIFF, SVG, or PDF files.

---

## 📋 Requirements

* **QGIS 3.0 – 3.99**, with its bundled Python 3.
* Core raster/vector rendering works out of the box using QGIS's own Python environment — nothing extra to install for basic use.
* A few optional packages unlock the features noted below; install them into QGIS's own Python (see **Installing optional dependencies**):

  | Package | Unlocks |
  |---|---|
  | `contextily` | Web basemaps (Esri, OSM, CartoDB, etc.) |
  | `qtawesome` | Nicer toolbar / layer-panel icons (cosmetic only) |

RasterViz checks for these at startup and shows a one-time notice listing anything missing — every other feature keeps working normally either way.

---

## 🚀 Installation

1. Download the latest `qrviz-x.y.z.zip` release.
2. In QGIS: **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Select the downloaded zip and click **Install Plugin**.
4. Open it from **Raster menu → RasterViz**, or the toolbar icon.

### Installing optional dependencies

Open the **OSGeo4W Shell** (Windows) or a terminal using QGIS's own Python (Linux/macOS), then run:

```bash
pip install contextily qtawesome
```

Restart QGIS afterwards. See `requirements.txt` inside the plugin folder for platform-specific notes.

---

## 🖱️ Quick Start

1. Load a raster from your QGIS project, or click **+ Raster** inside the RasterViz dialog to open one from disk.
2. Choose single-band, discrete, or RGB mode, then pick a colormap and stretch.
3. Optionally add a vector overlay (**+ Vector**) and a basemap from the **Basemap** dropdown.
4. Adjust colorbar, grid, coordinate format, north arrow, and scale bar — the preview updates live.
5. Click **Export** to save PNG / SVG / TIFF / PDF at publication resolution.

---

## 🧭 Project History

RasterViz began as this QGIS plugin, was rebuilt as a **[standalone desktop edition](https://github.com/Defani/RasterViz)** to run without QGIS installed (adding the multi-layer panel, vector overlays, dynamic basemaps, and dark/light theming), and has now had that complete feature set brought back into the QGIS plugin — so both editions share the same rendering engine and feature parity going forward.

---

## 🙏 Acknowledgments & Credits

This plugin exists because of the monumental efforts of the open-source GIS and Python community. A massive thank you to the developers and maintainers of the following projects:

* **[QGIS](https://qgis.org/)**: The desktop GIS platform this plugin is built for and built on.
* **[Matplotlib](https://matplotlib.org/)**: The backbone of our map rendering, turning raw arrays into beautiful cartography.
* **[Rasterio](https://rasterio.readthedocs.io/)** & **[GDAL](https://gdal.org/)**: For fast, efficient, and Pythonic reading of geospatial raster data.
* **[GeoPandas](https://geopandas.org/)** & **[Fiona](https://fiona.readthedocs.io/)**: For making vector data manipulation and rendering incredibly intuitive.
* **[Contextily](https://contextily.readthedocs.io/)**: For seamlessly retrieving and plotting web map tiles (basemaps).
* **[NumPy](https://numpy.org/)**: For the blazingly fast array operations required in image processing.
* **[QtAwesome](https://github.com/spyder-ide/qtawesome)**: For providing beautiful, scalable UI icons.

*Special thanks to the global open-source community for continuing to democratize geospatial technology.*

---

**Author:** Defani Arman Alfitriansyah — defaniarman@gmail.com
**Repository:** https://github.com/Defani/QRasterVIZ
**License:** GNU GPL v2 or later — see `LICENSE`.
## License

GNU General Public License v3.0 or later. See [LICENSE](LICENSE).

Matplotlib and NumPy are distributed under the BSD License. PyQGIS and PyQt5 are distributed under the GNU GPL v2.

---

## Citation

```
Alfitriansyah, D. A. (2026). RasterViz: Scientific Raster Visualization Plugin for QGIS
(Version 1.1.0) [Software]. https://github.com/Defani/RasterViz
```

---

<div align="center">

Built with **PyQGIS · NumPy · Matplotlib · PyQt5**

[github.com/Defani/RasterViz](https://github.com/Defani/RasterViz)

</div>
