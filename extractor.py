#!/usr/bin/env python3
"""
extractor.py
Main driver script for the IntegraCar multi-temporal spatial extraction pipeline.

Supports two temporal epochs:
  • 2019–2020: KOMPSAT-3/3A orthophotomosaic + IJSN LULC map via OGC WMS (asynchronous)
  • 2012–2015: IEMA aerial orthophotomosaic via WMS + GeoBases vector shapefile
               offline rasterization (with exact RGB palette harmonization)
  • both:      Extracts synchronized, paired datasets for both epochs simultaneously

Usage:
  # Extract 2019–2020 (default):
  python extractor.py --csv sample_train_coordinates.csv --output ./output

  # Extract 2012–2015:
  python extractor.py --csv sample_train_coordinates.csv --output ./output --period 2012-2015

  # Extract both epochs concurrently with 2048x2048 resolution:
  python extractor.py --csv sample_train_coordinates.csv --output ./output --period both --width 2048 --height 2048

  # Verification smoke test (first 10 samples):
  python extractor.py --csv sample_train_coordinates.csv --output ./output --period both --limit 10
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
import pandas as pd
from tqdm import tqdm

from config import CONFIG
from utils.manifest import (
    initialize_manifest,
    record_result,
)
from utils.rasterize import (
    download_and_extract_shapefile,
    load_and_prepare_shapefile,
    rasterize_vector_bbox_async,
)
from utils.wms import (
    calculate_bbox_latlon,
    connect_wms,
    download_image_async,
    validate_layer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CLI Argument Parsing & Validation
# ---------------------------------------------------------------------------

def create_parser() -> argparse.ArgumentParser:
    """Configures and returns the command-line argument parser."""

    parser = argparse.ArgumentParser(
        prog="extractor.py",
        description=(
            "===============================================================================\n"
            "       🛰️  IntegraCar — Multi-Temporal Spatial Extraction Pipeline             \n"
            "===============================================================================\n\n"
            "Automated command-line tool that extracts synchronized, georeferenced raster   \n"
            "datasets from the GeoBases do Espírito Santo spatial data infrastructure for    \n"
            "rural properties registered in the CAR (Cadastro Ambiental Rural).\n\n"
            "Supported Temporal Epochs (--period):\n"
            "  1. 2019–2020 (Default):\n"
            "     • SATELLITE  : KOMPSAT-3/3A high-resolution orthophotomosaic via WMS\n"
            "     • SEGMENTED  : IJSN Land Use/Land Cover (LULC) classified map via WMS\n"
            "  2. 2012–2015:\n"
            "     • SATELLITE  : IEMA high-resolution aerial orthophotomosaic (0.25m GSD) via WMS\n"
            "     • SEGMENTED  : GeoBases official vector shapefile locally rasterized\n"
            "                    onto matching pixel grid with standardized RGB palette\n"
            "  3. both:\n"
            "     • Extracts both 2012–2015 and 2019–2020 scenes with identical bounding box\n"
            "       extents (EPSG:4326), spatial resolution, and pixel dimensions.\n"
        ),
        epilog=(
            "-------------------------------------------------------------------------------\n"
            "Practical Execution Examples:\n"
            "-------------------------------------------------------------------------------\n"
            "  1. Standard 2019–2020 extraction (2048x2048 px, 1.0 m/px GSD):\n"
            "     python extractor.py --csv sample_train_coordinates.csv --output ./output\n\n"
            "  2. Extract 2012–2015 dataset:\n"
            "     python extractor.py \\\n"
            "       --csv sample_train_coordinates.csv \\\n"
            "       --output ./output \\\n"
            "       --period 2012-2015\n\n"
            "  3. Multi-temporal extraction (both 2012–2015 and 2019–2020 simultaneously):\n"
            "     python extractor.py \\\n"
            "       --csv sample_train_coordinates.csv \\\n"
            "       --output ./output \\\n"
            "       --period both\n\n"
            "  4. Rapid verification run (first 10 samples only):\n"
            "     python extractor.py --csv sample_train_coordinates.csv --output ./output --period both --limit 10\n\n"
            "  5. Custom spatial buffer and resolution (512m buffer, 1024x1024 px = 1.0 m/px):\n"
            "     python extractor.py \\\n"
            "       --csv sample_train_coordinates.csv \\\n"
            "       --output ./output \\\n"
            "       --buffer 512 \\\n"
            "       --width 1024 \\\n"
            "       --height 1024\n\n"
            "  6. Specify explicit local path to 2012 shapefile:\n"
            "     python extractor.py \\\n"
            "       --csv sample_train_coordinates.csv \\\n"
            "       --output ./output \\\n"
            "       --period 2012-2015 \\\n"
            "       --shapefile ./temp_shp_2012/USO_COBERTURA_VEGETAL_2012-2015/Mapeamento_Uso_Cobertura_Vegetal_2012.shp\n\n"
            "For full technical details, refer to README.md and Technologies.md.\n"
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
            "Directory layout:\n"
            "  • When --period both:\n"
            "      <DIR_PATH>/2012_2015/SATELLITE/ and SEGMENTED/\n"
            "      <DIR_PATH>/2019_2020/SATELLITE/ and SEGMENTED/\n"
            "  • When single period without --flat:\n"
            "      <DIR_PATH>/<PERIOD>/SATELLITE/ and SEGMENTED/\n"
            "  • When single period with --flat:\n"
            "      <DIR_PATH>/SATELLITE/ and SEGMENTED/\n"
            "Aliases supported: -o, --path, --caminho\n"
            "Example: --output ./output"
        ),
    )

    # -------------------------------------------------------------------------
    # Temporal Epoch Selection
    # -------------------------------------------------------------------------
    temporal_group = parser.add_argument_group("Temporal Period Options")

    temporal_group.add_argument(
        "--period",
        "--year",
        "--ano",
        "--periodo",
        dest="period",
        choices=["2019-2020", "2012-2015", "both"],
        default=CONFIG["default_period"],
        help=(
            "Temporal epoch to extract:\n"
            "  • 2019-2020 : KOMPSAT-3/3A satellite orthomosaic + IJSN LULC WMS (Default)\n"
            "  • 2012-2015 : IEMA aerial orthomosaic via WMS + GeoBases shapefile rasterized\n"
            "  • both      : Simultaneously extracts both epochs for each coordinate pair\n"
            "Aliases supported: --year, --ano, --periodo\n"
            "Default: 2019-2020"
        ),
    )

    temporal_group.add_argument(
        "--shapefile",
        "--shp",
        dest="shapefile",
        metavar="FILE_OR_DIR",
        default=None,
        help=(
            "Explicit path to the 2012–2015 land use/land cover shapefile (.shp) or\n"
            "directory containing it. If omitted, the pipeline automatically checks local\n"
            "cache or streams the official ZIP archive from the GeoBases S3 repository.\n"
            "Aliases supported: --shp\n"
            "Example: --shapefile ./temp_shp_2012/USO_COBERTURA_VEGETAL_2012-2015/Mapeamento_Uso_Cobertura_Vegetal_2012.shp"
        ),
    )

    temporal_group.add_argument(
        "--flat",
        dest="flat",
        action="store_true",
        default=False,
        help=(
            "When extracting a single period ('2019-2020' or '2012-2015'), save directly\n"
            "into <output>/SATELLITE and <output>/SEGMENTED instead of creating an epoch subfolder.\n"
            "Ignored when --period both is specified to prevent raster filename collisions."
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
            "    (With buffer=1024 and width=2048, GSD is exactly 1.0 m/pixel)\n"
            "Aliases supported: --largura\n"
            "Example: --width 2048"
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
            "Example: --height 2048"
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
            "Ideal for verification batches and rapid testing.\n"
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
            f"  • Default : {CONFIG['parallel_workers']} concurrent workers\n"
            "  • Tip     : Decrease to 1 or 2 if encountering upstream WMS 504 timeouts;\n"
            "              increase on high-throughput connections.\n"
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


async def process_sample_multitemporal_async(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    sample_id: int,
    property_id: str,
    x: float,
    y: float,
    config: dict,
    period: str,
    dirs: dict,
    vector_context: Optional[dict] = None,
) -> dict:
    """
    Coordinates end-to-end extraction for a single spatial sample across configured epochs.
    Executes network downloads and vector rasterization concurrently.
    """
    async with semaphore:
        cfg = config
        prefix = cfg["file_prefix"]
        filename = f"{prefix}_{sample_id}.tif"

        # Compute geographic bounding box in decimal degrees (EPSG:4326)
        bbox = calculate_bbox_latlon(x, y, cfg["buffer_meters"], cfg["input_crs"])

        outcomes = {
            "sample_id": sample_id,
            "property_id": property_id,
            "x": x,
            "y": y,
            "bbox": bbox,
            "periods": {},
        }

        tasks = []
        task_descriptors = []

        # ---- Epoch 2019–2020 ----
        if period in {"2019-2020", "both"}:
            sat_path_19 = dirs["2019_2020"]["satellite"] / filename
            seg_path_19 = dirs["2019_2020"]["segmented"] / filename

            tasks.append(
                _download_single_image_async(
                    session, cfg, cfg["satellite_layer_2019_2020"], bbox, sat_path_19
                )
            )
            task_descriptors.append(("2019-2020", "satellite"))

            tasks.append(
                _download_single_image_async(
                    session, cfg, cfg["land_cover_layer_2019_2020"], bbox, seg_path_19
                )
            )
            task_descriptors.append(("2019-2020", "segmented"))

        # ---- Epoch 2012–2015 ----
        if period in {"2012-2015", "both"}:
            sat_path_12 = dirs["2012_2015"]["satellite"] / filename
            seg_path_12 = dirs["2012_2015"]["segmented"] / filename

            # Satellite from WMS
            tasks.append(
                _download_single_image_async(
                    session, cfg, cfg["satellite_layer_2012_2015"], bbox, sat_path_12
                )
            )
            task_descriptors.append(("2012-2015", "satellite"))

            # Land cover from vector shapefile rasterization (thread pool executor)
            if vector_context:
                tasks.append(
                    rasterize_vector_bbox_async(
                        gdf=vector_context["gdf"],
                        id_col=vector_context["id_col"],
                        id_to_rgb=vector_context["id_to_rgb"],
                        bbox=bbox,
                        width_px=cfg["image_width_px"],
                        height_px=cfg["image_height_px"],
                        output_path=seg_path_12,
                        epsg_code=cfg["output_epsg_code"],
                    )
                )
                task_descriptors.append(("2012-2015", "segmented"))

        # Execute all epoch routines concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for (p_name, l_type), res in zip(task_descriptors, results):
            status = "ok" if res == "ok" else ("empty" if res == "empty" else "error")
            if p_name not in outcomes["periods"]:
                outcomes["periods"][p_name] = {}
            outcomes["periods"][p_name][l_type] = status

        return outcomes


# ---------------------------------------------------------------------------
# Pipeline Orchestrator
# ---------------------------------------------------------------------------

async def run_pipeline_async(cfg: dict) -> None:
    """
    Main asynchronous pipeline orchestrator:
      1. Initializes logging and dataset manifest.
      2. Validates remote WMS layers.
      3. Ingests and prepares vector LULC shapefile if 2012–2015 is enabled.
      4. Parses coordinate records from CSV.
      5. Executes concurrent downloads and rasterizations.
    """
    setup_logging(cfg["logs_dir"], cfg["log_filename"])
    logger.info("=" * 70)
    logger.info("Starting IntegraCar Multi-Temporal Image Extraction Pipeline")
    logger.info(f"  CSV file       : {cfg['csv_file']}")
    logger.info(f"  Output folder  : {cfg['output_dir']}")
    logger.info(f"  Period         : {cfg['period']}")
    logger.info(f"  Buffer radius  : {cfg['buffer_meters']} m (box side: {cfg['buffer_meters'] * 2} m)")
    logger.info(f"  Dimensions     : {cfg['image_width_px']} x {cfg['image_height_px']} px")
    gsd = (cfg["buffer_meters"] * 2) / cfg["image_width_px"]
    logger.info(f"  Resolution GSD : {gsd:.2f} m / pixel")
    if cfg.get("sample_limit"):
        logger.info(f"  Sample limit   : first {cfg['sample_limit']} coordinates")
    logger.info(f"  Workers        : {cfg['parallel_workers']}")
    logger.info("=" * 70)

    period = cfg["period"]
    base_output = Path(cfg["output_dir"])
    flat_mode = cfg.get("flat", False)

    # ---- Step 1: Directory Setup ----
    dirs = {}
    if period in {"2019-2020", "both"}:
        if period == "both" or not flat_mode:
            p_dir = base_output / cfg["period_2019_dir_name"]
        else:
            p_dir = base_output
        sat_dir = p_dir / cfg["satellite_dir_name"]
        seg_dir = p_dir / cfg["land_cover_dir_name"]
        sat_dir.mkdir(parents=True, exist_ok=True)
        seg_dir.mkdir(parents=True, exist_ok=True)
        dirs["2019_2020"] = {"satellite": sat_dir, "segmented": seg_dir}

    if period in {"2012-2015", "both"}:
        if period == "both" or not flat_mode:
            p_dir = base_output / cfg["period_2012_dir_name"]
        else:
            p_dir = base_output
        sat_dir = p_dir / cfg["satellite_dir_name"]
        seg_dir = p_dir / cfg["land_cover_dir_name"]
        sat_dir.mkdir(parents=True, exist_ok=True)
        seg_dir.mkdir(parents=True, exist_ok=True)
        dirs["2012_2015"] = {"satellite": sat_dir, "segmented": seg_dir}

    # ---- Step 2: Validate WMS connection and catalog layers ----
    wms = connect_wms(cfg["wms_url"], cfg["wms_version"])

    layers_to_check = []
    if period in {"2019-2020", "both"}:
        layers_to_check.extend([cfg["satellite_layer_2019_2020"], cfg["land_cover_layer_2019_2020"]])
    if period in {"2012-2015", "both"}:
        layers_to_check.append(cfg["satellite_layer_2012_2015"])

    for layer_name in layers_to_check:
        if validate_layer(wms, layer_name):
            logger.info(f"WMS layer confirmed: {layer_name}")
        else:
            logger.warning(f"WMS layer NOT found in catalog: {layer_name}")

    # ---- Step 3: Ingest 2012–2015 Vector Shapefile (if applicable) ----
    vector_context = None
    if period in {"2012-2015", "both"}:
        shp_target_dir = Path(cfg["shapefile_2012_dir"])
        custom_shp = Path(cfg["shapefile_2012_path"]) if cfg.get("shapefile_2012_path") else None
        resolved_shp = download_and_extract_shapefile(
            url=cfg["shapefile_2012_url"],
            target_dir=shp_target_dir,
            custom_shapefile_path=custom_shp,
        )
        gdf, id_col, id_to_rgb = load_and_prepare_shapefile(
            shp_path=resolved_shp, target_crs=cfg["wms_crs"]
        )
        vector_context = {"gdf": gdf, "id_col": id_col, "id_to_rgb": id_to_rgb}

    # ---- Step 4: Initialize dataset manifest ----
    manifest_path = Path(cfg["artifacts_dir"]) / cfg["manifest_filename"]
    initialize_manifest(manifest_path)

    # ---- Step 5: Load spatial coordinates CSV ----
    dataframe = pd.read_csv(cfg["csv_file"], sep=cfg["csv_separator"])
    total_csv = len(dataframe)
    logger.info(f"CSV ingested: {total_csv} coordinate records found")

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
    total_samples = len(dataframe)

    # ---- Step 6: Execute concurrent multi-temporal extraction ----
    success_count = 0
    error_count = 0

    semaphore = asyncio.Semaphore(cfg["parallel_workers"])
    connector = aiohttp.TCPConnector(
        limit=cfg["parallel_workers"] * 2 + 4,
        limit_per_host=cfg["parallel_workers"] * 2 + 4,
    )

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            process_sample_multitemporal_async(
                session=session,
                semaphore=semaphore,
                sample_id=row["sample_id"],
                property_id=str(row[property_col]),
                x=float(row["x"]),
                y=float(row["y"]),
                config=cfg,
                period=period,
                dirs=dirs,
                vector_context=vector_context,
            )
            for _, row in dataframe.iterrows()
        ]

        desc_label = f"Extracting [{period}]"
        with tqdm(total=len(tasks), desc=desc_label, unit="sample") as progress_bar:
            for coroutine in asyncio.as_completed(tasks):
                outcome = await coroutine
                sample_id = outcome["sample_id"]
                prop_id = outcome["property_id"]
                x_val = outcome["x"]
                y_val = outcome["y"]
                bbox_val = outcome["bbox"]

                all_ok = True
                for p_key, p_res in outcome["periods"].items():
                    sat_st = p_res.get("satellite", "error")
                    seg_st = p_res.get("segmented", "error")

                    record_result(
                        manifest_path=manifest_path,
                        sample_id=sample_id,
                        property_id=prop_id,
                        x=x_val,
                        y=y_val,
                        bbox=bbox_val,
                        satellite_status=sat_st,
                        land_cover_status=seg_st,
                        period=p_key,
                    )

                    if sat_st != "ok" or seg_st != "ok":
                        all_ok = False

                if all_ok:
                    success_count += 1
                else:
                    error_count += 1

                progress_bar.update(1)

    logger.info("=" * 70)
    logger.info("Pipeline execution completed.")
    logger.info(f"  Period executed        : {period}")
    logger.info(f"  Total samples          : {total_samples}")
    logger.info(f"  Fully successful       : {success_count}")
    logger.info(f"  Errors encountered     : {error_count}")
    logger.info(f"  Manifest recorded at   : {manifest_path.resolve()}")
    logger.info("=" * 70)


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
    cfg["period"] = args.period
    cfg["shapefile_2012_path"] = args.shapefile
    cfg["flat"] = args.flat
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
processar_amostra_async = process_sample_multitemporal_async
executar_pipeline_async = run_pipeline_async


if __name__ == "__main__":
    main()
