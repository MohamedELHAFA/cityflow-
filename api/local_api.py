"""
local_api.py — Équivalent API Gateway + Lambda
API REST locale (FastAPI) qui expose les données agrégées depuis SQLite.
Compatible avec app_local.py (même format de réponse que l'API AWS d'origine).

Lancement :
  uvicorn api.local_api:app --reload --port 8000
  # ou depuis le dossier cityflow/ :
  python -m uvicorn api.local_api:app --port 8000
"""

import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

# Chargement de la config centralisee
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import API_KEYS, CORS_ORIGINS, DB_PATH, LOG_DIR

LOG_DIR.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("api")
if not log.handlers:
    log.setLevel(logging.INFO)
    _fmt = logging.Formatter("%(asctime)s [%(levelname)s] api — %(message)s")
    _fh = logging.FileHandler(LOG_DIR / "api.log", encoding="utf-8")
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)
    log.propagate = True

# Regex de validation de date (YYYY-MM-DD)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

app = FastAPI(
    title="CityFlow Local API",
    version="1.0.0",
    description="Replique locale de l'API Gateway AWS CityFlow",
)

# CORS restreint aux origines connues (ne jamais utiliser ["*"] en production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET"],
    allow_headers=["X-API-Key", "Content-Type"],
)


def check_access(
    api_key: Optional[str] = Query(None, alias="api_key"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> str:
    """
    Verifie la cle API (controle d'acces RBAC — simulation IAM).
    La cle peut etre passee via le header HTTP 'X-API-Key' (recommande)
    ou via le parametre de requete 'api_key' (retro-compatibilite).
    Sans cle : acces public (lecture seule agregats + dates).
    Cle invalide : HTTP 403.
    Les cles sont chargees depuis la variable d'env CITYFLOW_API_KEYS.
    """
    key = x_api_key or api_key
    if key is None:
        return "public"
    role = API_KEYS.get(key)
    if role is None:
        log.warning("Tentative d'acces avec cle invalide")
        raise HTTPException(status_code=403, detail="Cle API invalide ou non autorisee")
    return role


# Niveaux de privilege numeriques pour comparaison
_ROLE_LEVEL = {"public": 0, "viewer": 1, "admin": 2}


def require_role(min_role: str):
    """Dependance FastAPI : leve HTTP 403 si le role est insuffisant."""
    def _check(role: str = Depends(check_access)):
        if _ROLE_LEVEL.get(role, 0) < _ROLE_LEVEL[min_role]:
            raise HTTPException(
                status_code=403,
                detail=f"Acces refuse. Role requis : {min_role} (actuel : {role})",
            )
        return role
    return _check


def _validate_date(date_str: str) -> None:
    """Valide le format YYYY-MM-DD et que la date est calendairement valide."""
    if not _DATE_RE.match(date_str):
        raise HTTPException(status_code=400, detail="Format de date invalide. Utiliser YYYY-MM-DD")
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Date invalide : {date_str}")


def get_db():
    """Fournit une connexion SQLite (injection de dépendance FastAPI)."""
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                "Base de données non initialisée. "
                "Lancez d'abord : python scripts/pipeline.py"
            ),
        )
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Verifie que l'API et la base sont accessibles."""
    # Ne pas exposer le chemin disque dans la reponse (securite)
    return {
        "status": "ok",
        "db_available": DB_PATH.exists(),
        "version": "1.0.0",
    }


@app.get("/aggregated")
def get_aggregated(
    request: Request,
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
    limit: int = Query(200, ge=1, le=500, description="Lignes par page"),
    nextToken: Optional[str] = Query(None, description="Offset pagination (opaque)"),
    db: sqlite3.Connection = Depends(get_db),
    _role: str = Depends(check_access),
):
    """
    Retourne les donnees agregees pour une date (paginees).
    Reponse compatible avec l'API AWS d'origine :
      { "items": [...], "total": N, "nextToken": "..." }
    """
    # Validation stricte du parametre date (protection injection SQL)
    _validate_date(date)
    log.info(f"GET /aggregated date={date} limit={limit} role={_role} ip={request.client.host}")

    offset = 0
    if nextToken:
        try:
            offset = int(nextToken)
        except ValueError:
            offset = 0

    try:
        cursor = db.execute(
            """
            SELECT arc_id, libelle, date,
                   debit_moyen_horaire, total_vehicules, nb_mesures,
                   vitesse_moyenne, heures_congestion, heure_pic, etat_trafic
            FROM aggregated
            WHERE date = ?
            ORDER BY arc_id
            LIMIT ? OFFSET ?
            """,
            (date, limit, offset),
        )
        items = [dict(row) for row in cursor.fetchall()]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    total = db.execute(
        "SELECT COUNT(*) FROM aggregated WHERE date = ?", (date,)
    ).fetchone()[0]

    response: dict = {
        "items":  items,
        "total":  total,
        "offset": offset,
        "limit":  limit,
    }
    next_offset = offset + limit
    if next_offset < total:
        response["nextToken"] = str(next_offset)

    return response


@app.get("/aggregated/dates")
def get_dates(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    _role: str = Depends(check_access),
):
    """Liste les dates disponibles dans la base (30 dernieres)."""
    log.info(f"GET /aggregated/dates ip={request.client.host}")
    cursor = db.execute(
        "SELECT DISTINCT date FROM aggregated ORDER BY date DESC LIMIT 30"
    )
    return {"dates": [row[0] for row in cursor.fetchall()]}


@app.get("/aggregated/stats")
def get_stats(
    request: Request,
    date: str = Query(..., description="Date au format YYYY-MM-DD"),
    db: sqlite3.Connection = Depends(get_db),
    _role: str = Depends(require_role("viewer")),
):
    """Statistiques resumees pour une date (KPIs dashboard)."""
    _validate_date(date)
    log.info(f"GET /aggregated/stats date={date} ip={request.client.host}")
    row = db.execute(
        """
        SELECT
            COUNT(*)                     AS nb_arcs,
            ROUND(AVG(vitesse_moyenne),2) AS vitesse_moy,
            ROUND(SUM(total_vehicules),0) AS total_veh,
            ROUND(AVG(debit_moyen_horaire),2) AS debit_moy,
            SUM(CASE WHEN etat_trafic='congestionné' THEN 1 ELSE 0 END) AS arcs_congestionnes,
            SUM(CASE WHEN etat_trafic='ralenti'      THEN 1 ELSE 0 END) AS arcs_ralentis,
            SUM(CASE WHEN etat_trafic='fluide'       THEN 1 ELSE 0 END) AS arcs_fluides
        FROM aggregated
        WHERE date = ?
        """,
        (date,),
    ).fetchone()

    if row is None or row[0] == 0:
        raise HTTPException(status_code=404, detail=f"Aucune donnee pour {date}")

    keys = [
        "nb_arcs", "vitesse_moy", "total_veh", "debit_moy",
        "arcs_congestionnes", "arcs_ralentis", "arcs_fluides",
    ]
    return dict(zip(keys, row))


@app.get("/pipeline/audit")
def get_audit(
    request: Request,
    limit: int = Query(50, ge=1, le=200, description="Nombre de lignes"),
    db: sqlite3.Connection = Depends(get_db),
    _role: str = Depends(require_role("admin")),
):
    """Historique des chargements pipeline (admin uniquement)."""
    log.info(f"GET /pipeline/audit limit={limit} ip={request.client.host}")
    cursor = db.execute(
        """
        SELECT run_at, source_file, rows_read, rows_inserted, rows_skipped, status
        FROM pipeline_audit
        ORDER BY run_at DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    return {"audit": rows, "count": len(rows)}
