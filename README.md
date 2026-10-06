# 🛰️ IntegraCar — Multi-Temporal Spatial Extraction Pipeline

Automated geospatial pipeline engineered to extract synchronized, georeferenced high-resolution satellite imagery and thematic land use/land cover (LULC) classification maps from the **GeoBases do Espírito Santo** spatial data infrastructure for rural properties registered within the CAR (*Cadastro Ambiental Rural*).

The pipeline supports multi-temporal extraction across two distinct historical epochs, generating paired GeoTIFF raster products with synchronized spatial metadata (EPSG:4326):

- **2019–2020 Epoch:**
  - **SATELLITE:** High-resolution orthophotomosaic (KOMPSAT-3/3A, 2019–2020) via OGC WMS.
  - **SEGMENTED:** Land use and land cover (LULC) thematic map (IJSN, 2019) via OGC WMS.

- **2012–2015 Epoch:**
  - **SATELLITE:** High-resolution aerial orthophotomosaic (IEMA, 0.25 m GSD, 2012–2015) via OGC WMS.
  - **SEGMENTED:** Authoritative GeoBases vector shapefile (`Mapeamento_Uso_Cobertura_Vegetal_2012`) rasterized locally on-the-fly with spatial indexing and exact 2019–2020 RGB palette harmonization.

- **Multi-Temporal Extraction (`--period both`):**
  - Concurrently extracts all four raster products for each coordinate pair, guaranteeing 100% spatial parity (identical bounding box extents, coordinate reference systems, pixel grid dimensions, and Ground Sampling Distance).

---

## Technical Extraction Methodology

### 1. How the 2019–2020 Dataset is Downloaded
```
[Centroid (x, y)] ──► [pyproj: UTM -> EPSG:4326 BBox]
                            │
       ┌────────────────────┴────────────────────┐
       ▼                                         ▼
[WMS GetMap: KOMPSAT-3]              [WMS GetMap: IJSN LULC]
       │                                         │
[Pillow: In-Memory Decode]           [Pillow: In-Memory Decode]
       │                                         │
[rasterio: GeoTIFF Export]           [rasterio: GeoTIFF Export]
```
- **Satellite Layer:** Retrieved asynchronously from the GeoBases GeoServer WMS endpoint (`https://ide.geobases.es.gov.br/geoserver/ows`, version 1.3.0) using the layer typename `geonode:ijsn-ortofotomosaico-es-kompsat-3-3a-2019-2020`.
- **LULC Thematic Layer:** Retrieved from the same WMS endpoint using the layer typename `geonode:ijsn_map_uso_solo_es_2019_20200`.
- **Ingestion & Georeferencing:** Binary PNG streams are received over asynchronous HTTP (`aiohttp`), decoded in memory via `Pillow`, converted to 3-band NumPy tensors, mapped to geodetic bounds using affine transformation matrices (`from_bounds`), and persisted as GeoTIFF files with LZW compression (`rasterio`).

---

### 2. How the 2012–2015 Dataset is Retrieved & Rasterized
```
[Centroid (x, y)] ──► [pyproj: UTM -> EPSG:4326 BBox]
                            │
       ┌────────────────────┴────────────────────────┐
       ▼                                             ▼
[WMS GetMap: IEMA 0.25m]             [GeoBases S3 / Local Cache: 2012 Shapefile]
       │                                             │
[Pillow: In-Memory Decode]           [GeoPandas: Spatial Query (rtree BBox Clip)]
       │                                             │
       │                             [rasterio.features.rasterize: Geometry -> Grid]
       │                                             │
       │                             [NumPy: Map Class IDs -> Official RGB Palette]
       │                                             │
[rasterio: GeoTIFF Export]           [rasterio: GeoTIFF Export (Identical Transform)]
```
- **Satellite Layer:** Retrieved from GeoBases GeoServer WMS endpoint using the high-resolution aerial survey layer `geonode:iema_ortofotomosaico_es_025m_2012-2015` in EPSG:4326.
- **LULC Thematic Layer:** Because high-resolution 2012–2015 thematic maps are served authoritatively as vector geometries rather than tiled WMS rasters, the pipeline employs a local rasterization engine:
  1. **Archive Ingestion & Caching:** The official shapefile package (`MAP_ES_2012_2015_USO_COBERTURA_VEGETAL_2012-2015.zip`) is automatically streamed from the official GeoBases Amazon S3 repository, extracted into `temp_shp_2012/`, and cached for subsequent runs.
  2. **Spatial Indexing & Reprojection:** The shapefile is ingested into memory using `geopandas` and reprojected to EPSG:4326. An R-tree spatial index (`.cx[minx:maxx, miny:maxy]`) extracts intersecting polygons in under a millisecond.
  3. **Affine Grid Rasterization:** Polygons are burned into a 2D integer grid matching the exact target dimensions (`width` × `height`) using `rasterio.features.rasterize()` and the computed affine transform matrix.
  4. **Color Palette Standardization:** Integer class IDs are converted into an 8-bit 3-band RGB matrix using the official GeoBases color table (`LULC_COLOR_PALETTE`), ensuring pixel-level visual and semantic consistency with the 2019–2020 WMS color scheme.
  5. **GeoTIFF Encoding:** The resulting tensor is saved as an EPSG:4326 GeoTIFF with LZW compression and `photometric="RGB"` tags.

---

## Prerequisites

- **Python 3.8+** installed with system variables configured in `PATH`.
- A CSV file containing property spatial coordinates (see specification below).

---

## Installation and Setup

### Step 1: Clone the Repository
```bash
git clone https://github.com/integraCAR/car-images-downloader.git
cd car-images-downloader
```

### Step 2: Configure Virtual Environment (Recommended)

**On Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**On Linux/macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Required Dependencies
```bash
pip install -r requirements.txt
```

---

## Command-Line Usage and Execution Recipes

The CLI entrypoint is `extractor.py`.

### 1. Standard Extraction (2019–2020 Epoch)
Extracts 2019–2020 scenes with default 2048 × 2048 px resolution (1.0 m/pixel GSD):
```bash
python extractor.py --csv sample_train_coordinates.csv --output ./output
```

### 2. Historical Extraction (2012–2015 Epoch)
Extracts 2012–2015 orthomosaics and rasterized LULC maps:
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output \
  --period 2012-2015
```

### 3. Simultaneous Multi-Temporal Extraction (`--period both`)
Extracts synchronized pairs for both historical epochs simultaneously:
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output \
  --period both
```

### 4. Rapid Verification Smoke Test
Restricts processing to the first 10 coordinate records:
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output \
  --period both \
  --limit 10
```

### 5. Custom Spatial Extent and Resolution
Defines a 512 m buffer (1024 m × 1024 m window) at 1024 × 1024 px resolution (1.0 m/px):
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output \
  --period both \
  --buffer 512 \
  --width 1024 \
  --height 1024
```

### 6. Using a Pre-Downloaded Local Shapefile
Bypasses remote S3 download by providing an explicit path to an existing shapefile:
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output \
  --period 2012-2015 \
  --shapefile ./temp_shp_2012/USO_COBERTURA_VEGETAL_2012-2015/Mapeamento_Uso_Cobertura_Vegetal_2012.shp
```

---

## Command-Line Parameters Reference

| Parameter | Type | Required | Default | Description |
|---|---|:---:|---|---|
| `--csv FILE_PATH` | string | ✅ Yes | — | Path to the input CSV coordinate file. Semicolon delimiter (`;`). Columns: `property_id` (or `cod_imovel`), `x`, `y` in EPSG:31984. |
| `--output DIR_PATH` | string | ✅ Yes | — | Destination directory path where raster subdirectories are created. |
| `--period EPOCH` | string | Optional | `2019-2020` | Temporal epoch: `2019-2020`, `2012-2015`, or `both`. |
| `--shapefile PATH` | string | Optional | `None` *(auto)* | Explicit local path to 2012–2015 LULC shapefile (`.shp`) or containing directory. If omitted, checks local cache or downloads from S3. |
| `--flat` | boolean | Optional | `False` | When extracting a single period, saves rasters directly into `<output>/SATELLITE` and `SEGMENTED` instead of epoch subfolders. |
| `--buffer METERS` | integer | Optional | `1024` | Half-side radius in meters around each centroid coordinate. Defines a square window of side length `2 * buffer` meters. |
| `--width PIXELS` | integer | Optional | `2048` | Horizontal raster resolution in pixels for exported GeoTIFF images. |
| `--height PIXELS` | integer | Optional | `2048` | Vertical raster resolution in pixels for exported GeoTIFF images. |
| `--limit N` | integer | Optional | `None` *(all)* | Caps extraction to the first N coordinates of the input CSV. |
| `--workers N` | integer | Optional | `4` | Maximum number of simultaneous asynchronous worker tasks. |

### Parameter Aliases
- `--output`: `-o`, `--path`, `--caminho`
- `--period`: `--year`, `--ano`, `--periodo`
- `--shapefile`: `--shp`
- `--width`: `--largura`
- `--height`: `--altura`
- `--limit`: `--count`, `--qtd`

---

## Spatial Resolution and Ground Sampling Distance (GSD)

The resulting spatial resolution (GSD) per pixel is determined by the spatial buffer radius and pixel dimensions:

$$\text{GSD} = \frac{2 \times \text{buffer}}{\text{width}} \quad [\text{meters per pixel}]$$

| Buffer (`--buffer`) | Spatial Window Side | Pixel Size (`--width` × `--height`) | Resulting GSD | Recommended Use Case |
|---|---|---|---|---|
| `1024` m | 2,048 m × 2,048 m (~2.0 km) | `2048` × `2048` px | **1.0 m / px** | **Default:** High-detail property canopy analysis |
| `1024` m | 2,048 m × 2,048 m (~2.0 km) | `1024` × `1024` px | **2.0 m / px** | Standard regional property context |
| `512` m | 1,024 m × 1,024 m (~1.0 km) | `1024` × `1024` px | **1.0 m / px** | Localized high-resolution analysis |
| `512` m | 1,024 m × 1,024 m (~1.0 km) | `512` × `512` px | **2.0 m / px** | Lightweight prototyping |

---

## Directory Hierarchy

When `--period both` is executed:
```
<--output>/
├── 2012_2015/
│   ├── SATELLITE/
│   │   ├── sample_1.tif
│   │   └── ...
│   └── SEGMENTED/
│       ├── sample_1.tif
│       └── ...
└── 2019_2020/
    ├── SATELLITE/
    │   ├── sample_1.tif
    │   └── ...
    └── SEGMENTED/
        ├── sample_1.tif
        └── ...

artifacts/
└── dataset_manifest.csv   ← Synchronized audit manifest tracking every extracted sample

logs/
└── execution.log          ← Comprehensive operational log
```

---

## Dataset Manifest Schema

Every extracted scene is appended to `artifacts/dataset_manifest.csv` with the following schema:

| Column | Description |
|---|---|
| `sample_id` | Sequential sample integer index (1, 2, 3, ...) |
| `property_id` | Rural property identifier code in the CAR registry |
| `period` | Temporal epoch of the extracted scene (`2019-2020` or `2012-2015`) |
| `x`, `y` | Centroid coordinates in planar UTM meters (EPSG:31984) |
| `bbox_xmin`, `ymin`, `xmax`, `ymax` | Bounding box spatial extents in decimal degrees (EPSG:4326) |
| `satellite_status` | Satellite scene status flag (`ok`, `error`, `empty`) |
| `land_cover_status` | Land cover scene status flag (`ok`, `error`, `empty`) |
| `download_timestamp` | ISO 8601 timestamp of extraction completion |

---

## Input CSV Specification

Delimiter: **semicolon** (`;`)

| Column Name | Data Type | Description |
|---|---|---|
| `property_id` *(or `cod_imovel`)* | string | Unique Rural Environmental Registry (CAR) property code |
| `x` | float | Centroid X coordinate in planar projection meters (EPSG:31984 — UTM Zone 24S) |
| `y` | float | Centroid Y coordinate in planar projection meters (EPSG:31984) |
