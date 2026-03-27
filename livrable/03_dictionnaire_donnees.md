# 3. Dictionnaire de données — CityFlow

## 3.1 Source de données origine

**API Paris Open Data**
URL : `https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/comptages-routiers-permanents/records`
Format : JSON (pagination 100 enregistrements/page)
Licence : Licence ouverte / Open Data - Mairie de Paris

---

## 3.2 Table `raw` — Mesures horaires validées

**Description :** Contient une ligne par mesure horaire et par arc routier, telle
que reçue de l'API Paris Open Data, après validation par `process.py`.

**Contrainte d'unicité :** `UNIQUE(arc_id, t_1h)` — une seule mesure par arc et par heure.

| Colonne | Type SQLite | Obligatoire | Source | Plage valide | Description |
|---|---|---|---|---|---|
| `id` | INTEGER (PK) | Auto | SQLite | — | Identifiant technique auto-incrémenté |
| `arc_id` | TEXT | Oui | API (`iu_ac`) | Non vide | Identifiant unique de l'arc routier (ex : `"75056_e0001"`) |
| `libelle` | TEXT | Non | API (`libelle`) | — | Nom complet de l'arc ou de la voie (ex : `"Rue de Rivoli"`) |
| `t_1h` | TEXT | Oui | API (`t_1h`) | Format ISO datetime | Horodatage de la mesure, heure de début (ex : `"2026-03-26T07:00:00"`) |
| `q` | REAL | Oui | API (`q`) | [0 ; 10 000] | Débit de véhicules, en veh/h |
| `k` | REAL | Oui | API (`k`) | [0 ; 100] | Taux d'occupation de la voie, en % |
| `etat_barre` | TEXT | Non | API (`etat_barre`) | — | État de la barre de comptage (`"1"` = actif, `"0"` = inactif/défaillance) |
| `tech_version` | TEXT | Oui | pipeline | `"1.0.0"` | Version du pipeline ayant produit cette ligne |
| `tech_updated_at` | TEXT | Oui | pipeline | ISO UTC | Horodatage UTC du traitement (ex : `"2026-03-27T00:12:34.000Z"`) |
| `source_file` | TEXT | Oui | pipeline | — | Nom du fichier JSON source (ex : `"comptages_veille_20260326.json"`) |
| `quality_score` | REAL | Oui | pipeline | [0.0 ; 1.0] | Score de qualité : fraction des 4 champs obligatoires valides |
| `loaded_at` | TEXT | Auto | SQLite | ISO UTC | Horodatage d'insertion en base (valeur par défaut : `CURRENT_TIMESTAMP`) |

**Notes métier :**
- `q = 0` peut indiquer une voie fermée ou une absence de trafic réelle.
- `k = 100` indique une voie saturée (arrêt total).
- `etat_barre = "0"` signale une panne du capteur ; les données sont présentes mais potentiellement erronées.

---

## 3.3 Table `aggregated` — Métriques journalières par arc

**Description :** Contient une ligne par combinaison (arc routier × jour), calculée
par `aggregate.py` à partir des mesures de la table `raw`.

**Contrainte d'unicité :** `PRIMARY KEY(arc_id, date)`.

| Colonne | Type SQLite | Obligatoire | Source | Plage valide | Description |
|---|---|---|---|---|---|
| `arc_id` | TEXT (PK) | Oui | table `raw` | Non vide | Identifiant de l'arc routier |
| `libelle` | TEXT | Non | table `raw` | — | Nom de l'arc (dernier libellé rencontré pour cet arc) |
| `date` | TEXT (PK) | Oui | calculé | Format YYYY-MM-DD | Journée de la mesure (ex : `"2026-03-26"`) |
| `debit_moyen_horaire` | REAL | Oui | calculé | ≥ 0 | Moyenne des débits `q` sur la journée, en veh/h |
| `total_vehicules` | REAL | Oui | calculé | ≥ 0 | Somme des débits `q` de la journée (proxy du volume total) |
| `nb_mesures` | INTEGER | Oui | calculé | [1 ; 24] | Nombre de mesures horaires disponibles pour la journée |
| `vitesse_moyenne` | REAL | Oui | calculé | [0 ; 100] | Proxy de vitesse = `clip(100 − k_moyen, 0, 100)` — 0 = arrêt, 100 = libre |
| `vitesse_confidence` | TEXT | Oui | calculé | HIGH / MEDIUM / LOW | Fiabilité de `vitesse_moyenne` selon le nombre de mesures |
| `heures_congestion` | INTEGER | Oui | calculé | [0 ; 24] | Nombre d'heures où `k > 90` (équivalent vitesse proxy < 10, seuil congestion) |
| `heure_pic` | INTEGER | Non | calculé | [0 ; 23] | Heure de la journée avec le débit horaire maximum |
| `etat_trafic` | TEXT | Oui | calculé | fluide / ralenti / congestionné / inconnu | Qualification de l'état dominant sur la journée |
| `aggregated_at` | TEXT | Auto | SQLite | ISO UTC | Horodatage du calcul d'agrégation |

**Notes métier sur `vitesse_moyenne` :**
Paris Open Data ne fournit pas de mesure de vitesse directe. La formule
`100 − k` est une approximation : quand le taux d'occupation est proche de 0 %,
les véhicules circulent librement (vitesse proxy élevée) ; quand il est proche de
100 %, il y a saturation (vitesse proxy nulle). Ce proxy est borné entre 0 et 100
par la fonction `clip`.

**Règles de classification de `vitesse_confidence` :**

| Valeur | Condition | Signification |
|---|---|---|
| `HIGH` | `nb_mesures ≥ 20` | Journée quasi-complète (moins de 4 h manquantes) |
| `MEDIUM` | `5 ≤ nb_mesures < 20` | Demi-journée disponible |
| `LOW` | `nb_mesures < 5` | Trop peu de mesures — résultat peu fiable |

**Règles de classification de `etat_trafic` :**

| Valeur | Conditions |
|---|---|
| `congestionné` | `vitesse_moyenne < 10` OU `heures_congestion ≥ 3` |
| `ralenti` | `10 ≤ vitesse_moyenne < 25` OU `heures_congestion ≥ 1` |
| `fluide` | `vitesse_moyenne ≥ 25` ET `heures_congestion = 0` |
| `inconnu` | Données insuffisantes (`vitesse_confidence = LOW`) |

---

## 3.4 Table `pipeline_audit` — Journal des chargements

**Description :** Enregistre chaque exécution de `load_db.py` pour assurer la
traçabilité complète des opérations de chargement.

| Colonne | Type SQLite | Description |
|---|---|---|
| `id` | INTEGER (PK) | Identifiant auto-incrémenté |
| `run_at` | TEXT | Horodatage UTC de l'exécution |
| `source_file` | TEXT | Chemin du fichier Parquet chargé |
| `rows_read` | INTEGER | Nombre total de lignes lues dans le Parquet |
| `rows_inserted` | INTEGER | Nombre de lignes effectivement insérées (nouvelles) |
| `rows_skipped` | INTEGER | Nombre de lignes ignorées (doublons — `INSERT OR IGNORE`) |
| `status` | TEXT | `ok` si toutes les lignes sont insérées ou ignorées proprement ; `partial` si erreur partielle |

---

## 3.5 Fichiers du pipeline

### Données brutes — `data/raw/`

| Attribut | Valeur |
|---|---|
| Format | JSON |
| Nommage | `comptages_veille_YYYYMMDD.json` |
| Contenu | Réponse brute de l'API Paris Open Data, non modifiée |
| Cycle de vie | Archivé dans `data/archive/` après traitement |

### Données propres — `data/clean/`

| Attribut | Valeur |
|---|---|
| Format | Apache Parquet (colonnaire, compressé) |
| Nommage | `comptages_veille_YYYYMMDD.parquet` |
| Contenu | Enregistrements validés, avec colonnes techniques ajoutées |
| Colonnes | `arc_id, libelle, t_1h, q, k, etat_barre, tech_version, tech_updated_at, source_file, quality_score` |

### Rejets — `data/errors/` (Dead Letter Queue)

| Attribut | Valeur |
|---|---|
| Format | CSV |
| Nommage | `errors_comptages_veille_YYYYMMDD_HHMMSS.csv` |
| Contenu | Enregistrements refusés par `validate_record()` |
| Colonnes supplémentaires | `reject_reason` (description de la règle violée) |

---

## 3.6 Paramètres de configuration (`config.py`)

| Nom | Type | Valeur par défaut | Rôle |
|---|---|---|---|
| `MAX_DEBIT` | int | 10 000 | Seuil maximum du débit `q` accepté |
| `MAX_K` | int | 100 | Seuil maximum de `k` accepté |
| `SEUIL_CONGESTION_VITESSE` | int | 10 | Vitesse proxy en dessous de laquelle une heure est classée "congestionnée" |
| `SEUIL_RALENTI_VITESSE` | int | 25 | Vitesse proxy en dessous de laquelle une heure est classée "ralentie" |
| `SEUIL_CONGESTION_HEURES` | int | 3 | Nombre d'heures de congestion pour classer la journée "congestionnée" |
| `SEUIL_RALENTI_HEURES` | int | 1 | Nombre d'heures de congestion pour classer la journée "ralentie" |
| `MIN_MESURES_HIGH` | int | 20 | Seuil minimal de mesures pour confiance HIGH |
| `MIN_MESURES_MEDIUM` | int | 5 | Seuil minimal de mesures pour confiance MEDIUM |
| `TECH_VERSION` | str | "1.0.0" | Version du pipeline (inscrite dans chaque enregistrement) |
