"""
process.py — Équivalent Lambda "Process & Validation"
Applique les règles de gouvernance :
  - Validation (nulls, types, valeurs hors limites)
  - Dédoublonnage
  - Ajout colonnes techniques (tech_version, tech_updated_at, source_file)
  - Export Parquet vers data/clean/
  - Enregistrements rejetés → data/errors/ (DLQ local)
"""

import json
import logging
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Chemin du dossier scripts/ -> remonter pour trouver config.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (
    DATA_RAW_DIR, DATA_CLEAN_DIR, DATA_ARCHIVE_DIR, DATA_ERROR_DIR,
    LOG_DIR, TECH_VERSION, MAX_DEBIT, MAX_TAUX_OCCUPATION,
)

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR     = DATA_RAW_DIR
CLEAN_DIR   = DATA_CLEAN_DIR
ARCHIVE_DIR = DATA_ARCHIVE_DIR
ERROR_DIR   = DATA_ERROR_DIR

for _d in [CLEAN_DIR, ARCHIVE_DIR, ERROR_DIR, LOG_DIR]:
    _d.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("process")
if not log.handlers:
    log.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] process — %(message)s")
    _fh = logging.FileHandler(LOG_DIR / "process.log", encoding="utf-8")
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)
    log.propagate = True

_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


# ──────────────────────────────────────────────────────────────
# Règles de validation (gouvernance qualité des données)
# ──────────────────────────────────────────────────────────────

def validate_record(row: pd.Series) -> tuple[bool, str, float]:
    """
    Retourne (ok: bool, raison: str, quality_score: float 0.0-1.0).
    Règles :
      1. arc_id obligatoire et non vide
      2. t_1h obligatoire et format ISO datetime
      3. q (débit) >= 0 et <= MAX_DEBIT si présent
      4. k (taux d'occupation) dans [0, 100] si présent
    Le quality_score reflète la proportion de champs valides présents.
    """
    score_fields = 0
    total_fields = 4  # arc_id, t_1h, q, k

    # Règle 1 : arc_id
    arc = row.get("arc_id", None)
    if pd.isna(arc) or str(arc).strip() == "":
        return False, "arc_id manquant ou vide", 0.0
    score_fields += 1

    # Règle 2 : t_1h obligatoire + format ISO
    t = row.get("t_1h", None)
    if pd.isna(t) or str(t).strip() == "":
        return False, "t_1h manquant", 0.25
    if not _TS_RE.match(str(t).strip()):
        return False, f"t_1h format invalide : {t}", 0.25
    score_fields += 1

    # Règle 3 : q dans [0, MAX_DEBIT]
    q = row.get("q", None)
    if q is not None and not pd.isna(q):
        qf = float(q)
        if qf < 0:
            return False, f"débit négatif : {q}", score_fields / total_fields
        if qf > MAX_DEBIT:
            return False, f"débit anormal (>{MAX_DEBIT}) : {q}", score_fields / total_fields
        score_fields += 1
    # q absent = champ optionnel non décompté comme erreur bloquante

    # Règle 4 : k dans [0, 100]
    k = row.get("k", None)
    if k is not None and not pd.isna(k):
        kf = float(k)
        if kf < 0 or kf > MAX_TAUX_OCCUPATION:
            return False, f"taux d'occupation hors [0,100] : {k}", score_fields / total_fields
        score_fields += 1

    quality = round(score_fields / total_fields, 2)
    return True, "", quality


# ──────────────────────────────────────────────────────────────
# Traitement principal
# ──────────────────────────────────────────────────────────────

def process_file(raw_filepath: str) -> str:
    """
    Traite un fichier JSON brut.
    Retourne le chemin du fichier Parquet produit, ou "" en cas d'échec.
    """
    filepath = Path(raw_filepath)
    log.info(f"=== Traitement de {filepath.name} ===")

    # 1. Chargement
    with open(filepath, encoding="utf-8") as f:
        records = json.load(f)

    log.info(f"  Chargé : {len(records)} enregistrements bruts")

    if not records:
        log.warning("  Fichier vide — rien à traiter")
        return ""

    df = pd.DataFrame(records)

    # Renommage iu_ac → arc_id (cohérence avec la DB)
    if "iu_ac" in df.columns:
        df = df.rename(columns={"iu_ac": "arc_id"})

    # Normalisation des types numériques
    for col in ("q", "k"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # 2. Validation enregistrement par enregistrement
    valid_flags = []
    reject_reasons = []
    quality_scores = []
    for _, row in df.iterrows():
        ok, reason, q_score = validate_record(row)
        valid_flags.append(ok)
        reject_reasons.append(reason)
        quality_scores.append(q_score)

    mask = pd.Series(valid_flags, index=df.index)
    df_valid   = df[mask].copy()
    df_invalid = df[~mask].copy()
    df_invalid["reject_reason"] = [r for r, v in zip(reject_reasons, valid_flags) if not v]
    df_valid["quality_score"] = [s for s, v in zip(quality_scores, valid_flags) if v]

    mean_quality = df_valid["quality_score"].mean() if not df_valid.empty else 0.0
    log.info(
        f"  Validation -> valides : {len(df_valid)} | rejetes : {len(df_invalid)} "
        f"| qualite moyenne : {mean_quality:.2f}"
    )

    # Sauvegarde des rejetés (DLQ local — traçabilité)
    if not df_invalid.empty:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        err_path = ERROR_DIR / f"errors_{filepath.stem}_{ts}.csv"
        df_invalid.to_csv(err_path, index=False)
        log.warning(f"  {len(df_invalid)} enregistrements rejetés → {err_path}")

    if df_valid.empty:
        log.error("  Aucun enregistrement valide — pipeline interrompu")
        return ""

    # 3. Dédoublonnage (gouvernance : drop duplicates)
    before = len(df_valid)
    df_valid = df_valid.drop_duplicates(subset=["arc_id", "t_1h"])
    removed = before - len(df_valid)
    if removed:
        log.info(f"  Doublons supprimés : {removed}")

    # 4. Colonnes de gouvernance / traçabilité
    now_utc = datetime.now(timezone.utc).isoformat()
    df_valid["tech_version"]    = TECH_VERSION
    df_valid["tech_updated_at"] = now_utc
    df_valid["source_file"]     = filepath.name

    # 5. Export Parquet (gouvernance : format standardisé)
    parquet_name = filepath.stem + ".parquet"
    parquet_path = CLEAN_DIR / parquet_name
    df_valid.to_parquet(parquet_path, index=False, engine="pyarrow")
    log.info(f"  [OK] Parquet exporte : {parquet_path} ({len(df_valid)} lignes)")

    # 6. Archivage du brut (équivalent S3 Lifecycle → Glacier)
    archive_path = ARCHIVE_DIR / filepath.name
    shutil.copy2(filepath, archive_path)
    log.info(f"  [ARCHIVE] Brut archive : {archive_path}")

    return str(parquet_path)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        process_file(sys.argv[1])
    else:
        # Traite tous les JSON bruts qui n'ont pas encore leur Parquet
        for json_file in sorted(RAW_DIR.glob("*.json")):
            parquet = CLEAN_DIR / (json_file.stem + ".parquet")
            if not parquet.exists():
                process_file(str(json_file))
