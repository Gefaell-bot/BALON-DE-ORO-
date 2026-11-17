"""API REST del Balón de Oro (FastAPI + PyMongo).

Arranque:  python -m uvicorn backend.main:app --reload     (desde la raíz del proyecto)
Docs interactivas: http://localhost:8000/docs
"""
import re
from datetime import datetime, timezone
from functools import lru_cache
from typing import Literal, Optional

from bson import ObjectId
from fastapi import FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.staticfiles import StaticFiles
from pymongo import MongoClient
from pymongo.errors import WriteError

from backend import pipelines as P
from backend.models import MatchIn, StatsPatch
from config import APP_SEASONS, DB_NAME, MONGO_URI, ROOT, season_label
from etl.transform import NATIONAL_COMPS, normalize_name
from ml.predict import rescore_season

client = MongoClient(MONGO_URI)
db = client[DB_NAME]
app = FastAPI(title="Balón de Oro Analytics API", version="1.0.0",
              description="Ranking, predicción y escritura de rendimiento sobre el Data Lake de Transfermarkt.")


def to_json(data):
    return jsonable_encoder(data, custom_encoder={ObjectId: str})


def validation_error(e: WriteError) -> HTTPException:
    """422 con las reglas del $jsonSchema incumplidas (errInfo trae ObjectIds: se serializan)."""
    rules = (e.details or {}).get("errInfo", {}).get("details", {}).get("schemaRulesNotSatisfied")
    return HTTPException(422, to_json({"error": "El documento no supera el $jsonSchema", "rules": rules}))


def season_of(date: datetime) -> int:
    return date.year if date.month >= 8 else date.year - 1


@lru_cache(maxsize=16)
def season_maxima(season: int):
    res = list(db.player_seasons.aggregate(P.season_maxima_pipeline(season)))
    if not res:
        raise HTTPException(404, f"Sin datos para la temporada {season}")
    return res[0]


def check_season(season: int):
    if season not in APP_SEASONS:
        raise HTTPException(404, f"Temporada no disponible. Opciones: {APP_SEASONS}")


# ----------------------------------------------------------------------------- lectura
@app.get("/api/health")
def health():
    return {"mongo": client.admin.command("ping")["ok"] == 1.0, "db": DB_NAME,
            "server_version": client.server_info()["version"]}


@app.get("/api/meta")
def meta(season: int = APP_SEASONS[-1]):
    """Opciones para los filtros: ligas, posiciones, clubes y rango de edad de la temporada."""
    check_season(season)
    leagues = list(db.player_seasons.aggregate([
        {"$match": {"season": season}},
        {"$group": {"_id": "$league.id", "name": {"$first": "$league.name"},
                    "country": {"$first": "$league.country"}, "count": {"$sum": 1}}},
        {"$sort": {"count": -1}}]))
    clubs = list(db.player_seasons.aggregate([
        {"$match": {"season": season}},
        {"$group": {"_id": "$club.id", "name": {"$first": "$club.name"}, "league": {"$first": "$league.id"},
                    "value": {"$sum": "$market_value_eur"}}},
        {"$sort": {"value": -1}}, {"$limit": 120}, {"$sort": {"name": 1}}]))
    ages = next(db.player_seasons.aggregate([
        {"$match": {"season": season}},
        {"$group": {"_id": None, "min": {"$min": "$age"}, "max": {"$max": "$age"}}}]))
    model = db.model_runs.find_one(sort=[("created_at", -1)], projection={"_id": 0, "per_season": 0})
    return to_json({
        "season": season, "season_label": season_label(season),
        "seasons": [{"value": s, "label": season_label(s)} for s in APP_SEASONS],
        "leagues": [{"id": l["_id"], "name": l["name"], "country": l["country"], "count": l["count"]} for l in leagues],
        "positions": db.player_seasons.distinct("position", {"season": season}),
        "clubs": [{"id": c["_id"], "name": c["name"], "league": c["league"]} for c in clubs],
        "age": {"min": int(ages["min"]), "max": int(ages["max"]) + 1},
        "model": model,
    })


@app.get("/api/rankings")
def rankings(season: int = APP_SEASONS[-1], league: Optional[str] = None, position: Optional[str] = None,
             club: Optional[int] = None, age_min: Optional[float] = None, age_max: Optional[float] = None,
             sort: Literal["score", "ml"] = "score", limit: int = Query(50, ge=1, le=200)):
    """Ranking ponderado + categorías ($facet, $bucket, $bucketAuto) en una sola agregación."""
    check_season(season)
    match = P.build_match(season, league, position, club, age_min, age_max)
    res = next(db.player_seasons.aggregate(P.ranking_pipeline(match, season_maxima(season), sort, limit)))
    for t in res["tiers"]:
        t["label"] = P.tier_label(t["_id"])
    res["tiers"].sort(key=lambda t: -t["_id"] if isinstance(t["_id"], (int, float)) else 1)
    res["summary"] = (res["summary"] or [{}])[0]
    res["weights"] = P.WEIGHTS
    return to_json(res)


@app.get("/api/players/search")
def search(q: str = Query(min_length=2), season: int = APP_SEASONS[-1], limit: int = 10):
    """Búsqueda por prefijo sobre name_search (índice) con respaldo por palabra."""
    term = normalize_name(q)
    proj = {"_id": 0, "player_id": 1, "name": 1, "club.name": 1, "position": 1, "image_url": 1, "ml.rank": 1}
    found = list(db.player_seasons.find({"season": season, "name_search": {"$regex": f"^{re.escape(term)}"}}, proj)
                 .sort("market_value_eur", -1).limit(limit))
    if len(found) < limit:
        seen = {f["player_id"] for f in found}
        extra = db.player_seasons.find({"season": season, "name_search": {"$regex": rf"\b{re.escape(term)}"},
                                        "player_id": {"$nin": list(seen)}}, proj) \
                                 .sort("market_value_eur", -1).limit(limit - len(found))
        found += list(extra)
    return to_json(found)


@app.get("/api/players/{player_id}")
def player_detail(player_id: int, season: int = APP_SEASONS[-1]):
    """Perfil + puntuación + timeline TimeSeries de partidos + histórico de valor de mercado."""
    check_season(season)
    match = P.build_match(season, player_ids=[player_id])
    docs = list(db.player_seasons.aggregate([{"$match": match}, *P.score_stages(season_maxima(season)),
                                              {"$project": {"_id": 0, "name_search": 0}}]))
    if not docs:
        raise HTTPException(404, "Jugador no encontrado en esa temporada")
    timeline = list(db.match_performances.aggregate(P.player_timeline_pipeline(player_id, season)))
    values = list(db.market_values.find({"meta.player_id": player_id}, {"_id": 0, "date": 1, "value_eur": 1})
                  .sort("date", 1))
    history = list(db.player_seasons.find({"player_id": player_id},
                                          {"_id": 0, "season": 1, "season_label": 1, "performance.ga": 1,
                                           "performance.goals": 1, "title_points": 1, "ml.probability": 1,
                                           "ml.rank": 1, "ballon_dor_rank": 1, "club.name": 1}).sort("season", 1))
    return to_json({"player": docs[0], "matches": timeline, "market_values": values, "career": history})


@app.get("/api/compare")
def compare(ids: str = Query(description="IDs separados por comas, máx. 6"), season: int = APP_SEASONS[-1]):
    """Datos para los gráficos comparativos: componentes de la puntuación + G+A acumulado mensual."""
    check_season(season)
    player_ids = [int(x) for x in ids.split(",") if x.strip()][:6]
    match = P.build_match(season, player_ids=player_ids)
    players = list(db.player_seasons.aggregate([{"$match": match}, *P.score_stages(season_maxima(season)),
                                                 {"$project": P.CARD_PROJECTION}]))
    monthly = list(db.match_performances.aggregate(P.monthly_ga_pipeline(player_ids, season)))
    return to_json({"players": players, "monthly": monthly})


@app.get("/api/history")
def history():
    """Podios reales vs. puesto que les dio el modelo fuera de muestra (LOSO)."""
    run = db.model_runs.find_one(sort=[("created_at", -1)]) or {}
    pred = {p["season"]: p for p in run.get("per_season", [])}
    rows = list(db.ballon_dor_history.find({}, {"_id": 0}).sort([("award_year", -1), ("rank", 1)]))
    for r in rows:
        r["model_rank"] = (db.player_seasons.find_one({"player_id": r["player_id"], "season": r["season"]},
                                                      {"ml.rank": 1}) or {}).get("ml", {}).get("rank")
    return to_json({"podiums": rows, "per_season": list(pred.values()), "metrics": run.get("metrics")})


# ----------------------------------------------------------------------------- escritura
@app.post("/api/matches", status_code=201)
def add_match(m: MatchIn):
    """INSERT en la TimeSeries + UPSERT (update con pipeline) del agregado del jugador + re-scoring ML."""
    season = season_of(m.date)
    base = db.player_seasons.find_one({"player_id": m.player_id}, sort=[("season", -1)])
    if base is None:
        raise HTTPException(404, "Jugador desconocido")
    is_nat = m.competition_id in NATIONAL_COMPS
    doc = {"date": m.date, "meta": {"player_id": m.player_id, "club_id": m.club_id or base["club"]["id"],
                                    "competition_id": m.competition_id},
           "season": season, "club_name": None if is_nat else base["club"]["name"],
           "goals": m.goals, "assists": m.assists, "minutes": m.minutes, "yellow": m.yellow, "red": m.red,
           "result": m.result, "source": "manual"}
    inserted = db.match_performances.insert_one(doc)

    # Si el jugador no tiene documento en esa temporada se crea (upsert) con su perfil más reciente.
    keep = ("name", "name_search", "image_url", "birth_date", "nationality", "citizenships", "place_of_birth",
            "position", "sub_position", "foot", "height_cm", "club", "league")
    on_insert = {k: base.get(k) for k in keep}
    on_insert.update({"season_label": season_label(season), "titles": [], "title_points": 0.0,
                      "market_value_eur": base.get("market_value_eur", 0.0),
                      "market_value_start_eur": base.get("market_value_eur", 0.0),
                      "ballon_dor_rank": None, "ml": None, "national": {},
                      "age": round(base["age"] + (season - base["season"]), 1)})
    update = [{"$set": {k: {"$ifNull": [f"${k}", {"$literal": v}]} for k, v in on_insert.items()}},
              *P.recompute_after_match(m)]
    try:
        db.player_seasons.update_one({"player_id": m.player_id, "season": season}, update, upsert=True)
    except WriteError as e:
        db.match_performances.delete_one({"_id": inserted.inserted_id})  # compensación
        raise validation_error(e)
    season_maxima.cache_clear()
    rescored = rescore_season(db, season) if season in APP_SEASONS else 0
    updated = db.player_seasons.find_one({"player_id": m.player_id, "season": season},
                                         {"_id": 0, "name": 1, "season": 1, "performance": 1, "ucl": 1, "ml": 1})
    return to_json({"inserted_id": str(inserted.inserted_id), "season": season,
                    "rescored_documents": rescored, "player": updated})


@app.patch("/api/players/{player_id}/seasons/{season}")
def patch_stats(player_id: int, season: int, body: StatsPatch):
    """UPDATE parcial: valor de mercado y/o añadir un título ($addToSet + $inc)."""
    upd = {"$set": {"updated_at": datetime.now(timezone.utc)}}
    if body.market_value_eur is not None:
        upd["$set"]["market_value_eur"] = body.market_value_eur
    flt = {"player_id": player_id, "season": season}
    if body.add_title:
        # Solo suma puntos si el título no estaba ya (evita duplicar con dos clics).
        flt["titles.competition_id"] = {"$ne": body.add_title.competition_id}
        upd["$addToSet"] = {"titles": body.add_title.model_dump()}
        upd["$inc"] = {"title_points": body.add_title.weight}
    try:
        res = db.player_seasons.update_one(flt, upd)
    except WriteError as e:
        raise validation_error(e)
    if res.matched_count == 0:
        if db.player_seasons.count_documents({"player_id": player_id, "season": season}) == 0:
            raise HTTPException(404, "Jugador/temporada no encontrado")
        raise HTTPException(409, "Ese título ya estaba registrado")
    season_maxima.cache_clear()
    rescore_season(db, season)
    return to_json(db.player_seasons.find_one({"player_id": player_id, "season": season},
                                              {"_id": 0, "name": 1, "market_value_eur": 1, "titles": 1,
                                               "title_points": 1, "ml": 1}))


app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
