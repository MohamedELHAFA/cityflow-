# CityFlow — Pipeline de données trafic routier parisien

Pipeline local de gouvernance des données reproduisant une architecture AWS
(EventBridge → Lambda → S3 → DynamoDB → API Gateway → Streamlit) avec des
outils Python standards : pandas, SQLite, FastAPI, Streamlit.

**Source :** [Paris Open Data — Comptages routiers permanents](https://opendata.paris.fr/explore/dataset/comptages-routiers-permanents)

---

## Architecture

```
fetch_data.py  →  data/raw/       (JSON brut)
process.py     →  data/clean/     (Parquet validé)
                  data/errors/    (DLQ — rejetés)
                  data/archive/   (bruts archivés)
load_db.py     →  db/cityflow.db  (table raw + pipeline_audit)
aggregate.py   →  db/cityflow.db  (table aggregated)
local_api.py   →  http://localhost:8000  (API REST)
app_local.py   →  http://localhost:8501  (Dashboard Streamlit)
```

---

## Installation

```bash
# 1. Cloner / décompresser le projet
cd cityflow/

# 2. Créer un environnement virtuel (recommandé)
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/Mac

# 3. Installer les dépendances
pip install -r requirements.txt

# 4. Configurer les variables d'environnement
copy .env.example .env        # Windows
# cp .env.example .env        # Linux/Mac
# Editez .env pour définir vos clés API
```

---

## Utilisation

### Exécuter le pipeline (récupère les données d'hier)

```bash
python scripts/pipeline.py
```

### Exécuter pour une date précise

```bash
python scripts/pipeline.py 2026-03-25
```

### Lancer l'API REST

```bash
uvicorn api.local_api:app --port 8000 --reload
```

L'API est disponible sur http://localhost:8000
La documentation Swagger est sur http://localhost:8000/docs

### Lancer le tableau de bord

```bash
streamlit run app_local.py
```

Le dashboard est disponible sur http://localhost:8501

---

## Endpoints API

| Méthode | Endpoint | Rôle requis | Description |
|---|---|---|---|
| GET | `/health` | public | Santé de l'API |
| GET | `/aggregated?date=YYYY-MM-DD` | public | Données agrégées paginées |
| GET | `/aggregated/dates` | public | Dates disponibles |
| GET | `/aggregated/stats?date=YYYY-MM-DD` | viewer | KPIs journaliers |
| GET | `/pipeline/audit` | admin | Historique des chargements |

**Authentification :**
- Header HTTP (recommandé) : `X-API-Key: votre-cle`
- Paramètre URL (retro-compat) : `?api_key=votre-cle`

---

## Structure des fichiers

```
cityflow/
├── config.py              # Configuration centralisée (env vars)
├── app_local.py           # Dashboard Streamlit
├── requirements.txt       # Dépendances Python
├── .env.example           # Template variables d'environnement
├── .gitignore
├── scripts/
│   ├── pipeline.py        # Orchestrateur (équivalent EventBridge)
│   ├── fetch_data.py      # Téléchargement API (équivalent Lambda)
│   ├── process.py         # Validation + Parquet (équivalent Lambda)
│   ├── load_db.py         # Chargement SQLite (équivalent Lambda)
│   └── aggregate.py       # Agrégation journalière (équivalent EC2)
├── api/
│   └── local_api.py       # API REST FastAPI (équivalent API Gateway)
├── tests/
│   └── test_validate_record.py
├── livrable/
│   ├── 01_analyse_existant.md
│   ├── 02_plan_gouvernance.md
│   ├── 03_dictionnaire_donnees.md
│   └── 04_justification_technique.md
├── data/
│   ├── raw/               # Données brutes JSON (ignoré par git)
│   ├── clean/             # Données Parquet validées (ignoré par git)
│   ├── archive/           # Bruts archivés (ignoré par git)
│   └── errors/            # DLQ — enregistrements rejetés (ignoré par git)
├── db/                    # Base SQLite (ignoré par git)
└── logs/                  # Journaux (ignoré par git)
```

---

## Règles de gouvernance implémentées

| Axe | Mécanisme | Fichier |
|---|---|---|
| **Qualité** | 4 règles de validation + quality_score + DLQ | `process.py` |
| **Qualité** | Dédoublonnage (drop_duplicates + UNIQUE SQL) | `process.py`, `load_db.py` |
| **Sécurité** | RBAC 3 niveaux (public/viewer/admin) | `local_api.py` |
| **Sécurité** | CORS restreint, validation des dates (anti-injection) | `local_api.py` |
| **Sécurité** | Secrets via variables d'environnement | `config.py` |
| **Traçabilité** | Colonnes tech_version, tech_updated_at, source_file | `process.py` |
| **Traçabilité** | Table pipeline_audit (data lineage) | `load_db.py` |
| **Traçabilité** | Archivage des fichiers bruts | `process.py` |
| **Documentation** | config.py centralisé + dictionnaire de données | `config.py`, `livrable/` |

---

## Lancer les tests

```bash
python -m pytest tests/ -v
```
