"""
utils/manifest.py
Manages the dataset CSV manifest for tracking extracted sample metadata and download status.
"""

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional, Set

MANIFEST_COLUMNS = [
    "sample_id",
    "property_id",
    "period",
    "x",
    "y",
    "bbox_xmin",
    "bbox_ymin",
    "bbox_xmax",
    "bbox_ymax",
    "satellite_status",
    "land_cover_status",
    "download_timestamp",
]

# Backward compatibility alias
COLUNAS_MANIFESTO = MANIFEST_COLUMNS


def initialize_manifest(manifest_path: str | Path) -> None:
    """
    Creates the manifest CSV file with header columns if it does not already exist.
    Preserves any existing manifest file without overwriting.
    """
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=MANIFEST_COLUMNS, delimiter=";")
            writer.writeheader()


def load_processed_samples(manifest_path: str | Path, period: Optional[str] = None) -> Set[int]:
    """
    Reads the manifest and returns a set of sample IDs that have already been
    successfully processed ('ok' status for both layers). Enables idempotent downloads.
    """
    path = Path(manifest_path)
    completed_samples: Set[int] = set()
    if not path.exists():
        return completed_samples

    with open(path, "r", encoding="utf-8") as csv_file:
        reader = csv.DictReader(csv_file, delimiter=";")
        for row in reader:
            if period and row.get("period") and row.get("period") != period:
                continue
            sat_status = row.get("satellite_status") or row.get("status_satelite")
            lc_status = row.get("land_cover_status") or row.get("status_uso_solo")
            sample_val = row.get("sample_id") or row.get("numero_amostra")
            if sat_status == "ok" and lc_status == "ok" and sample_val:
                try:
                    completed_samples.add(int(sample_val))
                except ValueError:
                    continue

    return completed_samples


def record_result(
    manifest_path: str | Path,
    sample_id: int,
    property_id: str,
    x: float,
    y: float,
    bbox: tuple[float, float, float, float],
    satellite_status: str,
    land_cover_status: str,
    period: str = "2019-2020",
) -> None:
    """
    Appends a record to the manifest CSV capturing the processing outcome of a sample.

    Parameters:
        manifest_path: Path to the target manifest CSV file.
        sample_id: Sequential integer identifying the sample (1, 2, 3, ...).
        property_id: Rural property identification code (e.g., CAR registry code).
        x: Centroid X coordinate in planar projection meters.
        y: Centroid Y coordinate in planar projection meters.
        bbox: Bounding box tuple (minx_lon, miny_lat, maxx_lon, maxy_lat) in decimal degrees.
        satellite_status: Outcome status ('ok', 'error', or 'skipped').
        land_cover_status: Outcome status ('ok', 'error', or 'skipped').
        period: Temporal epoch ('2019-2020' or '2012-2015').
    """
    path = Path(manifest_path)
    row = {
        "sample_id": sample_id,
        "property_id": property_id,
        "period": period,
        "x": x,
        "y": y,
        "bbox_xmin": bbox[0],
        "bbox_ymin": bbox[1],
        "bbox_xmax": bbox[2],
        "bbox_ymax": bbox[3],
        "satellite_status": satellite_status,
        "land_cover_status": land_cover_status,
        "download_timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    with open(path, "a", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=MANIFEST_COLUMNS, delimiter=";")
        writer.writerow(row)


# Backward compatibility aliases
inicializar_manifesto = initialize_manifest
carregar_amostras_processadas = load_processed_samples
registrar_resultado = record_result
