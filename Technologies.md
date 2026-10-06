# 📦 Technologies and Dependencies

Comprehensive reference of the libraries and runtime dependencies employed within the IntegraCar multi-temporal spatial extraction pipeline, outlining their respective architectural roles and integration patterns.

---

## pandas

**Minimum version:** `>= 2.2.3`  
**Installation:** included in `requirements.txt`

High-performance data manipulation and analysis library. Within this pipeline, it is utilized exclusively to parse and structure the input CSV file containing spatial coordinates of rural properties.

- Reads tabular coordinates via `pd.read_csv(file, sep=";")` returning a structured `DataFrame`.
- Dynamically resolves property identifier columns (`property_id` or `cod_imovel`).
- Enables dataset slicing via `.head(N)` when executing bounded batches (`--limit`).
- Iterates across coordinate records to populate asynchronous download tasks.

*Note: pandas is dedicated strictly to tabular parsing and metadata handling; it is not utilized for raster manipulation.*

---

## geopandas

**Minimum version:** `>= 1.0.0`

Geospatial extension of pandas, facilitating spatial operations on geometric vector data using `shapely` and `pyogrio`/`fiona`.

- Ingests the authoritative 2012–2015 Land Use / Land Cover (LULC) vector shapefile (`Mapeamento_Uso_Cobertura_Vegetal_2012.shp`) from GeoBases.
- Seamlessly reprojects state-wide polygonal vector layers from planar SIRGAS 2000 UTM 24S (**EPSG:31984**) into geographic coordinates (**EPSG:4326**).
- Leverages spatial indexing (`rtree`) through `.cx[minx:maxx, miny:maxy]` for near-instantaneous spatial clipping to the target bounding box.

---

## shapely

**Minimum version:** `>= 2.0.0`

Computational geometry engine for planar geometric objects.

- Supplies spatial geometry primitives (`Polygon`, `MultiPolygon`) within GeoPandas.
- Validates topological integrity and evaluates polygon intersections during spatial clipping.

---

## aiohttp

**Minimum version:** `>= 3.9.0`

Asynchronous HTTP client framework built on top of `asyncio`. It serves as the networking backbone for all communication with the GeoBases OGC WMS endpoint.

- Issues non-blocking HTTP `GET` queries to the WMS endpoint with standard `GetMap` parameters.
- Utilizes `TCPConnector` with persistent connection pools to maintain keep-alive TCP sockets, drastically reducing per-tile TLS/TCP handshake overhead.
- Enforces strict query timeouts (`ClientTimeout`) to prevent worker starvation on unresponsive server states.
- Implements resilient retry mechanics with configurable delays upon transient network anomalies or HTTP errors.

---

## asyncio

**Source:** Python standard library (no external installation required)

Python asynchronous concurrency framework. It facilitates concurrent execution of high-volume I/O operations (such as spatial map downloads) within a single event loop thread.

- **`asyncio.Semaphore`**: Constrains concurrent network queries (`--workers`) to prevent denial-of-service throttling or saturation of upstream WMS infrastructure.
- **`asyncio.gather`**: Dispatches simultaneous routines for both SATELLITE (orthophotomosaic) and SEGMENTED (land use/land cover) layers across all selected epochs.
- **`asyncio.as_completed`**: Yields completed image sets reactively, providing continuous pipeline throughput without blocking on trailing requests.
- **`loop.run_in_executor`**: Offloads CPU-intensive operations (such as PNG decoding, rasterization, and GeoTIFF encoding) to background worker threads, preventing event-loop degradation.

---

## OWSLib

**Minimum version:** `>= 0.29.3`

Specialized client library for Open Geospatial Consortium (OGC) web service standards, including **WMS** (Web Map Service), **WFS**, and **WCS**.

- Establishes communication with the WMS endpoint via `WebMapService(url, version="1.3.0")`.
- Parses the remote `GetCapabilities` XML document to inspect the spatial layer catalog.
- Validates the availability of configured typenames (`satellite_layer_2019_2020`, `land_cover_layer_2019_2020`, `satellite_layer_2012_2015`) prior to initiating batch extraction.

*Note: OWSLib is deployed strictly for initial service validation; actual tile streaming is handled by `aiohttp` to leverage non-blocking asynchronous execution.*

---

## pyproj

**Minimum version:** `>= 3.6.1`

Geodetic and cartographic projection transformation library, providing Python bindings to the `PROJ` system.

- Converts planar coordinates from projected spatial reference systems (e.g., **EPSG:31984** — SIRGAS 2000 / UTM Zone 24S in meters) to geographic coordinate systems (**EPSG:4326** — WGS 84 in decimal degrees).
- Computes geographic bounding box envelopes (`lon_min, lat_min, lon_max, lat_max`) derived from central coordinate points and metric buffer distances.
- Optimizes computational overhead by instantiating thread-safe, cached `Transformer` instances.

---

## Pillow (PIL)

**Minimum version:** `>= 11.1.0`

Core imaging library for Python.

- Ingests raw binary byte streams returned by WMS `GetMap` responses (`image/png`).
- Decodes compressed PNG streams in memory (`Image.open(io.BytesIO(...)).convert("RGB")`) into uncompressed RGB pixel grids without intermediate disk writes.

---

## numpy

**Minimum version:** `>= 2.2.3`

Fundamental numerical computing package providing high-performance multidimensional array structures.

- Transforms Pillow RGB image structures into three-dimensional NumPy numerical arrays with shape `(height, width, 3)`.
- Translates rasterized integer class ID arrays into standardized 3-band 8-bit RGB color matrices based on the official GeoBases color scheme.
- Reorders tensor dimensions via `.transpose(2, 0, 1)` into channel-first notation `(bands, height, width)` as required by raster geowriters.

---

## rasterio

**Minimum version:** `>= 1.4.3`

Industry-standard geospatial raster I/O package based on GDAL.

- Encodes raw pixel matrices into georeferenced GeoTIFF (`GTiff`) files.
- Provides `rasterio.features.rasterize` for burning vector geometry polygons into discrete 2D integer grids matching target affine transforms.
- Computes affine transformation matrices mapping pixel space `(row, col)` to geographic coordinates `(lon, lat)` based on bounding box limits.
- Applies standard Coordinate Reference System (CRS) definitions (such as `EPSG:4326`).
- Embeds lossless LZW compression (`compress="lzw"`) and `photometric="RGB"` tags to minimize storage footprint while retaining full radiometric fidelity.

---

## tqdm

**Minimum version:** `>= 4.67.1`

Progress bar utility for command-line interfaces.

- Provides real-time visual progress monitoring across parallel asynchronous tasks.
- Dynamically displays completion percentage, total processed samples, elapsed time, estimated time of arrival (ETA), and throughput rates (samples per second).

---

## logging

**Source:** Python standard library (no external installation required)

Structured application logging system.

- Employs dual output handlers: console streaming (`StreamHandler`) and persistent disk storage (`FileHandler` writing to `logs/execution.log`).
- Logs timestamps, operational levels (`INFO`, `WARNING`, `ERROR`), and structured context messages for auditability and diagnostic tracking.
