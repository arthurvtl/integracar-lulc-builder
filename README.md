# 🛰️ IntegraCar — Image Extraction Pipeline

Automated pipeline that downloads satellite images and land use maps from **GeoBases do Espírito Santo** for rural properties registered in the CAR (Rural Environmental Registry - Cadastro Ambiental Rural).

For each coordinate listed in a CSV file, the pipeline produces two georeferenced GeoTIFF files:

- **SATELLITE** — raw orthophotomosaic (KOMPSAT 2019-2020)
- **SEGMENTED** — land use and land cover map (IJSN 2019)

---

## Prerequisites

- **Python 3.8+** installed (adding it to PATH is recommended).
- CSV file with the coordinates (see the expected format at the bottom of the page).

---

## Installation and Execution

### Step 1: Clone the repository
Open your terminal (Command Prompt, PowerShell, or Linux/Mac Terminal) and clone the project:
```bash
git clone https://github.com/integraCAR/car-imagens-downloader.git
cd car-imagens-downloader
```

### Step 2: Create a virtual environment (Recommended)
To prevent conflicts with other libraries on your computer, create and activate a virtual environment:

**On Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**On Linux/Mac:**
```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install dependencies
With the environment activated (you will see a `(venv)` in the terminal), install the required libraries:
```bash
pip install -r requirements.txt
```

### Step 4: Run the extractor
Execute the `extrator.py` script by providing your CSV file and the destination folder for the images.

**Basic example:**
```bash
python extrator.py --csv coordenadas_treino_amostra.csv --caminho ./saida
```

**Complete example (customizing parameters):**
```bash
python extrator.py \
  --csv coordenadas_treino_amostra.csv \
  --caminho ./saida \
  --buffer 1024 \
  --largura 1024 \
  --altura 1024 \
  --qtd 1000
```
*(Tip: on Windows PowerShell, if you get an error when breaking lines with `\`, write the entire command on a single line).*

**To view help:**
```bash
python extrator.py --help
```

---

## Parameters

| Parameter | Required | Description | Default |
|---|---|---|---|
| `--csv FILE` | ✅ | CSV with columns `cod_imovel`, `x`, `y` (separated by `;`) | — |
| `--caminho FOLDER` | ✅ | Destination folder where subfolders will be created | — |
| `--buffer METERS` | — | Half the side length of the geographic crop in meters | `1024` |
| `--largura PIXELS` | — | Output image width in pixels | `1024` |
| `--altura PIXELS` | — | Output image height in pixels | `1024` |
| `--qtd N` | — | Limits to the first N rows of the CSV | all |
| `--workers N` | — | Simultaneous parallel downloads | `4` |

---

## Output Structure

```
<--caminho>/
├── SATELITE/
│   ├── amostra_1.tif
│   ├── amostra_2.tif
│   └── ...
└── SEGMENTADO/
    ├── amostra_1.tif
    ├── amostra_2.tif
    └── ...

artifacts/
└── dataset_manifesto.csv   ← status record of each download

logs/
└── execucao.log            ← complete execution log
```

---

## How the Pipeline Works — Step by Step

The pipeline consists of **6 sequential steps**, executed by `extrator.py`. The internal steps of each download are performed in a parallel and asynchronous manner.

---

### Step 1 — Reading the coordinates CSV

> **File:** `extrator.py` → `executar_pipeline_async` function
> **Library:** `pandas`

The pipeline begins by reading the CSV file provided via `--csv`. This file contains one row per rural property, with the property code and its geographic coordinates in UTM.

```python
dataframe = pd.read_csv(cfg["arquivo_csv"], sep=";")
```

The **pandas** library (`pd.read_csv`) reads the file and transforms each row into a `DataFrame` row — an in-memory table structure that allows for efficient data filtering, iteration, and manipulation. If the user passed `--qtd 1000`, the `DataFrame` is immediately truncated to the first 1000 rows with `.head(1000)` before any download starts.

---

### Step 2 — Converting coordinates UTM → Latitude/Longitude

> **File:** `utils/wms.py` → `calcular_bbox_latlon` function
> **Library:** `pyproj`

The coordinates in the CSV are in the **EPSG:31984** system (UTM zone 24S, in meters). However, the GeoBases WMS server requires coordinates in **EPSG:4326** (latitude and longitude in decimal degrees).

```python
transformador = Transformer.from_crs("EPSG:31984", "EPSG:4326", always_xy=True)
lon_min, lat_min = transformador.transform(xmin_utm, ymin_utm)
lon_max, lat_max = transformador.transform(xmax_utm, ymax_utm)
```

The **pyproj** library performs this cartographic projection with geodetic precision. From the central point `(x, y)` and the buffer in meters, the code creates a square box around the point in UTM, and then converts the four corners of this box to lat/lon — obtaining the **bounding box** (bbox) that delimits the geographic region to crop.

The `Transformer` is created only once and reused from cache for all coordinates, avoiding overhead.

---

### Step 3 — Connection and validation of the WMS service

> **File:** `utils/wms.py` → `conectar_wms` and `validar_camada` functions
> **Library:** `OWSLib`

Before any download, the pipeline connects to the GeoBases WMS server to verify if it is responding and if the required layers exist.

```python
wms = WebMapService("https://ide.geobases.es.gov.br/geoserver/ows", version="1.3.0")
```

The **OWSLib** library implements the **OGC WMS** (Web Map Service) protocol — an international standard for map servers. With it, the simple call `WebMapService(url)` performs the handshake with the server, downloads the `GetCapabilities` (catalog of available layers), and exposes the result in Python.

After connecting, the pipeline checks if the two layers that will be used (`camada_satelite` and `camada_uso_solo`) actually exist on the server. If they do not exist, a warning is logged but the execution continues — because the validation is only done via OWSLib, while the downloads use `aiohttp` directly.

The connection is kept in a global cache (`_conexao_wms`) so it is not repeated for every image.

---

### Step 4 — Asynchronous image download

> **File:** `utils/wms.py` → `requisitar_imagem_wms_async` and `baixar_imagem_async` functions
> **Libraries:** `aiohttp`, `asyncio`

This is the most critical and complex step of the pipeline. For each coordinate, the pipeline needs to download **two images** (satellite + segmented), and this must happen for **hundreds or thousands of coordinates** — quickly.

The solution uses **asynchronous programming** with `asyncio` and `aiohttp`:

```python
# Download both images for the same coordinate at the same time
status_satelite, status_segmentado = await asyncio.gather(
    _baixar_uma_imagem_async(sessao, cfg, cfg["camada_satelite"], bbox, caminho_satelite),
    _baixar_uma_imagem_async(sessao, cfg, cfg["camada_uso_solo"], bbox, caminho_segmentado),
)
```

**How it works in practice:**

- **`asyncio`** is Python's concurrency engine. Instead of blocking the program while waiting for the HTTP response, it "pauses" the current operation and executes others while waiting — like a waiter taking an order from one table and immediately attending the next without waiting for the kitchen.

- **`aiohttp`** is the asynchronous HTTP client. It sends the `GetMap` request to the WMS server and waits for the response without blocking the process. It uses a TCP connection pool (`TCPConnector`) to reuse open connections to the server, reducing handshake costs.

- **`asyncio.Semaphore`** limits how many coordinates are processed simultaneously (controlled by `--workers`). This prevents overloading the GeoBases server with dozens of concurrent requests.

- **`asyncio.gather`** triggers the download of the satellite and the segmented image **in parallel** for the same coordinate — both requests travel to the server at the same time.

In case of a failure (timeout, HTTP error), the code retries up to 3 times with a 2-second pause between attempts before logging the error in the manifest.

The sent WMS request is a `GetMap` with the parameters:

```
SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap
&LAYERS=<layer_name>
&BBOX=<lat_min,lon_min,lat_max,lon_max>
&WIDTH=<width>&HEIGHT=<height>
&CRS=EPSG:4326&FORMAT=image/png
```

> **WMS 1.3.0 Warning:** in this protocol version, EPSG:4326 requires the bbox to be passed in the `lat,lon` order (inverted compared to the convention). The code already handles this in `montar_parametros_wms`.

---

### Step 5 — PNG to GeoTIFF conversion

> **File:** `utils/wms.py` → `salvar_como_geotiff` function
> **Libraries:** `Pillow`, `rasterio`, `numpy`

The WMS server returns the images in **PNG** format. For them to be useful in geospatial analysis (such as neural network segmentation), they must be converted to **georeferenced GeoTIFF** — a format that embeds geographic coordinates inside the file.

```python
# 1. Decode the binary PNG into a numerical array
imagem_pil = Image.open(io.BytesIO(conteudo_binario)).convert("RGB")
array_imagem = np.array(imagem_pil)   # shape: (height, width, 3)

# 2. Calculate the affine transformation mapping pixels → coordinates
transform_afim = from_bounds(lon_min, lat_min, lon_max, lat_max, largura, altura)

# 3. Write the GeoTIFF with geospatial metadata
with rasterio.open(caminho, "w", driver="GTiff", crs=CRS.from_epsg(4326),
                   transform=transform_afim, ...) as dst:
    dst.write(array_imagem.transpose(2, 0, 1))
```

Each library has a specific role:

- **Pillow** (`PIL.Image`) decodes the binary bytes received from the server (which are compressed PNG format) and converts them into an in-memory RGB image.

- **numpy** converts the Pillow image into a three-dimensional array of integers `(height × width × 3 channels)`. This numerical representation is what `rasterio` can write to disk.

- **rasterio** is the standard library for geospatial raster data I/O in Python. It writes the array as a GeoTIFF with:
  - **CRS** (Coordinate Reference System): `EPSG:4326`
  - **Affine transform**: a matrix associating each pixel to an actual geographic position
  - **LZW compression**: reduces file size without quality loss

Because the PNG → GeoTIFF conversion is a **CPU-bound** operation (uses processor, does not wait for I/O), it is executed in a separate thread via `loop.run_in_executor(None, ...)` — preventing the asynchronous event loop from blocking while other images are being downloaded.

---

### Step 6 — Registering in the Manifest

> **File:** `utils/manifesto.py` → `inicializar_manifesto` and `registrar_resultado` functions
> **Library:** `csv` (Python standard library)

After each pair of images is processed, the result is immediately recorded in the CSV manifest.

```python
registrar_resultado(
    numero_amostra=1,
    cod_imovel="ES-...",
    x=..., y=...,
    bbox=(lon_min, lat_min, lon_max, lat_max),
    status_satelite="ok",
    status_uso_solo="ok",
)
```

The manifest is a CSV file at `artifacts/dataset_manifesto.csv` containing one row per processed coordinate, with columns:

| Column | Description |
|---|---|
| `numero_amostra` | Sequential number (1, 2, 3, ...) |
| `cod_imovel` | Property code in CAR |
| `x`, `y` | Original UTM coordinates |
| `bbox_xmin/ymin/xmax/ymax` | Bounding box in decimal degrees |
| `status_satelite` | `ok` or `erro` |
| `status_uso_solo` | `ok` or `erro` |
| `data_download` | ISO 8601 timestamp of the download moment |

Python's standard **`csv`** library is used with `DictWriter`, which writes dictionaries directly as CSV rows — one at a time, in append mode (`"a"`). This ensures the manifest is updated in real-time: even if the pipeline is interrupted halfway, the processed samples remain recorded.

---

### Progress Bar

> **Library:** `tqdm`

During processing, the terminal displays a real-time progress bar:

```
Baixando imagens:  42%|████████████          | 420/1000 [03:21<04:38,  2.09img/s]
```

The **tqdm** library wraps the processing loop and automatically updates the bar for each completed image, displaying: percentage, count, elapsed time, estimated time, and speed (images/second).

---

### Logging

> **Library:** `logging` (Python standard library)

Running parallel to the progress bar, all pipeline events are recorded with a timestamp in the `logs/execucao.log` file and displayed in the terminal:

```
2025-03-05 14:32:01 [INFO] Connecting to WMS service: https://...
2025-03-05 14:32:03 [INFO] Layer validated: geonode:ijsn-ortofoto...
2025-03-05 14:32:03 [INFO] CSV loaded: 3000 coordinates found
2025-03-05 14:32:45 [INFO] [amostra_42] SATELITE OK
2025-03-05 14:32:45 [WARNING] Timeout on attempt 1/3
```

Python's standard **`logging`** library uses two simultaneous `handlers`: a `FileHandler` (saves to the log file) and a `StreamHandler` (displays in the terminal). The `INFO` level records normal flow; errors and warnings appear in `WARNING` and `ERROR`.

---

## Flow Diagram

```
coordenadas.csv
      │
      ▼
 [pandas] reads the CSV
      │
      ▼ for each coordinate (in parallel via asyncio.Semaphore)
      │
      ├──► [pyproj] converts UTM → lat/lon → calculates bbox
      │
      ├──► [aiohttp + asyncio] sends GetMap to GeoBases WMS
      │         │                     │
      │    SATELLITE              SEGMENTED
      │    (in parallel via asyncio.gather)
      │
      ├──► [Pillow] decodes PNG → RGB image
      ├──► [numpy] converts image → numerical array
      ├──► [rasterio] writes georeferenced GeoTIFF
      │
      └──► [csv] registers result in manifest
                    │
                    ▼
         [tqdm] updates progress bar
         [logging] records events in log
```

---

## Input CSV Format

Separator: **semicolon** (`;`)

| Column | Type | Description |
|---|---|---|
| `cod_imovel` | string | Property code in CAR |
| `x` | float | X coordinate in meters (EPSG:31984 — UTM 24S) |
| `y` | float | Y coordinate in meters (EPSG:31984) |
