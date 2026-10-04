#!/usr/bin/env python3
"""
extractor.py
Main driver script for the IntegraCar satellite image extraction pipeline.

Usage:
  python extractor.py --csv sample_train_coordinates.csv --output ./output
  python extractor.py --csv sample_train_coordinates.csv --output ./output --buffer 512 --width 512 --height 512 --limit 500

All configuration defaults are defined in config.py.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import aiohttp
import pandas as pd
from tqdm import tqdm

from config import CONFIG
from utils.manifest import (
    initialize_manifest,
    record_result,
)
from utils.wms import (
    calculate_bbox_latlon,
    connect_wms,
    download_image_async,
    validate_layer,
)


# ---------------------------------------------------------------------------
# CLI Argument Parsing & Validation
# ---------------------------------------------------------------------------

def create_parser() -> argparse.ArgumentParser:
    """Configures and returns the command-line argument parser."""

    parser = argparse.ArgumentParser(
        prog="extractor.py",
        description=(
            "===============================================================================\n"
            "       🛰️  IntegraCar — Automated Spatial Image Extraction Pipeline           \n"
            "===============================================================================\n\n"
            "Automated command-line tool that extracts paired, georeferenced raster datasets\n"
            "from the GeoBases do Espírito Santo Web Map Service (WMS) for rural properties\n"
            "cataloged in the Rural Environmental Registry (CAR - Cadastro Ambiental Rural).\n\n"
            "For each coordinate record provided in the input CSV, two synchronized GeoTIFF\n"
            "raster products are generated with spatial metadata (EPSG:4326):\n"
            "  1. SATELLITE  — High-resolution aerial orthophotomosaic (raw RGB)\n"
            "  2. SEGMENTED  — Land use and land cover (LULC) classification map\n"
        ),
        epilog=(
            "-------------------------------------------------------------------------------\n"
            "Practical Execution Examples:\n"
            "-------------------------------------------------------------------------------\n"
            "  1. Standard execution:\n"
            "     python extractor.py --csv sample_train_coordinates.csv --output ./output\n\n"
            "  2. Quick verification run (first 10 samples only):\n"
            "     python extractor.py --csv sample_train_coordinates.csv --output ./output --limit 10\n\n"
            "  3. Custom spatial window and resolution (1024m buffer, 512x512 pixels):\n"
            "     python extractor.py \\\n"
            "       --csv sample_train_coordinates.csv \\\n"
            "       --output ./output \\\n"
            "       --buffer 1024 \\\n"
            "       --width 512 \\\n"
            "       --height 512\n\n"
            "  4. Throttled execution for low-bandwidth or unstable network connections:\n"
            "     python extractor.py --csv sample_train_coordinates.csv --output ./output --workers 2\n\n"
            "For detailed documentation, refer to README.md and Technologies.md.\n"
            "==============================================================================="
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    # -------------------------------------------------------------------------
    # Mandatory Arguments (Input & Output)
    # -------------------------------------------------------------------------
    io_group = parser.add_argument_group("Required Arguments (Input & Output)")

    io_group.add_argument(
        "--csv",
        metavar="FILE_PATH",
        required=True,
        help=(
            "Path to the input CSV file containing spatial coordinates.\n"
            "File format specifications:\n"
            "  • Delimiter : Semicolon (;)\n"
            "  • Columns   : property_id (or cod_imovel), x, y\n"
            "  • Projected : Planar UTM coordinates in meters (EPSG:31984 - SIRGAS 2000 24S)\n"
            "Example: --csv sample_train_coordinates.csv"
        ),
    )

    io_group.add_argument(
        "--output",
        "-o",
        "--path",
        "--caminho",
        dest="output",
        metavar="DIR_PATH",
        required=True,
        help=(
            "Destination directory path where output subdirectories and rasters are saved.\n"
            "Automatically generates the following folder hierarchy:\n"
            "  • <DIR_PATH>/SATELLITE/  — Raw orthophotomosaic GeoTIFF files\n"
            "  • <DIR_PATH>/SEGMENTED/  — Land use/land cover classification GeoTIFF files\n"
            "The directory is created automatically if it does not already exist.\n"
            "Aliases supported: -o, --path, --caminho\n"
            "Example: --output ./output"
        ),
    )

    # -------------------------------------------------------------------------
    # Spatial Extents and Raster Dimensions
    # -------------------------------------------------------------------------
    spatial_group = parser.add_argument_group("Spatial Extent and Resolution Options")

    spatial_group.add_argument(
        "--buffer",
        metavar="METERS",
        type=int,
        default=CONFIG["buffer_meters"],
        help=(
            "Half-side radius in meters around each centroid coordinate point.\n"
            "Defines a square bounding box of size (2 * buffer) × (2 * buffer) meters.\n"
            f"  • Default : {CONFIG['buffer_meters']} m (yields a 2048 m × 2048 m bounding box)\n"
            "  • Example : --buffer 512 (yields a 1024 m × 1024 m bounding box)"
        ),
    )

    spatial_group.add_argument(
        "--width",
        "--largura",
        dest="width",
        metavar="PIXELS",
        type=int,
        default=CONFIG["image_width_px"],
        help=(
            "Horizontal resolution of each exported GeoTIFF image in pixels.\n"
            f"  • Default : {CONFIG['image_width_px']} px\n"
            "  • Ground Sampling Distance (GSD) = (2 * buffer) / width meters per pixel.\n"
            "Aliases supported: --largura\n"
            "Example: --width 512"
        ),
    )

    spatial_group.add_argument(
        "--height",
        "--altura",
        dest="height",
        metavar="PIXELS",
        type=int,
        default=CONFIG["image_height_px"],
        help=(
            "Vertical resolution of each exported GeoTIFF image in pixels.\n"
            f"  • Default : {CONFIG['image_height_px']} px\n"
            "Aliases supported: --altura\n"
            "Example: --height 512"
        ),
    )

    # -------------------------------------------------------------------------
    # Execution & Performance Options
    # -------------------------------------------------------------------------
    exec_group = parser.add_argument_group("Execution and Concurrency Options")

    exec_group.add_argument(
        "--limit",
        "--count",
        "--qtd",
        dest="limit",
        metavar="N",
        type=int,
        default=None,
        help=(
            "Maximum number of spatial coordinates to process from the input CSV.\n"
            "Extracts rows from index 0 up to N. If omitted, all rows are processed.\n"
            "Ideal for testing, development, and validation batches.\n"
            "Aliases supported: --count, --qtd\n"
            "Example: --limit 50"
        ),
    )

    exec_group.add_argument(
        "--workers",
        metavar="CONCURRENCY",
        type=int,
        default=CONFIG["parallel_workers"],
        help=(
            "Maximum number of simultaneous asynchronous sample extractions.\n"
            "Each worker simultaneously downloads both SATELLITE and SEGMENTED layers.\n"
            f"  • Default : {CONFIG['parallel_workers']} concurrent workers\n"
            "  • Tip     : Decrease to 1 or 2 if encountering upstream WMS 504 timeouts;\n"
            "              increase on high-throughput connections with responsive servers.\n"
            "Example: --workers 2"
        ),
    )

    return parser


def validate_args(args: argparse.Namespace) -> None:
    """Validates CLI arguments and terminates execution with informative diagnostic messages upon error."""

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"\n❌ ERROR: CSV input file not found: {csv_path.resolve()}", file=sys.stderr)
        print("   Verify the file path provided to --csv\n", file=sys.stderr)
        sys.exit(1)

    if csv_path.suffix.lower() not in {".csv", ".txt"}:
        print(f"\n⚠️  WARNING: File '{csv_path.name}' does not have a .csv extension.", file=sys.stderr)
        print("   Proceeding with execution...\n", file=sys.stderr)

    for param_name, param_val in [("--buffer", args.buffer), ("--width", args.width), ("--height", args.height)]:
        if param_val <= 0:
            print(f"\n❌ ERROR: {param_name} must be a positive integer (received: {param_val})\n", file=sys.stderr)
            sys.exit(1)

    if args.limit is not None and args.limit <= 0:
        print(f"\n❌ ERROR: --limit must be a positive integer (received: {args.limit})\n", file=sys.stderr)
        sys.exit(1)

    if args.workers <= 0:
        print(f"\n❌ ERROR: --workers must be a positive integer (received: {args.workers})\n", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

def setup_logging(logs_dir: str, log_filename: str) -> None:
    """Configures root logging handler to simultaneously write to file and stream to console."""
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    log_file_path = Path(logs_dir) / log_filename

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file_path, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


# ---------------------------------------------------------------------------
# Asynchronous Processing Tasks
# ---------------------------------------------------------------------------

async def _download_single_image_async(
    session: aiohttp.ClientSession, cfg: dict, layer: str, bbox: tuple, output_path: Path
) -> str:
    """Dispatches a single image request and returns outcome status ('ok' or 'error')."""
    try:
        await download_image_async(
            session=session,
            wms_url=cfg["wms_url"],
            layer=layer,
            bbox=bbox,
            output_path=output_path,
            image_width_px=cfg["image_width_px"],
            image_height_px=cfg["image_height_px"],
            crs=cfg["wms_crs"],
            wms_version=cfg["wms_version"],
            wms_format=cfg["wms_format"],
            transparent=cfg["transparent"],
            epsg_code=cfg["output_epsg_code"],
            timeout=cfg["request_timeout"],
            retries=cfg["retries_per_image"],
            retry_delay_seconds=cfg["retry_delay_seconds"],
        )
        return "ok"
    except Exception as err:
        logging.getLogger(__name__).error(f"Download failed for {output_path.name}: {err}")
        return "error"


async def process_sample_async(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    sample_id: int,
    property_id: str,
    x: float,
    y: float,
    config: dict,
) -> dict:
    """
    Coordinates end-to-end extraction for a single spatial sample:
    computes bounding box and initiates concurrent downloads for SATELLITE and SEGMENTED layers.
    """
    async with semaphore:
        logger = logging.getLogger(__name__)
        cfg = config

        prefix = cfg["file_prefix"]
        filename = f"{prefix}_{sample_id}.tif"

        satellite_dir = Path(cfg["output_dir"]) / cfg["satellite_dir_name"]
        segmented_dir = Path(cfg["output_dir"]) / cfg["land_cover_dir_name"]

        satellite_path = satellite_dir / filename
        segmented_path = segmented_dir / filename

        # Compute geographic bounding box in decimal degrees
        bbox = calculate_bbox_latlon(x, y, cfg["buffer_meters"], cfg["input_crs"])

        # Fetch SATELLITE and SEGMENTED rasters concurrently
        satellite_status, land_cover_status = await asyncio.gather(
            _download_single_image_async(
                session, cfg, cfg["satellite_layer"], bbox, satellite_path
            ),
            _download_single_image_async(
                session, cfg, cfg["land_cover_layer"], bbox, segmented_path
            ),
        )

        if satellite_status == "ok":
            logger.info(f"[sample_{sample_id}] SATELLITE OK")
        if land_cover_status == "ok":
            logger.info(f"[sample_{sample_id}] SEGMENTED OK")

        return {
            "sample_id": sample_id,
            "property_id": property_id,
            "x": x,
            "y": y,
            "bbox": bbox,
            "satellite_status": satellite_status,
            "land_cover_status": land_cover_status,
        }


async def run_pipeline_async(cfg: dict) -> None:
    """
    Main asynchronous pipeline orchestrator: validates WMS service layers,
    parses coordinate records, and executes parallel downloads.
    """
    setup_logging(cfg["logs_dir"], cfg["log_filename"])
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("Starting IntegraCar Image Extraction Pipeline")
    logger.info(f"  CSV file       : {cfg['csv_file']}")
    logger.info(f"  Output folder  : {cfg['output_dir']}")
    logger.info(f"  Buffer radius  : {cfg['buffer_meters']} m")
    logger.info(f"  Dimensions     : {cfg['image_width_px']} x {cfg['image_height_px']} px")
    if cfg.get("sample_limit"):
        logger.info(f"  Sample limit   : first {cfg['sample_limit']} coordinates")
    logger.info(f"  Workers        : {cfg['parallel_workers']}")
    logger.info("=" * 60)

    # ---- Step 1: Validate WMS connection and catalog layers ----
    wms = connect_wms(cfg["wms_url"], cfg["wms_version"])

    for layer_name in [cfg["satellite_layer"], cfg["land_cover_layer"]]:
        if validate_layer(wms, layer_name):
            logger.info(f"Layer validated: {layer_name}")
        else:
            logger.warning(f"Layer NOT found: {layer_name}")

    # ---- Step 2: Initialize dataset manifest ----
    manifest_path = Path(cfg["artifacts_dir"]) / cfg["manifest_filename"]
    initialize_manifest(manifest_path)

    # ---- Step 3: Load spatial coordinates CSV ----
    dataframe = pd.read_csv(cfg["csv_file"], sep=cfg["csv_separator"])
    total_csv = len(dataframe)
    logger.info(f"CSV loaded: {total_csv} coordinate records found")

    # Resolve property identifier column dynamically (supports property_id or cod_imovel)
    if "property_id" in dataframe.columns:
        property_col = "property_id"
    elif "cod_imovel" in dataframe.columns:
        property_col = "cod_imovel"
    else:
        property_col = dataframe.columns[0]

    # Apply sample processing ceiling if requested
    limit = cfg.get("sample_limit")
    if limit and limit < total_csv:
        dataframe = dataframe.head(limit)
        logger.info(f"Processing restricted to first {limit} coordinates (of {total_csv})")

    dataframe["sample_id"] = range(1, len(dataframe) + 1)

    # ---- Step 4: Ensure target destination directories exist ----
    satellite_dir = Path(cfg["output_dir"]) / cfg["satellite_dir_name"]
    segmented_dir = Path(cfg["output_dir"]) / cfg["land_cover_dir_name"]
    satellite_dir.mkdir(parents=True, exist_ok=True)
    segmented_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"SATELLITE directory : {satellite_dir.resolve()}")
    logger.info(f"SEGMENTED directory : {segmented_dir.resolve()}")

    # ---- Step 5: Execute concurrent download pipeline ----
    success_count = 0
    error_count = 0

    semaphore = asyncio.Semaphore(cfg["parallel_workers"])
    connector = aiohttp.TCPConnector(
        limit=cfg["parallel_workers"] * 2 + 4,
        limit_per_host=cfg["parallel_workers"] * 2 + 4,
    )

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_sample_async(
                session=session,
                semaphore=semaphore,
                sample_id=row["sample_id"],
                property_id=str(row[property_col]),
                x=float(row["x"]),
                y=float(row["y"]),
                config=cfg,
            )
            for _, row in dataframe.iterrows()
        ]

        with tqdm(total=len(tasks), desc="Downloading images", unit="img") as progress_bar:
            for coroutine in asyncio.as_completed(tasks):
                outcome = await coroutine

                record_result(
                    manifest_path=manifest_path,
                    sample_id=outcome["sample_id"],
                    property_id=outcome["property_id"],
                    x=outcome["x"],
                    y=outcome["y"],
                    bbox=outcome["bbox"],
                    satellite_status=outcome["satellite_status"],
                    land_cover_status=outcome["land_cover_status"],
                )

                if outcome["satellite_status"] == "ok" and outcome["land_cover_status"] == "ok":
                    success_count += 1
                else:
                    error_count += 1

                progress_bar.update(1)

    logger.info("=" * 60)
    logger.info("Pipeline execution completed.")
    logger.info(f"  Complete pairs (ok/ok) : {success_count}")
    logger.info(f"  Errors encountered     : {error_count}")
    logger.info(f"  Manifest recorded at   : {manifest_path}")
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = create_parser()
    args = parser.parse_args()
    validate_args(args)

    # Initialize execution configuration by merging defaults with CLI flags
    cfg = dict(CONFIG)
    cfg["csv_file"] = args.csv
    cfg["output_dir"] = args.output
    cfg["buffer_meters"] = args.buffer
    cfg["image_width_px"] = args.width
    cfg["image_height_px"] = args.height
    cfg["parallel_workers"] = args.workers
    cfg["sample_limit"] = args.limit

    asyncio.run(run_pipeline_async(cfg))


# Backward compatibility aliases
criar_parser = create_parser
validar_args = validate_args
configurar_logging = setup_logging
_baixar_uma_imagem_async = _download_single_image_async
processar_amostra_async = process_sample_async
executar_pipeline_async = run_pipeline_async


if __name__ == "__main__":
    main()
