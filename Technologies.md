# 📦 Technologies and Libraries

Reference of the libraries used in the IntegraCar pipeline, with a description of each one's role in the project.

---

## pandas

**Minimum version:** `>= 2.2.3`
**Installation:** included in `requirements.txt`

Library for analyzing and manipulating tabular data in Python. In the project, it is used exclusively to **read the input CSV** with the UTM coordinates of the rural properties.

- Reads the file with `pd.read_csv(file, sep=";")` and returns a `DataFrame`
- Allows easily truncating to the first N rows (`--qtd`) with `.head(N)`
- Iterates row by row with `.iterrows()` to feed the download pipeline

**It is not used for image or geospatial processing** — only to load and prepare the input data.

---

## aiohttp

**Minimum version:** `>= 3.9.0`

**Asynchronous** HTTP client for Python, based on `asyncio`. It is the library responsible for all network communication with the GeoBases WMS server.

- Sends `GET` requests to the WMS endpoint with the `GetMap` parameters
- Uses `TCPConnector` with a connection pool to reuse TCP sockets (keep-alive), reducing the connection cost for each image
- Configures a timeout per request (`ClientTimeout`) to prevent the pipeline from getting stuck waiting for an unresponsive server
- In case of failure (timeout or HTTP error), the pipeline retries up to 3 times with a pause between attempts

Works in conjunction with `asyncio` to allow multiple downloads to occur simultaneously without blocking the process.

---

## asyncio

**Origin:** Python standard library (does not require installation)

Python's asynchronous concurrency engine. It allows executing multiple I/O operations (like HTTP downloads) "concurrently" without using multiple threads or processes.

In the project:

- **`asyncio.Semaphore`** — limits how many coordinates are processed at the same time (controlled by `--workers`). Prevents the pipeline from sending hundreds of simultaneous requests to the server.
- **`asyncio.gather`** — triggers the download of the satellite and the segmented image for the same coordinate **in parallel**, waiting for both to finish before continuing.
- **`asyncio.as_completed`** — processes the results as they are ready, without waiting for all of them to finish to display progress.
- **`loop.run_in_executor`** — executes the PNG → GeoTIFF conversion (CPU-bound operation) in a separate thread, without blocking the event loop.

---

## OWSLib

**Minimum version:** `>= 0.29.3`

Python library for consuming OGC geospatial services, including **WMS** (Web Map Service), **WFS**, and **WCS**. In the project, it is used only in the **initialization and validation phase**.

- Connects to the WMS server via `WebMapService(url, version="1.3.0")`
- Automatically downloads the `GetCapabilities` — the catalog of available layers on the server
- Allows verifying if the used layers (`camada_satelite`, `camada_uso_solo`) exist on the server, displaying a warning otherwise

**It is not used for the downloads themselves.** The image downloads are done directly with `aiohttp` to allow asynchronous communication, which OWSLib does not support.

---

## pyproj

**Minimum version:** `>= 3.6.1`

Cartographic and geodetic transformation library, based on the C library `PROJ`. It is used to **convert coordinates** between reference systems.

In the project, it converts the CSV coordinates from **EPSG:31984** (UTM zone 24S, in meters) to **EPSG:4326** (latitude/longitude in decimal degrees), which is the system required by the WMS server.

- Creates a `Transformer` with `from_crs("EPSG:31984", "EPSG:4326", always_xy=True)`
- Applies the transformation to the four corners of the bounding box around each central point
- The transformer is created only once and reused from cache for all coordinates

---

## Pillow

**Minimum version:** `>= 11.1.0`

Image processing library in Python. In the pipeline, it is used to **decode the PNG bytes** returned by the WMS server into an RGB image that can be numerically manipulated.

```python
imagem_pil = Image.open(io.BytesIO(conteudo_binario)).convert("RGB")
