"""
utils/palette.py
Official Land Use and Land Cover (LULC) RGB Color Palette Mapping.

Maintains visual and semantic consistency between:
  1. The 2019–2020 WMS thematic classified map (IJSN / GeoBases)
  2. The 2012–2015 vector shapefile land cover rasterization (IEMA / GeoBases)

All color values follow the official 8-bit RGB color table established
by GeoBases do Estado do Espírito Santo.
"""

from typing import Dict, Tuple

# Official RGB palette mapping for land use and land cover classes
LULC_COLOR_PALETTE: Dict[str, Tuple[int, int, int]] = {
    # --- Standard 2019-2020 WMS Class Labels ---
    "Afloramento Rochoso": (150, 150, 150),
    "Área Edificada": (251, 154, 153),
    "Brejo": (69, 175, 213),
    "Campo Rupestre/Altitude": (150, 109, 207),
    "Cultivo Agrícola - Abacaxi": (128, 214, 16),
    "Cultivo Agrícola - Banana": (247, 223, 8),
    "Cultivo Agrícola - Café": (119, 9, 29),
    "Cultivo Agrícola - Cana-de-Açúcar": (209, 163, 117),
    "Cultivo Agrícola - Coco-da-Baía": (231, 67, 97),
    "Cultivo Agrícola - Mamão": (245, 141, 23),
    "Outros Cultivos Permanentes": (55, 196, 201),
    "Outros Cultivos Temporários": (225, 175, 38),
    "Extração Mineração": (81, 77, 77),
    "Macega": (211, 127, 122),
    "Mangue": (156, 68, 203),
    "Massa D'Água": (133, 196, 221),
    "Mata Nativa": (13, 103, 19),
    "Mata em Regeneração": (51, 160, 44),
    "Outros": (31, 205, 170),
    "Pastagem": (178, 214, 32),
    "Eucalipto": (207, 103, 65),
    "Pinus": (243, 184, 129),
    "Seringueira": (151, 132, 233),
    "Restinga": (63, 231, 161),
    "Solo Exposto": (245, 222, 193),

    # --- 2012-2015 Vector Shapefile Naming Variants ---
    # Normalizes historical nomenclature differences to ensure uniform training labels
    "Mata Nativa em Estágio Inicial de Regeneração": (51, 160, 44),
    "Cultivo Agrícola - Outros Cultivos Permanentes": (55, 196, 201),
    "Reflorestamento - Eucalipto": (207, 103, 65),
    "Cultivo Agrícola - Coco-Da-Baía": (231, 67, 97),
    "Cultivo Agrícola - Cana-De-Açúcar": (209, 163, 117),
    "Reflorestamento - Seringueira": (151, 132, 233),
    "Reflorestamento - Pinus": (243, 184, 129),
    "Cultivo Agrícola - Outros Cultivos Temporários": (225, 175, 38),
}

# Backward compatibility alias
CORES_CLASSES_RGB = LULC_COLOR_PALETTE


def get_class_color(class_name: str, fallback: Tuple[int, int, int] = (0, 0, 0)) -> Tuple[int, int, int]:
    """
    Returns the RGB tuple corresponding to a given land cover class name.
    Falls back to a default value (default: black / background) if unmapped.
    """
    cleaned = class_name.strip()
    return LULC_COLOR_PALETTE.get(cleaned, fallback)
