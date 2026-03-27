"""
fetch_data.py — Équivalent Lambda "Retrieve Data"
Télécharge les données de comptage routier Paris Open Data et les sauvegarde en JSON brut.
"""

import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

# Forcer UTF-8 sur le terminal Windows (cp1252 ne supporte pas les emojis)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_RAW_DIR, LOG_DIR, OPEN_DATA_API_URL, OPEN_DATA_PAGE_SIZE

RAW_DIR = DATA_RAW_DIR
RAW_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("fetch_data")
if not log.handlers:
    log.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] fetch_data — %(message)s")
    _fh = logging.FileHandler(LOG_DIR / "fetch_data.log", encoding="utf-8")
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)
    log.propagate = True

API_URL   = OPEN_DATA_API_URL
PAGE_SIZE = OPEN_DATA_PAGE_SIZE


def fetch_data(target_date: str = None) -> str:
    """
    Télécharge les données de comptage routier pour la date cible.
    Retourne le chemin absolu du fichier JSON sauvegardé.
    """
    if target_date is None:
        target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    date_from = f"{target_date}T00:00:00"
    date_to = f"{target_date}T23:59:59"
    where_clause = f"t_1h >= '{date_from}' AND t_1h <= '{date_to}'"

    log.info(f"Début du téléchargement — date cible : {target_date}")

    all_records = []
    offset = 0

    while True:
        params = {
            "select": "iu_ac,libelle,t_1h,q,k,etat_barre",
            "where": where_clause,
            "limit": PAGE_SIZE,
            "offset": offset,
            "timezone": "Europe/Paris",
        }

        try:
            resp = requests.get(API_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.HTTPError as exc:
            # L'API Paris Open Data refuse offset >= 10000 avec HTTP 400
            if resp.status_code == 400 and offset > 0:
                log.warning(f"Limite API atteinte (offset={offset}), arret de la pagination ({len(all_records)} enreg. recuperes)")
                break
            log.error(f"Erreur HTTP (offset={offset}) : {exc}")
            raise
        except requests.RequestException as exc:
            log.error(f"Erreur reseau (offset={offset}) : {exc}")
            raise

        records = data.get("results", [])
        if not records:
            break

        all_records.extend(records)
        total_count = data.get("total_count", 0)
        log.info(f"  Page offset={offset} -> {len(records)} lignes | total API : {total_count}")

        offset += PAGE_SIZE
        if offset >= total_count:
            break

    date_tag = target_date.replace("-", "")
    filename = f"comptages_veille_{date_tag}.json"
    filepath = RAW_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(all_records, f, ensure_ascii=False, indent=2)

    log.info(f"{len(all_records)} enregistrements -> {filepath}")
    return str(filepath)


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    fetch_data(date_arg)
