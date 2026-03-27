# 4. Justification des choix techniques — CityFlow

## 4.1 Principe général

Le projet CityFlow a été développé en local pour **démontrer les principes de
gouvernance de données AWS** sans disposer d'une infrastructure Cloud.
Chaque choix local reproduit une bonne pratique AWS documentée. Ce document
justifie les choix les plus importants.

---

## 4.2 Tableau de correspondance AWS → Local

| Service AWS | Équivalent local | Justification |
|---|---|---|
| EventBridge (scheduler) | `pipeline.py` avec arguments `argv` | Déclenchement paramétrable par date, même concept de scheduling |
| Lambda Retrieve | `fetch_data.py` | Fonction isolée, sans état, déclenchée à la demande |
| S3 (zone Raw) | `data/raw/` | Séparation des zones de stockage selon le niveau de traitement |
| S3 Glacier (archive) | `data/archive/` | Conservation des bruts après traitement, politique de rétention |
| Lambda Process | `process.py` | Validation + transformation dans une fonction isolée |
| SQS Dead Letter Queue | `data/errors/` | Capture des enregistrements rejetés pour retraitement |
| S3 Clean (Parquet) | `data/clean/` | Format optimisé pour l'analytique (voir §4.3) |
| Lambda S3→DynamoDB | `load_db.py` | Chargement idempotent avec audit |
| DynamoDB | SQLite `cityflow.db` | Base structurée, contraintes, requêtes SQL (voir §4.4) |
| EC2 (agrégation) | `aggregate.py` | Calcul journalier des métriques métier |
| API Gateway | FastAPI `local_api.py` | Exposition REST sécurisée (voir §4.5) |
| Cognito / IAM | Clés API + rôles dans `config.py` | Gestion des accès sans infrastructure Identity (voir §4.6) |
| CloudWatch Logs | `logs/` + Python logging | Journaux horodatés, niveaux INFO/ERROR/WARNING |

---

## 4.3 Choix du format Parquet pour la zone Clean

**Problème :** Les données brutes sont des fichiers JSON. JSON est verbeux, non
compressé, et ne supporte pas le partitionnement colonnaire.

**Choix :** Apache Parquet dans `data/clean/`.

**Justifications :**
1. **Efficacité** : Parquet est colonnaire. Pour lire uniquement les colonnes `q` et
   `k`, seules ces deux colonnes sont décompressées. Sur 2 978 enregistrements, le
   gain est faible, mais sur des millions de mesures (usage réel), c'est critique.
2. **Standard industriel** : AWS Athena, Redshift Spectrum, Glue, Spark, et
   Pandas lisent tous Parquet nativement. Ce choix assure la portabilité.
3. **Schéma embarqué** : Parquet encode les types (int, float, string). Plus de
   risque de confusion entre un `q` entier et un `q` flottant selon le jour.
4. **Compression** : La taille des fichiers est réduite d'environ 70 % par rapport
   au JSON équivalent (compression Snappy).

---

## 4.4 Choix de SQLite pour le stockage structuré

**Problème :** DynamoDB (NoSQL) est la base AWS d'origine. Elle n'est pas
utilisable localement sans émulateur payant.

**Choix :** SQLite dans `db/cityflow.db`.

**Justifications :**
1. **Contraintes d'intégrité** : SQLite supporte `UNIQUE`, `PRIMARY KEY`,
   `FOREIGN KEY`, `NOT NULL`. Ces contraintes reproduisent les mécanismes de
   gouvernance habituellement portés par le schéma de base.
2. **Idempotence** : `INSERT OR IGNORE` sur la contrainte `UNIQUE(arc_id, t_1h)`
   garantit qu'un rechargement du même fichier **n'insère pas de doublons**.
   C'est le même principe qu'une DynamoDB avec une condition PutItem.
3. **Requêtabilité** : SQL permet des agrégations complexes (`AVG`, `SUM`,
   `GROUP BY`, `CLIP`) que DynamoDB ne peut pas faire nativement.
4. **Zéro infrastructure** : Pas de serveur, pas de port, pas de Docker. La base
   est un fichier local, versionnable, sauvegardable.

---

## 4.5 Choix de FastAPI pour l'exposition REST

**Choix :** FastAPI (`api/local_api.py`) sur le port 8000.

**Justifications :**
1. **Validation automatique** : FastAPI valide les types des paramètres de
   requête. Si `date` n'est pas une chaîne, l'erreur est interceptée avant
   d'atteindre la base de données.
2. **Documentation automatique** : FastAPI génère `/docs` (Swagger UI) et
   `/redoc` automatiquement. Toute l'API est auto-documentée.
3. **CORS configurable** : `CORSMiddleware` permet de restreindre les origines
   autorisées, évitant les attaques cross-origin.
4. **Légèreté** : Équivalent local d'API Gateway sans configuration YAML.

---

## 4.6 Gestion des accès par clés API

**Problème :** AWS utilise IAM (roles, policies, Cognito). Localement, IAM n'est
pas disponible.

**Choix :** Clés API chargées depuis une variable d'environnement (`CITYFLOW_API_KEYS`).

**Justifications :**
1. **Pas de secrets dans le code** : Les clés ne sont jamais écrites dans les
   fichiers `.py` ou `.json`. Elles sont injectées à l'exécution. Ce principe est
   identique à l'usage de AWS Secrets Manager.
2. **Séparation des rôles** : Le rôle `public` ne peut que lire les agrégats.
   Le rôle `viewer` accède aux statistiques. Le rôle `admin` a accès complet.
   Ce modèle RBAC reproduit les politiques IAM.
3. **Réponse défensive** : Une clé invalide reçoit HTTP 403. Une absence de clé
   reçoit un accès en lecture limitée. Jamais de clé exposée dans les messages
   d'erreur.

---

## 4.7 Colonne `quality_score` — Quantification de la qualité

**Problème :** Sans indicateur de qualité, il est impossible de savoir si une
donnée est fiable ou si elle a passé les vérifications avec des champs manquants.

**Choix :** Colonne `quality_score` (REAL, [0.0–1.0]) sur chaque enregistrement.

**Justifications :**
1. **Traçabilité de la qualité** : Le score indique immédiatement combien de
   règles ont été satisfaites. `1.0` = données complètes, `0.5` = deux champs
   manquants.
2. **Filtrabilité** : Il est possible d'exclure les enregistrements avec
   `quality_score < 0.75` d'une analyse critique.
3. **Métriques de pipeline** : Le score moyen journalier (`AVG(quality_score)`)
   est journalisé. Si des capteurs commencent à transmettre des données
   incomplètes, cela se voit immédiatement.

---

## 4.8 Colonne `vitesse_confidence` — Incertitude sur les agrégats

**Problème :** Un agrégat journalier calculé sur 2 mesures est beaucoup moins
fiable qu'un agrégat calculé sur 22 mesures. Sans indicateur, les deux semblent
équivalents dans la table.

**Choix :** Colonne `vitesse_confidence` (TEXT : HIGH / MEDIUM / LOW).

**Justifications :**
1. **Quantification de l'incertitude** : `HIGH` (≥ 20 mesures sur 24) signifie
   que la journée est quasi-complète. `LOW` (< 5 mesures) signifie que l'agrégat
   n'est pas fiable.
2. **Gouvernance** : Les tableaux de bord peuvent masquer ou alerter sur les arcs
   avec `vitesse_confidence = LOW`.
3. **Transparence** : Sans cet indicateur, un `vitesse_moyenne = 85` calculé sur
   1 seule mesure serait présenté à égalité avec un `vitesse_moyenne = 67`
   calculé sur 23 mesures.

---

## 4.9 Table `pipeline_audit` — Traçabilité des opérations

**Problème :** Sans journal de chargement, il est impossible de savoir si un
fichier a été chargé, quand, et combien de doublons ont été ignorés.

**Choix :** Table `pipeline_audit` dans SQLite.

**Justifications :**
1. **Data lineage** : Chaque enregistrement de la table `raw` peut être
   retracé jusqu'à son fichier source via `source_file`, et jusqu'à son
   chargement via `pipeline_audit`.
2. **Détection d'anomalies** : Un chargement avec `rows_inserted = 0` et
   `rows_skipped = 2978` indique un double-chargement. C'est visible
   immédiatement dans `pipeline_audit`.
3. **Reproductibilité** : En cas de bug, on sait exactement quels fichiers ont
   été chargés et dans quel ordre.

---

## 4.10 Exemples de démonstration

### Exemple 1 — Démonstration des règles de validation (`process.py`)

Le pipeline lit un fichier JSON brut et applique 4 règles de qualité.

**Scénario de test :** On injecte manuellement des enregistrements invalides dans
le fichier JSON pour observer leur rejet dans le DLQ.

```python
# Enregistrement invalide 1 : arc_id vide
{"iu_ac": "", "libelle": "Test", "t_1h": "2026-03-26T10:00:00", "q": 500, "k": 30}
# → rejeté : "arc_id vide"

# Enregistrement invalide 2 : débit hors borne
{"iu_ac": "75056_e0001", "libelle": "Test", "t_1h": "2026-03-26T10:00:00", "q": 99999, "k": 30}
# → rejeté : "q hors borne [0, 10000]"

# Enregistrement valide
{"iu_ac": "75056_e0001", "libelle": "Test", "t_1h": "2026-03-26T10:00:00", "q": 500, "k": 30}
# → accepté, quality_score = 1.0
```

**Résultat démontré :**
- Le fichier `data/errors/errors_test_XXXXXX.csv` contient les 2 rejets avec `reject_reason`
- Le fichier `data/clean/test.parquet` contient uniquement l'enregistrement valide
- Le log `process.log` indique : `3 lus, 1 valide, 2 rejetés, qualite moyenne : 1.00`
  _(le score moyen est calculé sur les enregistrements **valides** uniquement, donc 1.0 pour le seul enregistrement accepté)_

---

### Exemple 2 — Démonstration de l'idempotence et de l'audit (`load_db.py`)

**Problème démontré :** En production, un pipeline peut être rejoué (erreur réseau,
maintenance). Sans idempotence, cela génère des doublons.

**Scénario :** On charge deux fois le même fichier Parquet.

**Premier chargement :**
```
[load_db] Fichier : comptages_veille_20260326.parquet
[load_db] Lignes lues : 2978
[load_db] Lignes insérées : 2978
[load_db] Doublons ignorés : 0
[load_db] Statut : ok
```

**Second chargement (même fichier) :**
```
[load_db] Fichier : comptages_veille_20260326.parquet
[load_db] Lignes lues : 2978
[load_db] Lignes insérées : 0
[load_db] Doublons ignorés : 2978
[load_db] Statut : ok
```

**Vérification dans `pipeline_audit` :**
```sql
SELECT run_at, rows_inserted, rows_skipped, status
FROM pipeline_audit
WHERE source_file LIKE '%20260326%';
```
```
run_at                    | rows_inserted | rows_skipped | status
2026-03-27T00:12:34.000Z  |          2978 |            0 | ok
2026-03-27T00:15:10.000Z  |             0 |         2978 | ok
```

**Résultat démontré :**
- La base de données reste propre (0 doublon)
- L'audit montre les deux exécutions
- Le statut `ok` confirme que le comportement est celui attendu, pas une erreur
