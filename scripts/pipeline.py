"""
pipeline.py — Équivalent EventBridge + orchestration des Lambdas
Lance les 4 étapes du pipeline dans l'ordre :
  1. fetch_data   (téléchargement Open Data)
  2. process      (validation + Parquet)
  3. load_db      (Parquet → SQLite raw)
  4. aggregate    (raw → agrégats journaliers)
Usage :
  python pipeline.py              # traite hier
  python pipeline.py 2026-01-20  # traite une date précise
"""

import logging
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Forcer UTF-8 sur le terminal Windows (stdout ET stderr)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Permet l'import des scripts du meme dossier et de config.py
sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import LOG_DIR

LOG_DIR.mkdir(parents=True, exist_ok=True)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] pipeline — %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)

from fetch_data import fetch_data
from process    import process_file
from load_db    import load_parquet
from aggregate  import aggregate


def run_pipeline(target_date: str = None) -> None:
    if target_date is None:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # Validation de la date (format + calendaire)
    if not _DATE_RE.match(target_date):
        log.error(f"Format de date invalide : '{target_date}'. Utiliser YYYY-MM-DD.")
        sys.exit(1)
    try:
        datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        log.error(f"Date inexistante : '{target_date}'.")
        sys.exit(1)

    sep = "=" * 55
    log.info(sep)
    log.info(f"  CITYFLOW PIPELINE - date : {target_date}")
    log.info(sep)
    start_time = datetime.now()

    # Etape 1 : Telechargement
    log.info("ETAPE 1/4 - Telechargement (fetch_data)")
    try:
        raw_path = fetch_data(target_date)
    except Exception as exc:
        log.error(f"  [ERREUR] Telechargement echoue : {exc}")
        sys.exit(1)
    log.info(f"  -> {raw_path}")

    # Etape 2 : Validation + Parquet
    log.info("ETAPE 2/4 - Traitement & Validation (process)")
    try:
        parquet_path = process_file(raw_path)
    except Exception as exc:
        log.error(f"  [ERREUR] Traitement echoue : {exc}")
        sys.exit(1)

    if not parquet_path:
        log.error("  [ERREUR] Aucun Parquet produit - pipeline interrompu")
        sys.exit(1)
    log.info(f"  -> {parquet_path}")

    # Etape 3 : Chargement DB
    log.info("ETAPE 3/4 - Chargement base de donnees (load_db)")
    try:
        n_inserted = load_parquet(parquet_path)
    except Exception as exc:
        log.error(f"  [ERREUR] Chargement DB echoue : {exc}")
        sys.exit(1)
    log.info(f"  -> {n_inserted} lignes inserees")

    # Etape 4 : Agregation
    log.info("ETAPE 4/4 - Agregation (aggregate)")
    try:
        aggregate(target_date)
    except Exception as exc:
        log.error(f"  [ERREUR] Agregation echouee : {exc}")
        sys.exit(1)

    elapsed = (datetime.now() - start_time).total_seconds()
    log.info(sep)
    log.info(f"  [OK] PIPELINE TERMINE - duree : {elapsed:.1f}s")
    log.info(sep)


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if date_arg is not None:
        if not _DATE_RE.match(date_arg):
            print(f"[ERREUR] Format de date invalide : '{date_arg}'. Utiliser YYYY-MM-DD.")
            sys.exit(1)
        try:
            datetime.strptime(date_arg, "%Y-%m-%d")
        except ValueError:
            print(f"[ERREUR] Date inexistante : '{date_arg}'.")
            sys.exit(1)
    run_pipeline(date_arg)
