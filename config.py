"""
config.py — Configuration centralisée CityFlow
Toutes les valeurs configurables sont ici. Les variables d'environnement
ont la priorité sur les valeurs par défaut.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ── Chemins des données ───────────────────────────────────────
DATA_RAW_DIR     = BASE_DIR / os.environ.get("CITYFLOW_RAW_DIR",     "data/raw")
DATA_CLEAN_DIR   = BASE_DIR / os.environ.get("CITYFLOW_CLEAN_DIR",   "data/clean")
DATA_ARCHIVE_DIR = BASE_DIR / os.environ.get("CITYFLOW_ARCHIVE_DIR", "data/archive")
DATA_ERROR_DIR   = BASE_DIR / os.environ.get("CITYFLOW_ERROR_DIR",   "data/errors")
DB_PATH          = BASE_DIR / os.environ.get("CITYFLOW_DB_PATH",     "db/cityflow.db")
LOG_DIR          = BASE_DIR / os.environ.get("CITYFLOW_LOG_DIR",     "logs")

# ── API source (Paris Open Data) ─────────────────────────────
OPEN_DATA_API_URL = os.environ.get(
    "CITYFLOW_OPEN_DATA_URL",
    "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"
    "/comptages-routiers-permanents/records",
)
OPEN_DATA_PAGE_SIZE = int(os.environ.get("CITYFLOW_PAGE_SIZE", "100"))

# ── API locale ───────────────────────────────────────────────
API_HOST = os.environ.get("CITYFLOW_API_HOST", "localhost")
try:
    API_PORT = int(os.environ.get("CITYFLOW_API_PORT", "8000"))
except ValueError:
    raise ValueError("CITYFLOW_API_PORT doit etre un entier (ex: 8000)")

# Clés API chargées depuis l'environnement (ne jamais hardcoder dans le code)
# Format : "viewer:viewer-key-001,admin:admin-key-999"
_raw_keys = os.environ.get("CITYFLOW_API_KEYS", "viewer:viewer-key-001,admin:admin-key-999")
API_KEYS: dict = {}
for entry in _raw_keys.split(","):
    if ":" in entry:
        role, key = entry.split(":", 1)
        API_KEYS[key.strip()] = role.strip()

# Domaines autorisés pour CORS (séparés par virgule)
CORS_ORIGINS = os.environ.get("CITYFLOW_CORS_ORIGINS", "http://localhost:8501").split(",")

# ── Règles de gouvernance / qualité ──────────────────────────
TECH_VERSION        = os.environ.get("CITYFLOW_VERSION", "1.0.0")

def _float_env(name: str, default: str) -> float:
    val = os.environ.get(name, default)
    try:
        return float(val)
    except ValueError:
        raise ValueError(f"Variable d'environnement {name} doit etre un nombre (recu : '{val}')")

def _int_env(name: str, default: str) -> int:
    val = os.environ.get(name, default)
    try:
        return int(val)
    except ValueError:
        raise ValueError(f"Variable d'environnement {name} doit etre un entier (recu : '{val}')")

MAX_DEBIT           = _float_env("CITYFLOW_MAX_DEBIT",          "10000")  # veh/h au-dela = outlier
MAX_TAUX_OCCUPATION = _float_env("CITYFLOW_MAX_K",             "100")
MIN_MESURES_HIGH    = _int_env(  "CITYFLOW_MIN_MESURES_HIGH",  "20")     # nb mesures pour confidence HIGH
MIN_MESURES_MEDIUM  = _int_env(  "CITYFLOW_MIN_MESURES_MEDIUM", "5")

# Seuils de classification trafic
SEUIL_CONGESTION_VITESSE = _float_env("CITYFLOW_SEUIL_CONG_V", "10")
SEUIL_RALENTI_VITESSE    = _float_env("CITYFLOW_SEUIL_RAL_V",  "25")
SEUIL_CONGESTION_HEURES  = _int_env(  "CITYFLOW_SEUIL_CONG_H", "3")
SEUIL_RALENTI_HEURES     = _int_env(  "CITYFLOW_SEUIL_RAL_H",  "1")
