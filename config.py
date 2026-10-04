"""
config.py
Central configuration settings for the IntegraCar satellite image extraction pipeline.

In most scenarios, modifying this file directly is unnecessary. Parameters can be
customized via command-line arguments in extractor.py:

    python extractor.py --csv sample_train_coordinates.csv --output ./output
    python extractor.py --help

This module establishes default constants and service parameters (WMS endpoints,
layer typenames, spatial reference systems, and execution thresholds).
"""

CONFIG = {
    # --- WMS Data Source ---
    "wms_url": "https://ide.geobases.es.gov.br/geoserver/ows",
    "wms_version": "1.3.0",

    # WMS Layer typenames on GeoBases (catalog: ide.geobases.es.gov.br)
    "satellite_layer": "geonode:ijsn-ortofotomosaico-es-kompsat-3-3a-2019-2020",
    "land_cover_layer": "geonode:ijsn_map_uso_solo_es_2019_20200",

    # --- Spatial Reference Systems ---
    # WMS 1.3.0 using EPSG:4326 requires latitude/longitude bounding boxes in inverted order (lat, lon).
    # Input coordinates are projected in EPSG:31984 (SIRGAS 2000 / UTM Zone 24S in meters).
    "input_crs": "EPSG:31984",         # CRS of input coordinates in CSV
    "wms_crs": "EPSG:4326",            # CRS for WMS requests (geographic decimal degrees)
    "output_epsg_code": 4326,          # Numerical EPSG code stored in GeoTIFF metadata

    # --- Default Bounding Box and Resolution Dimensions ---
    # Can be overridden via CLI flags: --buffer, --width, --height
    "buffer_meters": 1024,             # Half-side length of the square bounding box in meters
    "image_width_px": 1024,            # Output image width in pixels
    "image_height_px": 1024,           # Output image height in pixels

    # --- WMS Request Format ---
    "wms_format": "image/png",         # Format requested from WMS endpoint
    "transparent": "FALSE",

    # --- Internal Output Paths & Naming Conventions ---
    "file_prefix": "sample",           # Generates sample_1.tif, sample_2.tif, ...
    "satellite_dir_name": "SATELLITE", # Subfolder for raw orthomosaic images
    "land_cover_dir_name": "SEGMENTED",# Subfolder for land use/land cover segmented maps
    "artifacts_dir": "artifacts",
    "logs_dir": "logs",
    "manifest_filename": "dataset_manifest.csv",
    "log_filename": "execution.log",

    # --- CSV Parsing Defaults ---
    "csv_separator": ";",

    # --- Asynchronous Pipeline Performance & Resilience ---
    "parallel_workers": 4,             # Concurrency limit for simultaneous sample downloads
    "request_timeout": 60,             # HTTP timeout per WMS request in seconds
    "retries_per_image": 3,            # Maximum retry attempts on network error or timeout
    "retry_delay_seconds": 2,          # Backoff interval between retries in seconds
}

# Backward compatibility alias
CONFIGURACOES = CONFIG
