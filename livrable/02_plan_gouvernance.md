# 2. Plan de gouvernance des données — CityFlow

## 2.1 Objectifs du plan

Ce plan définit les règles de gouvernance appliquées au pipeline CityFlow afin
de garantir :
- la **qualité** des données traitées
- la **sécurité** des accès
- la **traçabilité** des traitements
- la **documentation** du système

---

## 2.2 Architecture cible — Cycle de vie de la donnée

```
[API Paris Open Data]
        |
        | JSON paginé (100 rec/page, max 10 000/jour)
        v
[ZONE 1 — data/raw/]          ← Données brutes, non modifiées
  comptages_veille_YYYYMMDD.json
        |
        | Validation + Nettoyage (process.py)
        |  - Règles de qualité appliquées
        |  - Enregistrements invalides → DLQ
        v
[ZONE 2 — data/clean/]        ← Données validées, format Parquet
  comptages_veille_YYYYMMDD.parquet
  (colonnes : arc_id, t_1h, q, k, etat_barre,
              tech_version, tech_updated_at, source_file, quality_score)
        |
        | Chargement idempotent (load_db.py)
        |  - INSERT OR IGNORE sur UNIQUE(arc_id, t_1h)
        |  - Enregistrement dans pipeline_audit
        v
[ZONE 3 — db/cityflow.db]     ← Base SQLite
  table: raw           (mesures horaires validées)
  table: aggregated    (métriques journalières par arc)
  table: pipeline_audit (historique des chargements)
        |
        | Agrégation journalière (aggregate.py)
        v
[ZONE 4 — API REST]           ← Exposition contrôlée (local_api.py)
  Endpoints : /aggregated, /aggregated/stats, /aggregated/dates
  Sécurité  : clés API + CORS restreint + validation des paramètres
        |
        v
[Tableau de bord Streamlit]   ← Visualisation (app_local.py)

[ZONE DLQ — data/errors/]     ← Enregistrements rejetés (traçabilité)
  errors_comptages_veille_YYYYMMDD_HHMMSS.csv

[ZONE ARCHIVE — data/archive/] ← Bruts archivés après traitement
  comptages_veille_YYYYMMDD.json (copie conservée)

[LOGS — logs/]                ← Journaux d'opération
  pipeline.log, process.log, load_db.log, aggregate.log
```

---

## 2.3 Règles de sécurité

### Gestion des accès (simulation IAM)

| Rôle | Endpoints autorisés | Implémentation |
|---|---|---|
| `public` | `/health`, `/aggregated`, `/aggregated/dates` | Pas de clé requise |
| `viewer` | + `/aggregated/stats` | Header `X-API-Key` ou param `api_key` |
| `admin` | + `/pipeline/audit` | Header `X-API-Key` ou param `api_key` |

Le contrôle est effectif en code : chaque endpoint vérifie le niveau de rôle
via `require_role("viewer")` ou `require_role("admin")`. Un accès insuffisant
retourne HTTP 403 avec description du rôle requis.

**Principe appliqué :** les clés API ne sont jamais écrites en dur dans le code
de production. Des valeurs de démonstration par défaut existent dans `config.py`
uniquement pour faciliter les tests locaux. En environnement réel, la variable
d'environnement `CITYFLOW_API_KEYS` doit toujours être définie pour écraser ces
valeurs par défaut.

### CORS (Cross-Origin Resource Sharing)

L'API n'accepte que les requêtes provenant des origines déclarées dans
`CITYFLOW_CORS_ORIGINS` (par défaut : `http://localhost:8501`). La configuration
`allow_origins=["*"]` du projet initial a été supprimée.

### Validation des paramètres d'entrée

Tout paramètre `date` passé à l'API est validé :
1. Regex `^\d{4}-\d{2}-\d{2}$` — format YYYY-MM-DD obligatoire
2. `datetime.strptime` — date calendairement valide (pas de "2026-02-30")
3. En cas d'échec : HTTP 400 (jamais de propagation vers SQLite)

---

## 2.4 Règles de qualité des données

### Règles de validation (`process.py`)

| # | Règle | Champ | Condition | Action si échec |
|---|---|---|---|---|
| 1 | Identifiant obligatoire | `arc_id` | Non nul, non vide | Rejet → DLQ |
| 2 | Horodatage obligatoire + format | `t_1h` | Non nul + regex ISO datetime | Rejet → DLQ |
| 3 | Débit dans bornes | `q` | 0 ≤ q ≤ 10 000 veh/h | Rejet → DLQ |
| 4 | Taux d'occupation dans bornes | `k` | 0 ≤ k ≤ 100 % | Rejet → DLQ |

### Score de qualité (`quality_score`)

Chaque enregistrement valide reçoit un score de 0.0 à 1.0 calculé comme :

```
quality_score = nb_champs_valides_presents / 4
```

- `1.00` : les 4 champs sont présents et valides
- `0.75` : 3 champs valides (ex : `k` absent)
- `0.50` : 2 champs valides

Le score moyen du lot est journalisé à chaque exécution.

### Dédoublonnage

- Au niveau fichier : `drop_duplicates(subset=["arc_id", "t_1h"])` dans `process.py`
- Au niveau base : contrainte `UNIQUE(arc_id, t_1h)` en SQLite + `INSERT OR IGNORE`

### Dead Letter Queue (DLQ)

Tout enregistrement rejeté est sauvegardé dans `data/errors/` avec la colonne
`reject_reason` indiquant la règle violée. Ce mécanisme permet de retraiter les
données après correction de la source.

---

## 2.5 Règles de traçabilité (Data Lineage)

### Colonnes techniques sur chaque enregistrement brut

| Colonne | Valeur | But |
|---|---|---|
| `tech_version` | `"1.0.0"` | Version du pipeline ayant produit la donnée |
| `tech_updated_at` | Timestamp UTC ISO | Date/heure du traitement |
| `source_file` | Nom du fichier JSON d'origine | Lien vers la donnée brute |
| `quality_score` | 0.0 – 1.0 | Fiabilité de l'enregistrement |
| `loaded_at` | Timestamp SQLite UTC | Date d'insertion en base |

### Table d'audit `pipeline_audit`

À chaque exécution de `load_db.py`, une ligne est insérée :

| Colonne | Description |
|---|---|
| `run_at` | Horodatage UTC de l'exécution |
| `source_file` | Fichier Parquet chargé |
| `rows_read` | Nombre de lignes lues dans le Parquet |
| `rows_inserted` | Nombre de lignes réellement insérées (nouvelles) |
| `rows_skipped` | Nombre de doublons ignorés |
| `status` | `ok` / `partial` |

### Archivage des données brutes

Après traitement, chaque fichier JSON brut est copié dans `data/archive/`.
L'original peut être réutilisé pour rejouer le pipeline en cas d'erreur.

### Journaux d'opération

Chaque script produit un fichier log horodaté dans `logs/` :

| Fichier | Script source |
|---|---|
| `pipeline.log` | Orchestrateur global |
| `process.log` | Validation + transformation |
| `load_db.log` | Chargement SQLite |
| `aggregate.log` | Calcul des agrégats |

---

## 2.6 Classification des données

| Type | Données CityFlow | Sensibilité |
|---|---|---|
| Structurées | Mesures horaires de comptage (JSON → Parquet → SQLite) | Non sensibles |
| Structurées | Métriques agrégées journalières | Non sensibles |
| Structurées | Logs d'opération | Internes |
| Référentielles | Identifiants d'arcs routiers parisiens (`arc_id`) | Non sensibles |

Les données CityFlow ne contiennent **aucune donnée personnelle**. Il n'y a pas
de RGPD à appliquer. Les données sont des mesures anonymes de flux de véhicules
sur des axes routiers.

---

## 2.7 Seuils de gouvernance (centralisés dans config.py)

Tous les paramètres métier sont centralisés dans `config.py` et peuvent être
surchargés par variables d'environnement sans modifier le code :

| Paramètre | Valeur par défaut | Variable d'environnement |
|---|---|---|
| Débit max toléré | 10 000 veh/h | `CITYFLOW_MAX_DEBIT` |
| Taux occupation max | 100 % | `CITYFLOW_MAX_K` |
| Seuil congestion vitesse | 10 | `CITYFLOW_SEUIL_CONG_V` |
| Seuil ralenti vitesse | 25 | `CITYFLOW_SEUIL_RAL_V` |
| Seuil congestion heures | 3 h | `CITYFLOW_SEUIL_CONG_H` |
| Seuil confiance HIGH | 20 mesures | `CITYFLOW_MIN_MESURES_HIGH` |
| Seuil confiance MEDIUM | 5 mesures | `CITYFLOW_MIN_MESURES_MEDIUM` |
