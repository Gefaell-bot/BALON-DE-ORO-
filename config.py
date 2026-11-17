"""Configuración compartida por ETL, ML, scripts y backend."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "data" / "raw"
MODEL_PATH = ROOT / "ml" / "model.joblib"

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "balon_de_oro")

# Temporadas que se muestran en la app y alimentan las colecciones TimeSeries.
# 2024 = 2024-25 (Balón de Oro 2025, ya entregado → sirve de validación)
# 2025 = 2025-26 (Balón de Oro 2026)
APP_SEASONS = [2024, 2025]
FIRST_SEASON = 2012  # primer año con datos por partido completos

# Mínimo de minutos en la temporada para entrar en la lista de candidatos.
MIN_MINUTES = 1200


def season_label(start_year: int) -> str:
    return f"{start_year}-{str(start_year + 1)[-2:]}"
