"""Ingesta del Data Lake de Transfermarkt en MongoDB.

Uso:  python -m etl.seed          (desde la raíz del proyecto)

1. Transforma los CSV (etl/transform.py).
2. Recrea las colecciones con validación $jsonSchema y las TimeSeries.
3. Inserta los documentos y crea los índices.
"""
import math
import time
from datetime import datetime, timezone

import pandas as pd
from pymongo import MongoClient
from pymongo.errors import BulkWriteError

from config import APP_SEASONS, DB_NAME, MONGO_URI, season_label
from etl.schema import create_collections, ensure_indexes
from etl.transform import build_all, comp_display_names, normalize_name


def clean(v):
    """Convierte tipos de numpy/pandas a tipos BSON nativos."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
        return None if isinstance(v, float) and math.isnan(v) else v
    if isinstance(v, pd.Timestamp):
        return None if pd.isna(v) else v.to_pydatetime()
    return v


def per90(x, minutes):
    return round(x * 90 / minutes, 3) if minutes else 0.0


def player_season_doc(r, bdo_rank, now):
    c = {k: clean(v) for k, v in r.items() if k not in ("titles", "citizenships")}
    losses = c["decided"] - c["wins"] - c["draws"]
    ga = c["goals"] + c["assists"]
    return {
        "player_id": c["player_id"], "season": c["season"], "season_label": season_label(c["season"]),
        "name": c["name"], "name_search": normalize_name(c["name"]),
        "image_url": c["image_url"], "birth_date": c["date_of_birth"], "age": float(c["age"]),
        "nationality": c["nationality"], "citizenships": list(r["citizenships"]),
        "place_of_birth": c["place_of_birth"],
        "position": c["position"], "sub_position": c["sub_position"], "foot": c["foot"],
        "height_cm": c["height_cm"],
        "club": {"id": int(c["club_id"]), "name": c["club_name"]},
        "league": {"id": c["league_id"], "name": c["league_name"], "country": c["league_country"]},
        "performance": {
            "apps": c["apps"], "minutes": c["minutes"], "goals": c["goals"], "assists": c["assists"],
            "ga": ga, "goals_per90": per90(c["goals"], c["minutes"]), "ga_per90": per90(ga, c["minutes"]),
            "yellow": c["yellow"], "red": c["red"], "top5_apps": c["top5_apps"],
            "wins": c["wins"], "draws": c["draws"], "losses": losses,
            "win_rate": round(c["wins"] / c["decided"], 4) if c["decided"] else 0.0,
        },
        "ucl": {"apps": c["cl_apps"], "goals": c["cl_goals"], "assists": c["cl_assists"],
                "ga": c["cl_goals"] + c["cl_assists"]},
        "national": {"apps": c["nat_apps"], "goals": c["nat_goals"], "assists": c["nat_assists"],
                     "caps": int(c["national_caps"]), "career_goals": int(c["national_goals"])},
        "titles": [{"competition_id": t["competition_id"], "name": t["name"], "weight": float(t["weight"])}
                   for t in r["titles"]],
        "title_points": float(c["title_points"]),
        "market_value_eur": float(c["market_value_eur"]),
        "market_value_start_eur": float(c["market_value_start_eur"]),
        "ballon_dor_rank": bdo_rank,
        "ml": None,
        "updated_at": now,
    }


def insert_batches(coll, docs, size=10_000):
    """Inserta por lotes; los documentos que no pasan el $jsonSchema se informan, no abortan."""
    rejected = []
    for i in range(0, len(docs), size):
        try:
            coll.insert_many(docs[i:i + size], ordered=False)
        except BulkWriteError as e:
            rejected += e.details["writeErrors"]
    if rejected:
        print(f"  {coll.name}: {len(rejected)} documentos rechazados por validación")
        for err in rejected[:5]:
            rule = err.get("errInfo", {}).get("details", {}).get("schemaRulesNotSatisfied", err["errmsg"])
            print(f"    player_id={err['op'].get('player_id')} -> {rule}")


def main():
    t0 = time.time()
    data = build_all()
    ps = data["player_seasons"]
    print(f"Transformación: {time.time() - t0:.1f}s · {len(ps):,} temporadas-jugador "
          f"({ps.season.min()}-{ps.season.max()}) · {len(data['matches']):,} partidos · "
          f"{len(data['market_values']):,} tasaciones")
    if data["bdo_missing"]:
        print("AVISO: podios sin resolver ->", data["bdo_missing"])

    db = MongoClient(MONGO_URI)[DB_NAME]
    create_collections(db)
    now = datetime.now(timezone.utc)

    ranks = {(r.player_id, r.season): int(r["rank"]) for _, r in data["ballon_dor"].iterrows()}
    docs = [player_season_doc(r, ranks.get((r["player_id"], r["season"])), now)
            for r in ps.to_dict("records")]
    insert_batches(db.player_seasons, docs)

    m = data["matches"]
    match_docs = [{
        "date": clean(r["date"]),
        "meta": {"player_id": int(r["player_id"]), "club_id": int(r["player_club_id"]),
                 "competition_id": r["competition_id"]},
        "season": int(r["season"]), "game_id": int(r["game_id"]), "club_name": clean(r["club_name"]),
        "goals": int(r["goals"]), "assists": int(r["assists"]), "minutes": int(r["minutes_played"]),
        "yellow": int(r["yellow_cards"]), "red": int(r["red_cards"]), "result": clean(r["result"]),
        "source": "transfermarkt",
    } for r in m.to_dict("records")]
    insert_batches(db.match_performances, match_docs)

    mv_docs = [{"date": clean(r["date"]), "meta": {"player_id": int(r["player_id"])},
                "value_eur": float(r["value_eur"])} for r in data["market_values"].to_dict("records")]
    insert_batches(db.market_values, mv_docs)

    db.ballon_dor_history.insert_many(data["ballon_dor"].to_dict("records"))
    comps = data["competitions"][["competition_id", "name", "type", "sub_type", "country_name"]].copy()
    comps["display_name"] = comps.competition_id.map(comp_display_names(data["competitions"]))
    db.competitions.insert_many([{k: clean(v) for k, v in r.items()} for r in comps.to_dict("records")])

    ensure_indexes(db)
    for name in ("player_seasons", "match_performances", "market_values", "ballon_dor_history", "competitions"):
        print(f"  {name:<20} {db[name].estimated_document_count():>9,} docs")
    for s in APP_SEASONS:
        print(f"  candidatos {season_label(s)}: {db.player_seasons.count_documents({'season': s}):,}")
    print(f"Seed completado en {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
