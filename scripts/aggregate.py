"""
aggregate.py — Équivalent EC2 "Raw to Aggregate"
Lit la table 'raw' et produit des métriques journalières par arc dans 'aggregated'.
Métriques :
  - debit_moyen_horaire  (moyenne de q sur la journée)
  - total_vehicules      (somme de q)
  - nb_mesures           (nombre de relevés)
  - vitesse_moyenne      (proxy : 100 - moyenne(k), car k est le taux d'occupation)
  - heures_congestion    (nb de créneaux horaires où k > 90, équivalent vitesse_proxy < 10)
  - heure_pic            (heure avec le débit max)
  - etat_trafic          (classification : fluide / ralenti / congestionné)
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
from config import (
    DB_PATH, LOG_DIR,
    SEUIL_CONGESTION_VITESSE, SEUIL_RALENTI_VITESSE,
    SEUIL_CONGESTION_HEURES, SEUIL_RALENTI_HEURES,
    MIN_MESURES_HIGH, MIN_MESURES_MEDIUM,
)

LOG_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("aggregate")
if not log.handlers:
    log.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] aggregate — %(message)s")
    _fh = logging.FileHandler(LOG_DIR / "aggregate.log", encoding="utf-8")
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)
    log.propagate = True

DDL_AGG = """
CREATE TABLE IF NOT EXISTS aggregated (
    arc_id               TEXT NOT NULL,
    libelle              TEXT,
    date                 TEXT NOT NULL,
    debit_moyen_horaire  REAL,
    total_vehicules      REAL,
    nb_mesures           INTEGER,
    vitesse_moyenne      REAL,
    vitesse_confidence   TEXT,
    heures_congestion    INTEGER,
    heure_pic            INTEGER,
    etat_trafic          TEXT,
    aggregated_at        TEXT,
    PRIMARY KEY (arc_id, date)
);
"""


def classify_traffic(vitesse: float, heures_cong: int) -> str:
    """
    Classifie l'état de trafic global d'un arc pour une journée.
    vitesse_moyenne = proxy = clip(100 - k_moyen, 0, 100)
      0  = route bloquée (k=100%)
      100 = route vide  (k=0%)
    Seuils issus de config.py.
    """
    if pd.isna(vitesse):
        return "inconnu"
    if vitesse < SEUIL_CONGESTION_VITESSE or heures_cong >= SEUIL_CONGESTION_HEURES:
        return "congestionné"
    if vitesse < SEUIL_RALENTI_VITESSE or heures_cong >= SEUIL_RALENTI_HEURES:
        return "ralenti"
    return "fluide"


def classify_confidence(nb_mesures: int) -> str:
    """Niveau de confiance basé sur le nombre de relevés horaires disponibles."""
    if nb_mesures >= MIN_MESURES_HIGH:
        return "HIGH"
    if nb_mesures >= MIN_MESURES_MEDIUM:
        return "MEDIUM"
    return "LOW"


def _safe_heure_pic(group: pd.DataFrame) -> int:
    """Retourne l'heure du débit max, -1 si indisponible."""
    valid = group.dropna(subset=["q"])
    if valid.empty:
        return -1
    return int(valid.loc[valid["q"].idxmax(), "heure"])


def aggregate(target_date: str = None) -> None:
    """
    Calcule et insère/met à jour les agrégats journaliers.
    Si target_date est None, traite toutes les dates absentes de 'aggregated'.
    """
    if not DB_PATH.exists():
        log.error(f"Base de données introuvable : {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(DDL_AGG)
        # Migration : ajoute vitesse_confidence si la table existait avant cette version
        try:
            conn.execute("ALTER TABLE aggregated ADD COLUMN vitesse_confidence TEXT")
        except sqlite3.OperationalError:
            pass  # colonne déjà présente
        conn.commit()

    if target_date:
        # Requete parametree (pas de f-string avec la date pour eviter SQL injection)
        log.info(f"Agregation pour la date {target_date}")
        df = pd.read_sql(
            "SELECT arc_id, libelle, t_1h, q, k, etat_barre FROM raw WHERE DATE(t_1h) = ?",
            conn,
            params=(target_date,),
        )
    else:
        log.info("Agregation de toutes les dates manquantes")
        df = pd.read_sql(
            """
            SELECT arc_id, libelle, t_1h, q, k, etat_barre FROM raw
            WHERE DATE(t_1h) NOT IN (SELECT DISTINCT date FROM aggregated)
            """,
            conn,
        )

    if df.empty:
        log.info("Aucune donnée brute à agréger.")
        return

    log.info(f"  {len(df)} lignes brutes chargées")

    # Ignorer les mesures de capteurs defaillants (etat_barre = '0')
    if "etat_barre" in df.columns:
        nb_exclus = (df["etat_barre"] == "0").sum()
        if nb_exclus > 0:
            log.info(f"  {nb_exclus} mesures exclues (capteurs defaillants etat_barre=0)")
            df = df[df["etat_barre"] != "0"].copy()

    # Parsing temporel
    df["_dt"]   = pd.to_datetime(df["t_1h"], utc=True, errors="coerce")
    df["date"]  = df["_dt"].dt.strftime("%Y-%m-%d")
    df["heure"] = df["_dt"].dt.hour
    df["q"]     = pd.to_numeric(df["q"], errors="coerce")
    df["k"]     = pd.to_numeric(df["k"], errors="coerce")

    # heures_congestion : coherent avec vitesse_moyenne (proxy = 100 - k)
    # congestion quand vitesse_proxy < SEUIL_CONGESTION_VITESSE
    # soit : 100 - k < SEUIL_CONGESTION_VITESSE  <=>  k > 100 - SEUIL_CONGESTION_VITESSE
    _k_cong_threshold = 100 - SEUIL_CONGESTION_VITESSE
    df["is_congested"] = df["k"] > _k_cong_threshold

    # Agrégation principale
    agg = (
        df.groupby(["arc_id", "libelle", "date"])
        .agg(
            debit_moyen_horaire=("q", "mean"),
            total_vehicules=("q", "sum"),
            nb_mesures=("q", "count"),
            k_mean=("k", "mean"),
            heures_congestion=("is_congested", "sum"),
        )
        .reset_index()
    )

    # vitesse_moyenne = proxy = clip(100 - taux d'occupation moyen, 0, 100)
    # Formule : quand k=0% (route vide) -> vitesse=100 (max)
    #           quand k=100% (bouchon) -> vitesse=0 (bloque)
    agg["vitesse_moyenne"] = (100 - agg["k_mean"]).clip(0, 100).round(2)
    agg["vitesse_confidence"] = agg["nb_mesures"].apply(classify_confidence)
    agg.drop(columns=["k_mean"], inplace=True)

    # Heure de pointe
    heure_pic_series = (
        df.groupby(["arc_id", "date"])
        .apply(_safe_heure_pic)
        .reset_index(name="heure_pic")
    )
    agg = agg.merge(heure_pic_series, on=["arc_id", "date"], how="left")

    # Classification trafic
    agg["etat_trafic"] = agg.apply(
        lambda r: classify_traffic(r["vitesse_moyenne"], int(r["heures_congestion"])),
        axis=1,
    )

    # Arrondi
    agg["debit_moyen_horaire"] = agg["debit_moyen_horaire"].round(2)
    agg["total_vehicules"]     = agg["total_vehicules"].round(0)
    agg["heures_congestion"]   = agg["heures_congestion"].astype(int)
    agg["aggregated_at"] = datetime.now(timezone.utc).isoformat()

    # Upsert dans la table aggregated
    with sqlite3.connect(DB_PATH) as conn:
        for _, row in agg.iterrows():
            conn.execute(
                """
                INSERT INTO aggregated (
                    arc_id, libelle, date, debit_moyen_horaire, total_vehicules,
                    nb_mesures, vitesse_moyenne, vitesse_confidence,
                    heures_congestion, heure_pic, etat_trafic, aggregated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(arc_id, date) DO UPDATE SET
                    libelle             = excluded.libelle,
                    debit_moyen_horaire = excluded.debit_moyen_horaire,
                    total_vehicules     = excluded.total_vehicules,
                    nb_mesures          = excluded.nb_mesures,
                    vitesse_moyenne     = excluded.vitesse_moyenne,
                    vitesse_confidence  = excluded.vitesse_confidence,
                    heures_congestion   = excluded.heures_congestion,
                    heure_pic          = excluded.heure_pic,
                    etat_trafic         = excluded.etat_trafic,
                    aggregated_at       = excluded.aggregated_at
                """,
                (
                    row["arc_id"], row["libelle"], row["date"],
                    row["debit_moyen_horaire"], row["total_vehicules"],
                    int(row["nb_mesures"]), row["vitesse_moyenne"],
                    row["vitesse_confidence"],
                    row["heures_congestion"], row.get("heure_pic"),
                    row["etat_trafic"], row["aggregated_at"],
                ),
            )
        conn.commit()

    log.info(f"{len(agg)} arcs agreges pour les dates traitees")


if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    aggregate(date_arg)
