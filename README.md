# 🛰️ IntegraCar — Satellite Image Extraction Pipeline

Automated pipeline designed to download georeferenced high-resolution satellite imagery and thematic land use/land cover classification maps from the **GeoBases do Espírito Santo** spatial data infrastructure for rural properties registered within the CAR (Rural Environmental Registry — *Cadastro Ambiental Rural*).

For each geographic coordinate listed in an input CSV dataset, the pipeline generates two synchronized, georeferenced GeoTIFF scenes:

- **SATELLITE** — Raw orthophotomosaic (KOMPSAT 2019–2020)
- **SEGMENTED** — Land use and land cover (LULC) classification map (IJSN 2019)

---

## Prerequisites

- **Python 3.8+** installed with environment variables configured in `PATH`.
- A CSV file containing property spatial coordinates (see specification below).

---

## Installation and Execution

### Step 1: Clone the repository
Open a terminal shell and clone the project repository:
```bash
git clone https://github.com/integraCAR/car-images-downloader.git
cd car-images-downloader
```

### Step 2: Configure a Virtual Environment (Recommended)
To isolate project dependencies and avoid environment conflicts, initialize and activate a virtual environment:

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
With the virtual environment active, install the runtime packages:
```bash
pip install -r requirements.txt
```

### Step 4: Execute the Extraction Pipeline
Execute `extractor.py` by supplying the input CSV dataset and the target output directory.

**Standard execution:**
```bash
python extractor.py --csv sample_train_coordinates.csv --output ./output
```

**Custom execution with parameter overrides:**
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output \
  --buffer 1024 \
  --width 1024 \
  --height 1024 \
  --limit 1000
```
*(Note: On Windows PowerShell, multi-line backtick `` ` `` or single-line commands are recommended if backslash line continuations are unsupported).*

**To display command-line help:**
```bash
python extractor.py --help
```

---

## Command-Line Parameters Guide

All execution parameters can be specified via command-line arguments when invoking `extractor.py`. When an optional parameter is omitted, the pipeline automatically applies the default constant defined in `config.py`.

### Parameter Reference

| Parameter | Type | Required | Default | Description |
|---|---|:---:|---|---|
| `--csv FILE_PATH` | string | ✅ Yes | — | Absolute or relative path to the input CSV file containing spatial coordinates. Expected delimiter: `;`. Columns: `property_id` (or `cod_imovel`), `x`, `y`. |
| `--output DIR_PATH` | string | ✅ Yes | — | Target directory path where raster subdirectories (`SATELLITE/` and `SEGMENTED/`) are created. Created automatically if missing. |
| `--buffer METERS` | integer | Optional | `1024` | Half-side radius in meters around each centroid coordinate. Defines a square spatial window of side length `2 * buffer` meters. |
| `--width PIXELS` | integer | Optional | `1024` | Horizontal raster resolution in pixels for both output GeoTIFF images. |
| `--height PIXELS` | integer | Optional | `1024` | Vertical raster resolution in pixels for both output GeoTIFF images. |
| `--limit N` | integer | Optional | `None` *(all)* | Caps extraction to the first N coordinates of the input dataset. Useful for smoke tests and small experimental batches. |
| `--workers N` | integer | Optional | `4` | Maximum number of concurrent asynchronous extraction workers fetching tiles simultaneously. |

### Backward-Compatible Aliases

For convenience and compatibility across legacy workflows, the following parameter aliases are fully supported:

- `--output`: `-o`, `--path`, `--caminho`
- `--width`: `--largura`
- `--height`: `--altura`
- `--limit`: `--count`, `--qtd`

---

### Spatial Resolution & Ground Sampling Distance (GSD)

The combination of `--buffer` (geographic metric extent) and `--width`/`--height` (pixel grid dimension) dictates the resulting **Ground Sampling Distance (GSD)** or spatial resolution per pixel:

$$\text{Spatial Resolution (GSD)} = \frac{2 \times \text{buffer}}{\text{width}} \quad [\text{meters per pixel}]$$

#### Common Configurations:

| Buffer (`--buffer`) | Bounding Box Dimensions | Pixel Size (`--width` × `--height`) | Resulting Spatial Resolution (GSD) | Recommended Use Case |
|---|---|---|---|---|
| `1024` m | 2,048 m × 2,048 m (~2.0 km) | `1024` × `1024` px | **2.0 m / pixel** | **Default:** Standard regional property context |
| `512` m | 1,024 m × 1,024 m (~1.0 km) | `512` × `512` px | **2.0 m / pixel** | Lightweight testing & rapid prototyping |
| `512` m | 1,024 m × 1,024 m (~1.0 km) | `1024` × `1024` px | **1.0 m / pixel** | Higher spatial detail for localized canopy analysis |
| `2048` m | 4,096 m × 4,096 m (~4.1 km) | `1024` × `1024` px | **4.0 m / pixel** | Macro-landscape and watershed overview |

---

### Practical Execution Recipes

#### 1. Smoke Test / Verification Batch (10 coordinates)
To quickly verify that the remote WMS server is responding and credentials/layers are operational without downloading the entire dataset:
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output_test \
  --limit 10
```

#### 2. Standard Production Extraction
Processes the complete coordinate dataset using the default 2,048 m × 2,048 m bounding box and 1,024 × 1,024 pixel grid:
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output
```

#### 3. High-Detail Localized Extraction (1.0 m/px)
Extracts a tighter 1 km² area around each farm centroid at 1.0 meter/pixel resolution:
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output_highres \
  --buffer 512 \
  --width 1024 \
  --height 1024
```

#### 4. Throttled Concurrency for Unstable Connections
If the upstream WMS server experiences latency spikes, HTTP 504 gateway timeouts, or connection resets, reduce concurrency to 1 or 2 workers:
```bash
python extractor.py \
  --csv sample_train_coordinates.csv \
  --output ./output \
  --workers 2
```

---

## Directory and Output Structure

```
<--output>/
├── SATELLITE/
│   ├── sample_1.tif
│   ├── sample_2.tif
│   └── ...
└── SEGMENTED/
    ├── sample_1.tif
    ├── sample_2.tif
    └── ...

artifacts/
└── dataset_manifest.csv   ← Real-time status manifest of each extracted coordinate

logs/
└── execution.log          ← Comprehensive operational execution log
```

---

## Pipeline Architecture and Execution Flow

The extraction pipeline consists of **six sequential phases**, orchestrated by `extractor.py`. Within each coordinate batch, requests are dispatched asynchronously.

---

### Step 1 — Ingestion of Spatial Coordinates

> **Module:** `extractor.py` → `run_pipeline_async()`  
> **Dependency:** `pandas`

The pipeline parses the semicolon-delimited CSV specified via `--csv`. The file defines one rural property per line, detailing its unique identifier code and planar UTM coordinates.

```python
dataframe = pd.read_csv(cfg["csv_file"], sep=cfg["csv_separator"])
```

The table is converted into an in-memory `pandas.DataFrame`. If `--limit` is specified, the dataset is truncated immediately using `.head(N)` to avoid extraneous coordinate allocations.

---

### Step 2 — Coordinate Reference System Transformation (UTM → Lat/Lon)

> **Module:** `utils/wms.py` → `calculate_bbox_latlon()`  
> **Dependency:** `pyproj`

Coordinate records originate in planar spatial units: **EPSG:31984** (SIRGAS 2000 / UTM Zone 24S in meters). The GeoBases OGC WMS service requires bounds in geographic coordinate degrees: **EPSG:4326** (WGS 84).

```python
transformer = Transformer.from_crs("EPSG:31984", "EPSG:4326", always_xy=True)
lon_min, lat_min = transformer.transform(xmin_proj, ymin_proj)
lon_max, lat_max = transformer.transform(xmax_proj, ymax_proj)
```

The `pyproj` library applies geodetic transformations. Given a centroid point `(x, y)` and a buffer distance in meters, the algorithm constructs an orthogonal square envelope in projected space, transforming the vertices into geographic bounding box extents `(minx_lon, miny_lat, maxx_lon, maxy_lat)`.

Cached transformer instances are shared across calls to eliminate instantiation overhead.

---

### Step 3 — Service Validation via OGC WMS Protocol

> **Module:** `utils/wms.py` → `connect_wms()` and `validate_layer()`  
> **Dependency:** `OWSLib`

Before initiating image retrieval, the pipeline initiates a handshake with the GeoBases WMS server to confirm service health and layer existence.

```python
wms = WebMapService("https://ide.geobases.es.gov.br/geoserver/ows", version="1.3.0")
```

`OWSLib` executes the initial `GetCapabilities` query, parsing the service schema. The pipeline verifies that the requested layer typenames (`satellite_layer` and `land_cover_layer`) are present in the catalog.

---

### Step 4 — Asynchronous Parallel Image Retrieval

> **Module:** `utils/wms.py` → `request_wms_image_async()` and `download_image_async()`  
> **Dependencies:** `aiohttp`, `asyncio`

For every coordinate, two raster products must be fetched concurrently: the orthophotomosaic and the thematic classification map.

```python
# Concurrently fetch both raster layers for a given coordinate
satellite_status, land_cover_status = await asyncio.gather(
    _download_single_image_async(session, cfg, cfg["satellite_layer"], bbox, satellite_path),
    _download_single_image_async(session, cfg, cfg["land_cover_layer"], bbox, segmented_path),
)
```

**Key concurrency mechanisms:**

- **`asyncio`**: Coordinates non-blocking event-loop routines.
- **`aiohttp`**: Manages persistent connection pools (`TCPConnector`), reusing active TCP channels and mitigating repeated SSL/TLS handshakes.
- **`asyncio.Semaphore`**: Regulates concurrent connections according to `--workers` to protect remote WMS infrastructure from load throttling.
- **`asyncio.gather`**: Dispatches requests for SATELLITE and SEGMENTED tiles simultaneously.

Automatic retry logic handles transient timeouts or connection resets (up to 3 attempts with exponential/configurable delays).

---

### Step 5 — Raster Encoding and GeoTIFF Georeferencing

> **Module:** `utils/wms.py` → `save_as_geotiff()`  
> **Dependencies:** `Pillow`, `rasterio`, `numpy`

The WMS service serves raster images as uncompressed or PNG binary streams. To enable direct downstream geospatial training and GIS compatibility, these payloads are converted into **georeferenced GeoTIFF** files.

```python
# 1. Decode binary stream into numeric RGB array
pil_image = Image.open(io.BytesIO(binary_content)).convert("RGB")
raster_array = np.array(pil_image)   # shape: (height, width, 3)

# 2. Derive affine transformation matrix mapping pixel matrix to coordinates
affine_transform = from_bounds(lon_min, lat_min, lon_max, lat_max, image_width_px, image_height_px)

# 3. Write GeoTIFF with coordinate reference system and LZW compression
with rasterio.open(output_path, "w", driver="GTiff", crs=CRS.from_epsg(4326),
                   transform=affine_transform, compress="lzw", ...) as dst:
    dst.write(raster_array.transpose(2, 0, 1))
```

Because raster encoding is CPU-bound, execution is delegated to worker threads via `loop.run_in_executor()`, preserving full responsiveness of the asynchronous network event loop.

---

### Step 6 — Dataset Manifest Registration

> **Module:** `utils/manifest.py` → `initialize_manifest()` and `record_result()`  
> **Dependency:** `csv` (Python Standard Library)

Following each coordinate processing cycle, completion metadata is recorded immediately to disk within `artifacts/dataset_manifest.csv`.

```python
record_result(
    manifest_path=manifest_path,
    sample_id=1,
    property_id="ES-3200136-...",
    x=317411.43,
    y=7898046.95,
    bbox=(lon_min, lat_min, lon_max, lat_max),
    satellite_status="ok",
    land_cover_status="ok",
)
```

#### Manifest Schema:

| Column | Description |
|---|---|
| `sample_id` | Sequential sample integer index (1, 2, 3, ...) |
| `property_id` | Rural property identifier code in the CAR registry |
| `x`, `y` | Original planar centroid coordinates (EPSG:31984) |
| `bbox_xmin`, `ymin`, `xmax`, `ymax` | Bounding box spatial extents in decimal degrees (EPSG:4326) |
| `satellite_status` | Status flag (`ok`, `error`, or `skipped`) |
| `land_cover_status` | Status flag (`ok`, `error`, or `skipped`) |
| `download_timestamp` | ISO 8601 UTC/local timestamp of extraction completion |

---

## Real-Time Monitoring and Logging

### Terminal Progress Indicator
`tqdm` outputs a real-time progress monitor:
```
Downloading images:  42%|████████████          | 420/1000 [03:21<04:38,  2.09img/s]
```

### Execution Log File
Structured event logs are recorded to `logs/execution.log` and broadcast to stdout:
```
2026-10-03 19:25:01 [INFO] Connecting to WMS service: https://ide.geobases.es.gov.br/geoserver/ows
2026-10-03 19:25:03 [INFO] Layer validated: geonode:ijsn-ortofotomosaico-es-kompsat-3-3a-2019-2020
2026-10-03 19:25:03 [INFO] CSV loaded: 282 coordinate records found
2026-10-03 19:25:10 [INFO] [sample_1] SATELLITE OK
2026-10-03 19:25:10 [INFO] [sample_1] SEGMENTED OK
```

---

## Architectural Flow Diagram

```
sample_train_coordinates.csv
       │
       ▼
 [pandas] Ingests and validates spatial records
       │
       ▼ Iterate across records (concurrency managed via asyncio.Semaphore)
       │
       ├──► [pyproj] Converts EPSG:31984 (UTM) → EPSG:4326 (BBox degrees)
       │
       ├──► [aiohttp + asyncio] Dispatches WMS GetMap queries
       │         │                         │
       │     SATELLITE                 SEGMENTED
       │     (parallelized via asyncio.gather)
       │
       ├──► [Pillow] Decodes PNG payload to RGB
       ├──► [numpy] Converts raster to multidimensional tensor
       ├──► [rasterio] Writes georeferenced GeoTIFF with affine transform
       │
       └──► [csv] Records sample outcome to manifest
                     │
                     ▼
          [tqdm] Updates terminal progress bar
          [logging] Persists operational event logs
```

---

## Input CSV Specification

Delimiter: **semicolon** (`;`)

| Column Name | Data Type | Description |
|---|---|---|
| `property_id` *(or `cod_imovel`)* | string | Rural Environmental Registry (CAR) unique property code |
| `x` | float | Centroid X coordinate in planar projection meters (EPSG:31984 — UTM Zone 24S) |
| `y` | float | Centroid Y coordinate in planar projection meters (EPSG:31984) |
