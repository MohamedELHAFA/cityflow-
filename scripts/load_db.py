"""
load_db.py — Équivalent Lambda "S3 to DynamoDB"
Charge les fichiers Parquet nettoyés dans la table 'raw' de la base SQLite locale.
Évite les doublons (idempotent).
"""

import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DATA_CLEAN_DIR, DB_PATH, LOG_DIR

CLEAN_DIR = DATA_CLEAN_DIR
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("load_db")
if not log.handlers:
    log.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] load_db — %(message)s")
    _fh = logging.FileHandler(LOG_DIR / "load_db.log", encoding="utf-8")
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)
    log.propagate = True

# ──────────────────────────────────────────────────────────────
# Schéma de la table 'raw'
# ──────────────────────────────────────────────────────────────
DDL_RAW = """
CREATE TABLE IF NOT EXISTS raw (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    arc_id          TEXT    NOT NULL,
    libelle         TEXT,
    t_1h            TEXT,
    q               REAL,
    k               REAL,
    etat_barre      TEXT,
    tech_version    TEXT,
    tech_updated_at TEXT,
    source_file     TEXT,
    quality_score   REAL,
    loaded_at       TEXT    DEFAULT (datetime('now', 'utc')),
    UNIQUE (arc_id, t_1h)
);
"""

DDL_AUDIT = """
CREATE TABLE IF NOT EXISTS pipeline_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT    NOT NULL,
    source_file     TEXT    NOT NULL,
    rows_read       INTEGER,
    rows_inserted   INTEGER,
    rows_skipped    INTEGER,
    status          TEXT
);
"""

DDL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_raw_arc_t1h ON raw (arc_id, t_1h);
"""

KNOWN_COLS = [
    "arc_id", "libelle", "t_1h", "q", "k", "etat_barre",
    "tech_version", "tech_updated_at", "source_file", "quality_score",
]


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(DDL_RAW)
    conn.execute(DDL_AUDIT)
    conn.execute(DDL_INDEX)
    # Migration : ajoute quality_score si la table existait avant cette version
    try:
        conn.execute("ALTER TABLE raw ADD COLUMN quality_score REAL")
    except sqlite3.OperationalError:
        pass  # colonne déjà présente
    conn.commit()


def load_parquet(parquet_path: str) -> int:
    """
    Insère les lignes d'un fichier Parquet dans la table raw.
    Idempotent via ON CONFLICT IGNORE sur UNIQUE(arc_id, t_1h).
    Enregistre chaque exécution dans pipeline_audit (traçabilité linéage).
    Retourne le nombre de lignes réellement insérées.
    """
    from datetime import datetime, timezone

    path = Path(parquet_path)
    log.info(f"=== Chargement DB : {path.name} ===")

    df = pd.read_parquet(path, engine="pyarrow")
    rows_read = len(df)

    # Garde uniquement les colonnes connues du schéma
    df = df[[c for c in KNOWN_COLS if c in df.columns]].copy()

    with sqlite3.connect(DB_PATH) as conn:
        _init_db(conn)

        # Insertion avec IGNORE en cas de doublon (arc_id, t_1h)
        inserted = 0
        for record in df.to_dict(orient="records"):
            cols = list(record.keys())
            placeholders = ",".join(["?"] * len(cols))
            col_names = ",".join(cols)
            try:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO raw ({col_names}) VALUES ({placeholders})",
                    list(record.values()),
                )
                inserted += cur.rowcount
            except sqlite3.Error as exc:
                log.warning(f"  Erreur insertion: {exc}")

        skipped = rows_read - inserted
        conn.commit()

        # Ligne d'audit (traçabilité linéage)
        conn.execute(
            """
            INSERT INTO pipeline_audit (run_at, source_file, rows_read, rows_inserted, rows_skipped, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                path.name, rows_read, inserted, skipped,
                "ok" if inserted > 0 or skipped == rows_read else "partial",
            ),
        )
        conn.commit()

    if skipped:
        log.info(f"  {skipped}/{rows_read} lignes ignorées (doublons UNIQUE)")
    log.info(f"  {inserted} lignes insérées dans raw")
    return inserted


if __name__ == "__main__":
    if len(sys.argv) > 1:
        load_parquet(sys.argv[1])
    else:
        for parquet_file in sorted(CLEAN_DIR.glob("*.parquet")):
            load_parquet(str(parquet_file))
