"""
app_local.py — Dashboard Streamlit (version locale)
Identique à app_api.py mais connecté à l'API locale FastAPI (localhost:8000)
au lieu de l'API Gateway AWS.

Lancement :
  streamlit run app_local.py
  (l'API doit tourner sur http://localhost:8000)
"""

import json
import sys
from datetime import date as ddate
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st
from pyproj import Transformer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import API_HOST, API_PORT

# ──────────────────────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────────────────────
_API_BASE  = f"http://{API_HOST}:{API_PORT}"
API_AGG    = f"{_API_BASE}/aggregated"
API_STATS  = f"{_API_BASE}/aggregated/stats"
API_DATES  = f"{_API_BASE}/aggregated/dates"
DEFAULT_LIMIT = 200

REF_GEOJSON = (
    "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"
    "/referentiel-comptages-routiers/exports/geojson"
)

st.set_page_config(page_title="CityFlow – Local", layout="wide")
st.title("🚦 CityFlow – Dashboard trafic local 🗺️")

# ──────────────────────────────────────────────────────────────
# HELPERS API
# ──────────────────────────────────────────────────────────────

def _rows_from_json(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"]
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("body"), str):
        try:
            return json.loads(data["body"])
        except Exception:
            return []
    return []


def _try_fetch(url: str, params: dict, timeout: int = 20) -> Tuple[Optional[dict], Optional[str]]:
    try:
        r = requests.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return (data if isinstance(data, dict) else {"items": data}), None
    except Exception as exc:
        return None, str(exc)


@st.cache_data(ttl=60)
def fetch_all_paginated(url: str, params: dict) -> Tuple[pd.DataFrame, Optional[str]]:
    all_items: List[Dict[str, Any]] = []
    p = dict(params)

    while True:
        data, err = _try_fetch(url, p)
        if err:
            return pd.DataFrame(), err

        all_items.extend(_rows_from_json(data))

        next_token = data.get("nextToken") if isinstance(data, dict) else None
        if not next_token:
            break
        p["nextToken"] = next_token

    df = pd.DataFrame(all_items)
    if not df.empty and "arc_id" in df.columns:
        df["arc_id"] = df["arc_id"].astype(str)
    return df, None


def fetch_aggregated(day_str: str, limit: int) -> Tuple[pd.DataFrame, str]:
    df, err = fetch_all_paginated(API_AGG, {"date": day_str, "limit": limit})
    return df, (err or "")


@st.cache_data(ttl=3600)
def fetch_available_dates() -> List[str]:
    data, err = _try_fetch(API_DATES, {})
    if err or data is None:
        return []
    return data.get("dates", [])


# ──────────────────────────────────────────────────────────────
# HELPERS GÉO
# ──────────────────────────────────────────────────────────────

def _flatten_coords(coords) -> List[List[List[float]]]:
    if not coords:
        return []
    if (
        isinstance(coords, list)
        and len(coords) > 0
        and isinstance(coords[0], list)
        and len(coords[0]) == 2
        and isinstance(coords[0][0], (int, float))
    ):
        return [coords]
    parts = []
    for part in (coords if isinstance(coords, list) else []):
        if (
            isinstance(part, list)
            and len(part) > 0
            and isinstance(part[0], list)
            and len(part[0]) == 2
            and isinstance(part[0][0], (int, float))
        ):
            parts.append(part)
    return parts


@st.cache_data(ttl=24 * 3600)
def load_arc_paths_wgs84() -> pd.DataFrame:
    transformer = Transformer.from_crs(2154, 4326, always_xy=True)
    r = requests.get(REF_GEOJSON, timeout=60)
    r.raise_for_status()
    geo = r.json()

    rows = []
    for feat in geo.get("features", []):
        props = feat.get("properties", {}) or {}
        geom  = feat.get("geometry", {}) or {}
        arc   = props.get("iu_ac")
        coords = geom.get("coordinates")
        if arc is None or coords is None:
            continue

        parts = _flatten_coords(coords)
        if not parts:
            continue

        x0, y0 = parts[0][0]
        do_transform = abs(float(x0)) > 1000 or abs(float(y0)) > 1000

        paths = []
        for part in parts:
            path = []
            for x, y in part:
                x, y = float(x), float(y)
                if do_transform:
                    x, y = transformer.transform(x, y)
                path.append([x, y])
            if len(path) >= 2:
                paths.append(path)

        if paths:
            rows.append({"arc_id": str(arc), "paths": paths})

    return pd.DataFrame(rows).drop_duplicates("arc_id")


# ──────────────────────────────────────────────────────────────
# COULEURS / LÉGENDE
# ──────────────────────────────────────────────────────────────
RED    = [220, 0, 0]
ORANGE = [255, 165, 0]
GREEN  = [0, 200, 0]
GREY   = [140, 140, 140]


def classify_color(value: float, mode: str, red_thr: float, orange_thr: float) -> list:
    if pd.isna(value):
        return GREY
    if mode == "vitesse_moyenne":
        return RED if value <= red_thr else (ORANGE if value <= orange_thr else GREEN)
    return RED if value >= red_thr else (ORANGE if value >= orange_thr else GREEN)


def add_traffic_class(df: pd.DataFrame, mode: str, red_thr: float, orange_thr: float) -> pd.DataFrame:
    out = df.copy()
    out["traffic_color"] = out[mode].apply(lambda v: classify_color(v, mode, red_thr, orange_thr))
    label_map = {str(RED): "Rouge", str(ORANGE): "Orange", str(GREEN): "Vert"}
    out["traffic_level"] = out["traffic_color"].apply(
        lambda c: label_map.get(str(c), "Sans donnée")
    )
    return out


# ──────────────────────────────────────────────────────────────
# SIDEBAR
# ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filtres")

    available_dates = fetch_available_dates()
    if available_dates:
        st.caption(f"Dates disponibles : {available_dates[0]} → {available_dates[-1]}")

    chosen_day = st.date_input("Date", value=ddate(2026, 1, 20))
    limit = st.number_input("Limit API (lignes/page)", 50, 500, DEFAULT_LIMIT, 50)

    st.divider()
    mode = st.selectbox(
        "Couleur selon",
        ["vitesse_moyenne", "heures_congestion", "debit_moyen_horaire"],
    )

    if mode == "vitesse_moyenne":
        red_thr    = st.slider("Rouge si vitesse ≤", 0, 60, 10)
        orange_thr = st.slider("Orange si vitesse ≤", 0, 80, 25)
        mode_help  = "Plus la vitesse est basse, plus c'est congestionné."
    elif mode == "heures_congestion":
        red_thr    = st.slider("Rouge si heures_congestion ≥", 0, 24, 3)
        orange_thr = st.slider("Orange si heures_congestion ≥", 0, 24, 1)
        mode_help  = "Plus il y a d'heures congestionnées, pire c'est."
    else:
        red_thr    = st.slider("Rouge si débit ≥", 0, 3000, 1200)
        orange_thr = st.slider("Orange si débit ≥", 0, 3000, 800)
        mode_help  = "Heuristique : plus le débit est haut, plus c'est chargé."

    st.divider()
    traffic_filter = st.multiselect(
        "Afficher seulement",
        ["Rouge", "Orange", "Vert", "Sans donnée"],
        default=["Rouge", "Orange", "Vert", "Sans donnée"],
    )
    show_table = st.checkbox("Afficher table complète", value=True)

if not st.button("📥 Charger + Afficher", type="primary"):
    st.info("Clique sur **📥 Charger + Afficher** pour démarrer.")
    st.stop()

# ──────────────────────────────────────────────────────────────
# CHARGEMENT DONNÉES
# ──────────────────────────────────────────────────────────────
with st.spinner("Chargement depuis l'API locale…"):
    df, err = fetch_aggregated(chosen_day.isoformat(), int(limit))

if err and df.empty:
    st.error(f"❌ Erreur API : {err}\n\n**Vérifiez que l'API tourne** : `uvicorn api.local_api:app --port 8000`")
    st.stop()
elif err:
    st.warning(err)

if df.empty:
    st.warning("Aucune donnée pour cette date. Lancez d'abord le pipeline.")
    st.stop()

# Cast numérique
for col in ["debit_moyen_horaire", "total_vehicules", "nb_mesures",
            "vitesse_moyenne", "heures_congestion", "heure_pic"]:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

if mode not in df.columns:
    st.error(f"La colonne '{mode}' est absente des données.")
    st.stop()

df = add_traffic_class(df, mode, float(red_thr), float(orange_thr))
df_vis = df[df["traffic_level"].isin(traffic_filter)].copy()

# ──────────────────────────────────────────────────────────────
# KPIs
# ──────────────────────────────────────────────────────────────
st.subheader("Résumé & légende")
st.info(f"{mode_help}")

counts = df["traffic_level"].value_counts()
c1, c2, c3, c4 = st.columns(4)
c1.metric("🟥 Rouges",      int(counts.get("Rouge", 0)))
c2.metric("🟧 Oranges",     int(counts.get("Orange", 0)))
c3.metric("🟩 Verts",       int(counts.get("Vert", 0)))
c4.metric("⬜ Sans donnée", int(counts.get("Sans donnée", 0)))

k1, k2, k3, k4 = st.columns(4)
k1.metric("Arcs total", f"{len(df):,}".replace(",", " "))
if "total_vehicules" in df.columns:
    k2.metric("Total véhicules", f"{df['total_vehicules'].sum():,.0f}".replace(",", " "))
if "vitesse_moyenne" in df.columns:
    k3.metric("Vitesse moyenne", f"{df['vitesse_moyenne'].mean():.1f}")
if "debit_moyen_horaire" in df.columns:
    k4.metric("Débit moyen", f"{df['debit_moyen_horaire'].mean():.1f}")

# ──────────────────────────────────────────────────────────────
# CARTE
# ──────────────────────────────────────────────────────────────
with st.spinner("Chargement référentiel géographique…"):
    arcs = load_arc_paths_wgs84()

dfm = df_vis.merge(arcs, on="arc_id", how="left").dropna(subset=["paths"])

if dfm.empty:
    st.warning("Aucune géométrie disponible après jointure (filtres trop stricts ou données manquantes).")
else:
    rows_map = []
    for _, r in dfm.iterrows():
        for path in r["paths"]:
            rows_map.append({
                "arc_id":        r.get("arc_id"),
                "libelle":       r.get("libelle"),
                "metric":        r.get(mode),
                "path":          path,
                "color":         r.get("traffic_color"),
                "traffic_level": r.get("traffic_level"),
            })
    lines = pd.DataFrame(rows_map)

    try:
        p0 = lines.iloc[0]["path"][0]
        center_lon, center_lat = float(p0[0]), float(p0[1])
    except Exception:
        center_lon, center_lat = 2.3522, 48.8566

    view  = pdk.ViewState(latitude=center_lat, longitude=center_lon, zoom=12, pitch=0)
    layer = pdk.Layer(
        "PathLayer",
        data=lines,
        get_path="path",
        get_color="color",
        width_scale=25,
        width_min_pixels=3,
        pickable=True,
    )
    st.subheader("🗺️ Carte du trafic")
    st.pydeck_chart(
        pdk.Deck(
            layers=[layer],
            initial_view_state=view,
            tooltip={"text": f"arc_id: {{arc_id}}\nlibelle: {{libelle}}\n{mode}: {{metric}}\nniveau: {{traffic_level}}"},
        ),
        use_container_width=True,
    )

# ──────────────────────────────────────────────────────────────
# VISUALISATIONS
# ──────────────────────────────────────────────────────────────
st.subheader("📊 Analyses")

colA, colB = st.columns(2)
with colA:
    st.write("Top 10 — pire trafic")
    top = df.sort_values(mode, ascending=(mode == "vitesse_moyenne")).head(10)
    cols_show = [c for c in ["libelle", "arc_id", mode, "total_vehicules", "heure_pic", "traffic_level"] if c in top.columns]
    st.dataframe(top[cols_show], use_container_width=True)

with colB:
    st.write("Répartition des niveaux")
    st.bar_chart(df["traffic_level"].value_counts())

colC, colD = st.columns(2)
with colC:
    st.write(f"Histogramme — {mode}")
    series = df[mode].dropna()
    if not series.empty:
        st.bar_chart(series.round(0).value_counts().sort_index())

with colD:
    st.write("Scatter débit vs vitesse")
    if "debit_moyen_horaire" in df.columns and "vitesse_moyenne" in df.columns:
        scat = df[["debit_moyen_horaire", "vitesse_moyenne"]].dropna()
        st.scatter_chart(scat, x="debit_moyen_horaire", y="vitesse_moyenne")

# ──────────────────────────────────────────────────────────────
# TABLE + EXPORT
# ──────────────────────────────────────────────────────────────
if show_table:
    st.subheader("📋 Table filtrée")
    st.dataframe(df_vis, use_container_width=True)

csv = df_vis.to_csv(index=False).encode("utf-8")
st.download_button(
    "⬇️ Télécharger CSV",
    data=csv,
    file_name=f"cityflow_{chosen_day.isoformat()}.csv",
    mime="text/csv",
)

st.caption("API locale SQLite — données Paris Open Data (comptages routiers)")
