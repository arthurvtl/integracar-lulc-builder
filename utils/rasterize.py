"""
utils/rasterize.py
Vector Shapefile Ingestion, Spatial Indexing, and Rasterization Utilities.

Enables offline vector rasterization of historical land use/land cover (LULC)
thematic data (e.g., GeoBases 2012–2015) onto pixel grids that precisely match
the resolution, bounding box extent, and Coordinate Reference System (CRS)
of corresponding satellite orthophotomosaics.
"""

import asyncio
import logging
import os
import shutil
import zipfile
from pathlib import Path
from typing import Dict, Optional, Tuple

import geopandas as gpd
import numpy as np
import rasterio
import requests
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.transform import from_bounds

from utils.palette import LULC_COLOR_PALETTE

logger = logging.getLogger(__name__)


def find_shapefile_in_dir(directory: Path) -> Optional[Path]:
    """Recursively traverses a directory looking for the first .shp file."""
    if not directory.exists():
        return None
    for root, _, files in os.walk(directory):
        for filename in files:
            if filename.lower().endswith(".shp") and not filename.startswith("."):
                return Path(root) / filename
    return None


def download_and_extract_shapefile(
    url: str,
    target_dir: Path,
    custom_shapefile_path: Optional[Path] = None,
) -> Path:
    """
    Ensures that the target LULC shapefile is accessible on local disk.
    
    Resolution hierarchy:
      1. Explicit path passed via custom_shapefile_path.
      2. Existing shapefile inside target_dir.
      3. Fallback cache search in known local paths.
      4. Automated HTTP streaming download from S3 and ZIP extraction.

    Returns:
        Path to the resolved .shp file.
    """
    target_dir = Path(target_dir)

    # 1. Custom explicit path
    if custom_shapefile_path:
        path = Path(custom_shapefile_path)
        if path.is_file() and path.suffix.lower() == ".shp":
            logger.info(f"Using explicitly specified shapefile: {path.resolve()}")
            return path
        if path.is_dir():
            found = find_shapefile_in_dir(path)
            if found:
                logger.info(f"Found shapefile in specified directory: {found.resolve()}")
                return found

    # 2. Local target directory cache
    cached_shp = find_shapefile_in_dir(target_dir)
    if cached_shp:
        logger.info(f"Shapefile located in local cache: {cached_shp.resolve()}")
        return cached_shp

    # 3. Fallback search in known sibling workspace directories
    fallback_candidates = [
        Path("/Users/arthurvtl/IFES/IC/car-imagens-downloader/temp_shp_2012"),
        Path.home() / "IFES/IC/car-imagens-downloader/temp_shp_2012",
    ]
    for candidate in fallback_candidates:
        fallback_shp = find_shapefile_in_dir(candidate)
        if fallback_shp:
            logger.info(f"Reusing shapefile from existing local installation: {fallback_shp.resolve()}")
            return fallback_shp

    # 4. Automated streaming download from remote S3 endpoint
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_dest = target_dir.with_suffix(".zip")

    logger.info(f"Downloading 2012-2015 LULC shapefile from: {url}")
    print(f"\n[INFO] Downloading 2012-2015 LULC shapefile archive (~360 MB compressed)...")

    try:
        with requests.get(url, stream=True, timeout=120) as response:
            response.raise_for_status()
            with open(zip_dest, "wb") as f_out:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f_out.write(chunk)
        logger.info(f"Archive successfully downloaded to: {zip_dest}")

        logger.info(f"Extracting shapefile archive into: {target_dir}")
        print(f"[INFO] Extracting shapefile archive...")
        with zipfile.ZipFile(zip_dest, "r") as zip_ref:
            zip_ref.extractall(target_dir)

        # Remove downloaded zip archive to conserve disk storage
        if zip_dest.exists():
            zip_dest.unlink()

    except Exception as err:
        logger.error(f"Failed to download or extract shapefile from {url}: {err}")
        raise RuntimeError(f"Unable to retrieve 2012-2015 shapefile: {err}") from err

    extracted_shp = find_shapefile_in_dir(target_dir)
    if not extracted_shp:
        raise FileNotFoundError(f"No .shp file found after extracting archive in {target_dir}")

    logger.info(f"Shapefile ready at: {extracted_shp.resolve()}")
    return extracted_shp


def load_and_prepare_shapefile(
    shp_path: Path, target_crs: str = "EPSG:4326"
) -> Tuple[gpd.GeoDataFrame, str, Dict[int, Tuple[int, int, int]]]:
    """
    Loads vector dataset into memory, converts CRS, and maps class labels to RGB values.

    Returns:
        Tuple containing:
          - GeoDataFrame reprojected to target CRS.
          - Name of the class code identifier column.
          - Dictionary mapping class ID integers to RGB color tuples (R, G, B).
    """
    logger.info(f"Loading vector shapefile into memory: {shp_path.name}")
    print(f"[INFO] Ingesting and reprojecting vector shapefile: {shp_path.name}...")

    gdf = gpd.read_file(shp_path)
    if gdf.crs is None:
        logger.warning("Shapefile lacks defined CRS metadata. Assuming SIRGAS 2000 UTM 24S (EPSG:31984).")
        gdf.set_crs("EPSG:31984", inplace=True)

    if str(gdf.crs).upper() != target_crs.upper():
        logger.info(f"Reprojecting vector geometries from {gdf.crs} to {target_crs}...")
        gdf = gdf.to_crs(target_crs)

    # Discover column names dynamically
    cols_lower = {col.lower().strip(): col for col in gdf.columns}
    col_id = (
        cols_lower.get("código")
        or cols_lower.get("codigo")
        or cols_lower.get("c")
        or cols_lower.get("id")
    )
    col_class = (
        cols_lower.get("classe")
        or cols_lower.get("class")
        or cols_lower.get("descricao")
        or cols_lower.get("nome")
    )

    if not col_id or not col_class:
        raise ValueError(
            f"Could not identify ID and Class columns in shapefile. Available: {list(gdf.columns)}"
        )

    # Build numeric ID to RGB color mapping
    id_to_rgb: Dict[int, Tuple[int, int, int]] = {0: (0, 0, 0)}  # Background class: black
    unique_pairs = gdf[[col_id, col_class]].drop_duplicates()

    for _, row in unique_pairs.iterrows():
        try:
            cid = int(row[col_id])
        except (ValueError, TypeError):
            continue
        cname = str(row[col_class]).strip()
        rgb_color = LULC_COLOR_PALETTE.get(cname)
        if rgb_color is not None:
            id_to_rgb[cid] = rgb_color
        else:
            logger.debug(f"Unmapped class name in palette: '{cname}' (ID {cid})")

    logger.info(f"Vector dataset prepared: {len(gdf)} features, {len(id_to_rgb)} mapped class IDs.")
    return gdf, col_id, id_to_rgb


def rasterize_vector_bbox(
    gdf: gpd.GeoDataFrame,
    id_col: str,
    id_to_rgb: Dict[int, Tuple[int, int, int]],
    bbox: Tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    output_path: Path,
    epsg_code: int = 4326,
) -> str:
    """
    Renders vector polygons intersecting a spatial bounding box onto a 3-band RGB GeoTIFF.

    Parameters:
        gdf: Spatial GeoDataFrame in target CRS (EPSG:4326).
        id_col: Column name containing integer class codes.
        id_to_rgb: Lookup table mapping integer IDs to RGB color tuples.
        bbox: Geographic bounding box (minx_lon, miny_lat, maxx_lon, maxy_lat).
        width_px: Horizontal pixel resolution.
        height_px: Vertical pixel resolution.
        output_path: Destination path for the generated GeoTIFF.
        epsg_code: Output EPSG spatial reference code.

    Returns:
        Status string: 'ok' if rasterized, 'error' on exception, or 'skipped' if empty.
    """
    target_file = Path(output_path)
    target_file.parent.mkdir(parents=True, exist_ok=True)

    minx, miny, maxx, maxy = bbox
    transform = from_bounds(minx, miny, maxx, maxy, width_px, height_px)

    # Fast spatial slice using rtree / spatial index
    gdf_slice = gdf.cx[minx:maxx, miny:maxy]
    if gdf_slice.empty:
        logger.warning(f"No vector polygons intersect bounding box for {target_file.name}")
        # Produce background raster to maintain dataset symmetry
        blank_rgb = np.zeros((3, height_px, width_px), dtype=np.uint8)
        with rasterio.open(
            target_file,
            "w",
            driver="GTiff",
            height=height_px,
            width=width_px,
            count=3,
            dtype="uint8",
            crs=CRS.from_epsg(epsg_code),
            transform=transform,
            compress="lzw",
            photometric="RGB",
        ) as dst:
            dst.write(blank_rgb)
        return "empty"

    # Assemble geometry and attribute value pairs
    shapes = []
    for geom, cid_val in zip(gdf_slice.geometry, gdf_slice[id_col]):
        if geom is None or geom.is_empty:
            continue
        try:
            cid = int(cid_val)
            shapes.append((geom, cid))
        except (ValueError, TypeError):
            continue

    if not shapes:
        logger.warning(f"No valid geometric features found for {target_file.name}")
        return "empty"

    # Rasterize vector polygons into a 2D integer grid
    ids_array = rasterize(
        shapes=shapes,
        out_shape=(height_px, width_px),
        transform=transform,
        fill=0,
        dtype="uint8",
    )

    # Map integer class grid to 3-band RGB array
    rgb_array = np.zeros((3, height_px, width_px), dtype=np.uint8)
    for class_id, (r, g, b) in id_to_rgb.items():
        if class_id == 0:
            continue
        mask = ids_array == class_id
        if np.any(mask):
            rgb_array[0][mask] = r
            rgb_array[1][mask] = g
            rgb_array[2][mask] = b

    # Write georeferenced GeoTIFF with lossless LZW compression
    with rasterio.open(
        target_file,
        "w",
        driver="GTiff",
        height=height_px,
        width=width_px,
        count=3,
        dtype="uint8",
        crs=CRS.from_epsg(epsg_code),
        transform=transform,
        compress="lzw",
        photometric="RGB",
    ) as dst:
        dst.write(rgb_array)

    return "ok"


async def rasterize_vector_bbox_async(
    gdf: gpd.GeoDataFrame,
    id_col: str,
    id_to_rgb: Dict[int, Tuple[int, int, int]],
    bbox: Tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    output_path: Path,
    epsg_code: int = 4326,
) -> str:
    """Asynchronous wrapper that offloads CPU-bound rasterization to thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        rasterize_vector_bbox,
        gdf,
        id_col,
        id_to_rgb,
        bbox,
        width_px,
        height_px,
        output_path,
        epsg_code,
    )
