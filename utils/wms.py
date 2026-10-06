"""
utils/wms.py
OGC Web Map Service (WMS) client communication and GeoTIFF georeferenced export utilities.
"""

import asyncio
import io
import logging
from pathlib import Path

import aiohttp
import numpy as np
from PIL import Image
from owslib.wms import WebMapService
from pyproj import Transformer
import rasterio
from rasterio.crs import CRS
from rasterio.transform import from_bounds

logger = logging.getLogger(__name__)

# Global cache for the WMS connection instance (prevents repeated handshake requests)
_wms_connection = None


def connect_wms(wms_url: str, wms_version: str) -> WebMapService:
    """
    Establishes connection to the OGC Web Map Service (WMS) endpoint.
    Caches the connection object to avoid redundant network handshakes.
    """
    global _wms_connection
    if _wms_connection is not None:
        return _wms_connection

    logger.info(f"Connecting to WMS service: {wms_url}")
    _wms_connection = WebMapService(wms_url, version=wms_version)
    total_layers = len(list(_wms_connection.contents))
    logger.info(f"Connected successfully. Available layers: {total_layers}")
    return _wms_connection


def validate_layer(wms: WebMapService, layer_name: str) -> bool:
    """
    Verifies whether a specified layer typename exists in the WMS service catalog.
    """
    return layer_name in wms.contents


# Coordinate transformation cache across CRS pairs
_transformer_cache: dict[str, Transformer] = {}


def _get_transformer(input_crs: str) -> Transformer:
    """Retrieves or instantiates a cached coordinate Transformer to EPSG:4326."""
    if input_crs not in _transformer_cache:
        _transformer_cache[input_crs] = Transformer.from_crs(
            input_crs, "EPSG:4326", always_xy=True
        )
    return _transformer_cache[input_crs]


def calculate_bbox_latlon(
    x: float, y: float, buffer_meters: float, input_crs: str
) -> tuple[float, float, float, float]:
    """
    Computes geographic bounding box coordinates (decimal degrees) from a projected
    centroid coordinate and a buffer distance in meters.

    Parameters:
        x: Centroid X coordinate in planar projection meters.
        y: Centroid Y coordinate in planar projection meters.
        buffer_meters: Half-side buffer radius in meters defining a square window.
        input_crs: Spatial reference system of the input coordinate (e.g., 'EPSG:31984').

    Returns:
        Tuple (minx_lon, miny_lat, maxx_lon, maxy_lat) in decimal degrees (EPSG:4326).
    """
    # Planar bounding box in projected meters
    xmin_proj = x - buffer_meters
    ymin_proj = y - buffer_meters
    xmax_proj = x + buffer_meters
    ymax_proj = y + buffer_meters

    # Transform bounding box extents to geographic decimal degrees
    transformer = _get_transformer(input_crs)
    lon_min, lat_min = transformer.transform(xmin_proj, ymin_proj)
    lon_max, lat_max = transformer.transform(xmax_proj, ymax_proj)

    return (lon_min, lat_min, lon_max, lat_max)


def build_wms_params(
    layer: str,
    bbox: tuple[float, float, float, float],
    image_width_px: int,
    image_height_px: int,
    crs: str,
    wms_version: str,
    wms_format: str,
    transparent: str,
) -> dict:
    """
    Constructs the parameter dictionary for an OGC WMS GetMap request.

    Note:
        Under WMS 1.3.0 specification with EPSG:4326, the axis order for BBOX
        is latitude, longitude (northing, easting), requiring axis inversion.
    """
    minx_lon, miny_lat, maxx_lon, maxy_lat = bbox

    # WMS 1.3.0 with EPSG:4326 axis order is lat, lon
    bbox_str = f"{miny_lat},{minx_lon},{maxy_lat},{maxx_lon}"

    return {
        "service": "WMS",
        "version": wms_version,
        "request": "GetMap",
        "layers": layer,
        "bbox": bbox_str,
        "width": image_width_px,
        "height": image_height_px,
        "crs": crs,
        "format": wms_format,
        "styles": "",
        "transparent": transparent,
    }


async def request_wms_image_async(
    session: aiohttp.ClientSession,
    wms_url: str,
    params: dict,
    timeout: int,
    retries: int,
    retry_delay_seconds: int,
) -> bytes:
    """
    Executes an asynchronous HTTP GET request to the WMS endpoint via aiohttp.
    Applies exponential or fixed delay retries upon connection failure or timeout.
    """
    timeout_cfg = aiohttp.ClientTimeout(total=timeout)

    for attempt in range(1, retries + 1):
        try:
            async with session.get(
                wms_url, params=params, timeout=timeout_cfg
            ) as response:
                response.raise_for_status()

                # Verify whether the server returned an image rather than a service exception
                content_type = response.headers.get("Content-Type", "")
                if "xml" in content_type or "text" in content_type:
                    body = await response.text()
                    raise RuntimeError(
                        f"WMS server returned service exception instead of image: {body[:300]}"
                    )

                return await response.read()

        except asyncio.TimeoutError:
            logger.warning(f"Timeout on attempt {attempt}/{retries}")
            if attempt < retries:
                await asyncio.sleep(retry_delay_seconds)

        except (aiohttp.ClientError, RuntimeError) as err:
            logger.warning(f"Attempt {attempt}/{retries} failed: {err}")
            if attempt < retries:
                await asyncio.sleep(retry_delay_seconds)

    raise RuntimeError(f"All {retries} retry attempts failed for WMS request.")


def save_as_geotiff(
    binary_content: bytes,
    output_path: str | Path,
    bbox: tuple[float, float, float, float],
    image_width_px: int,
    image_height_px: int,
    epsg_code: int,
) -> None:
    """
    Converts raw raster binary payload (PNG) into a georeferenced GeoTIFF dataset.

    Computes the affine transformation matrix matching the bounding box bounds to
    the pixel grid dimensions, encoding standard spatial reference metadata.
    """
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Decode binary image bytes into RGB array via Pillow and numpy
    pil_image = Image.open(io.BytesIO(binary_content)).convert("RGB")
    raster_array = np.array(pil_image)  # shape: (height, width, 3)

    # Compute affine transformation mapping pixel matrix to geographic coordinates
    minx, miny, maxx, maxy = bbox
    affine_transform = from_bounds(minx, miny, maxx, maxy, image_width_px, image_height_px)
    crs_target = CRS.from_epsg(epsg_code)

    with rasterio.open(
        target_path,
        "w",
        driver="GTiff",
        height=image_height_px,
        width=image_width_px,
        count=3,
        dtype="uint8",
        crs=crs_target,
        transform=affine_transform,
        compress="lzw",
        photometric="RGB",
    ) as dst:
        dst.write(raster_array.transpose(2, 0, 1))


async def download_image_async(
    session: aiohttp.ClientSession,
    wms_url: str,
    layer: str,
    bbox: tuple[float, float, float, float],
    output_path: str | Path,
    image_width_px: int,
    image_height_px: int,
    crs: str,
    wms_version: str,
    wms_format: str,
    transparent: str,
    epsg_code: int,
    timeout: int,
    retries: int,
    retry_delay_seconds: int,
) -> None:
    """
    High-level asynchronous coordinator: submits WMS GetMap query and saves result as GeoTIFF.
    Offloads CPU-bound raster writing to a background worker thread.
    """
    params = build_wms_params(
        layer=layer,
        bbox=bbox,
        image_width_px=image_width_px,
        image_height_px=image_height_px,
        crs=crs,
        wms_version=wms_version,
        wms_format=wms_format,
        transparent=transparent,
    )
    content = await request_wms_image_async(
        session=session,
        wms_url=wms_url,
        params=params,
        timeout=timeout,
        retries=retries,
        retry_delay_seconds=retry_delay_seconds,
    )

    # save_as_geotiff is CPU-bound (Pillow + rasterio), executed in thread pool
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        save_as_geotiff,
        content,
        output_path,
        bbox,
        image_width_px,
        image_height_px,
        epsg_code,
    )


# Backward compatibility aliases
conectar_wms = connect_wms
validar_camada = validate_layer
_obter_transformador = _get_transformer
calcular_bbox_latlon = calculate_bbox_latlon
montar_parametros_wms = build_wms_params
requisitar_imagem_wms_async = request_wms_image_async
salvar_como_geotiff = save_as_geotiff
baixar_imagem_async = download_image_async
