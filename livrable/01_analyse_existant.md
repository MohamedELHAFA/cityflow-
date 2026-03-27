# 1. Analyse de l'existant — Projet CityFlow

## 1.1 Présentation du projet initial

Le projet CityFlow est un pipeline de traitement de données de trafic routier
parisien. Il ingère les données de comptage en temps réel depuis l'API Open Data
de la Ville de Paris (dataset `comptages-routiers-permanents`), les transforme
et les expose via un tableau de bord interactif.

**Architecture initiale (AWS Cloud) :**

| Composant AWS | Rôle |
|---|---|
| EventBridge Cron | Déclenchement planifié du pipeline |
| Lambda "Retrieve Data" | Téléchargement depuis l'API Paris Open Data |
| S3 Raw Bucket | Stockage des données brutes (JSON) |
| Lambda "Process & Validation" | Transformation et validation des données |
| S3 Clean (Parquet) | Stockage des données nettoyées |
| Lambda "S3 → DynamoDB" | Chargement en base NoSQL |
| DynamoDB Table Raw | Base de données des mesures brutes |
| EC2 "Aggregate" | Calcul des métriques journalières |
| DynamoDB Table Aggregated | Base de données des agrégats |
| API Gateway | Exposition des données via REST |
| EC2 Streamlit | Tableau de bord de visualisation |

**Source de données :**
- URL : `https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/comptages-routiers-permanents/records`
- Fréquence : données horaires (~68 000 mesures/jour pour Paris)
- Limite API : 10 000 enregistrements max par requête (limite imposée par le fournisseur)

---

## 1.2 Limites identifiées dans le projet initial

### A. Qualité des données — FAIBLE

| Problème | Description |
|---|---|
| Pas de validation | Les données de l'API étaient insérées directement sans vérifier les champs obligatoires |
| Pas de détection des outliers | Un débit de 999 999 veh/h pouvait être stocké sans alerte |
| Pas de gestion des doublons | Une même mesure (même arc, même horodatage) pouvait être insérée plusieurs fois |
| Format non normalisé | Le champ `iu_ac` (identifiant arc) n'était pas renommé de façon cohérente |
| Pas de score de qualité | Impossible de filtrer les données par niveau de fiabilité |

### B. Sécurité — FAIBLE

| Problème | Description |
|---|---|
| Clés API hardcodées | Les secrets d'accès à l'API étaient écrits directement dans le code source |
| CORS ouvert (`["*"]`) | N'importe quel domaine pouvait appeler l'API REST sans restriction |
| Pas de contrôle d'accès | Aucune distinction entre utilisateur public et administrateur |
| Pas de validation des entrées | Le paramètre `date` de l'API n'était pas validé → risque d'injection SQL |
| Logs sans détection d'anomalie | Les tentatives d'accès invalides n'étaient pas tracées |

### C. Traçabilité — INEXISTANTE

| Problème | Description |
|---|---|
| Pas de linéage des données | Impossible de savoir quel fichier source a produit quelle ligne en base |
| Pas de table d'audit | Aucun historique des opérations de chargement |
| Pas de colonnes techniques | Pas de `tech_version`, `tech_updated_at`, `source_file` sur les enregistrements |
| Pas d'archivage des bruts | Les fichiers JSON bruts étaient supprimés après traitement |
| Pas de DLQ | Les enregistrements invalides étaient silencieusement ignorés |

### D. Documentation — INEXISTANTE

| Problème | Description |
|---|---|
| Pas de dictionnaire de données | Aucune définition des champs (`q`, `k`, `arc_id`, etc.) |
| Pas de description des pipelines | Aucun document expliquant les étapes de transformation |
| Paramètres non centralisés | Les seuils métier (congestion, qualité) étaient dispersés dans le code |
| Pas de versionning | Impossible de savoir quelle version du pipeline a produit une donnée |

---

## 1.3 Conclusion de l'analyse

Le projet initial remplissait sa fonction de visualisation mais ne satisfaisait
aucun critère de gouvernance des données. Les quatre axes — qualité, sécurité,
traçabilité et documentation — étaient tous à construire. C'est l'objet du plan
de gouvernance décrit dans la section suivante.
