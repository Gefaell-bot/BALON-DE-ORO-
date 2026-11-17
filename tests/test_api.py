"""Smoke tests de la API. Uso:  python -m tests.test_api

Las lecturas van contra la BD real; las escrituras contra una copia temporal
(balon_de_oro_test) que se borra al terminar.
"""
import os
import sys

from fastapi.testclient import TestClient
from pymongo import MongoClient

from config import DB_NAME, MONGO_URI

TEST_DB = DB_NAME + "_test"
src, dst = MongoClient(MONGO_URI)[DB_NAME], MongoClient(MONGO_URI)[TEST_DB]


def clone_test_db():
    """Copia mínima: la temporada 2025 + TimeSeries de 2 jugadores, con mismos validadores e índices."""
    from etl.schema import create_collections, ensure_indexes
    create_collections(dst)
    dst.player_seasons.insert_many(list(src.player_seasons.find({"season": {"$in": [2024, 2025]}}, {"_id": 0})))
    dst.match_performances.insert_many(list(src.match_performances.find(
        {"meta.player_id": {"$in": [937958, 342229]}}, {"_id": 0})))
    dst.ballon_dor_history.insert_many(list(src.ballon_dor_history.find({}, {"_id": 0})))
    dst.model_runs.insert_one(src.model_runs.find_one(sort=[("created_at", -1)], projection={"_id": 0}))
    ensure_indexes(dst)


fails = []


def check(name, cond, info=""):
    print(f"[{'OK ' if cond else 'FALLO'}] {name}" + (f"  ->  {info}" if info else ""))
    if not cond:
        fails.append(name)


clone_test_db()
os.environ["MONGO_DB"] = TEST_DB
import config  # noqa: E402
config.DB_NAME = TEST_DB
from backend.main import app  # noqa: E402

c = TestClient(app)

r = c.get("/api/health").json()
check("health", r["mongo"], r["server_version"])

m = c.get("/api/meta?season=2025").json()
check("meta: ligas/posiciones/clubes", len(m["leagues"]) > 10 and len(m["positions"]) == 4 and m["clubs"],
      f"{len(m['leagues'])} ligas, {len(m['clubs'])} clubes, edad {m['age']}")

r = c.get("/api/rankings?season=2025&limit=10").json()
lb = r["leaderboard"]
check("rankings: ordenado por puntuación", all(a["bdo"]["score"] >= b["bdo"]["score"] for a, b in zip(lb, lb[1:])),
      " | ".join(f"{p['name']} {p['bdo']['score']}" for p in lb[:5]))
check("rankings: $bucket con categorías", {t["label"] for t in r["tiers"]} >= {"Resto"},
      ", ".join(f"{t['label']}={t['count']}" for t in r["tiers"]))
check("rankings: $bucketAuto 5 franjas de valor", len(r["market_bands"]) == 5)
check("rankings: promesas ≤21 años", all(p["age"] <= 21 for p in r["promesas"]),
      ", ".join(p["name"] for p in r["promesas"][:3]))

r = c.get("/api/rankings?season=2025&league=ES1&position=Attack&age_max=23").json()
check("filtros liga+posición+edad", all(p["league"]["id"] == "ES1" and p["position"] == "Attack" and p["age"] <= 23
                                       for p in r["leaderboard"]), f"{r['summary'].get('candidates')} candidatos")
r = c.get("/api/rankings?season=2025&sort=ml&limit=5").json()
probs = [p["ml"]["probability"] for p in r["leaderboard"]]
check("orden por probabilidad ML", probs == sorted(probs, reverse=True), str([round(p, 3) for p in probs]))

r = c.get("/api/players/search?q=yamal&season=2025").json()
check("búsqueda por palabra", any(p["player_id"] == 937958 for p in r), r[0]["name"] if r else "-")

d = c.get("/api/players/937958?season=2025").json()
check("detalle + TimeSeries con ventanas", d["matches"] and d["matches"][-1]["cum_ga"] == d["player"]["performance"]["ga"],
      f"{len(d['matches'])} partidos, G+A acumulado {d['matches'][-1]['cum_ga']}")

r = c.get("/api/compare?ids=937958,342229&season=2025").json()
check("compare", len(r["players"]) == 2 and r["monthly"], f"{len(r['monthly'])} puntos mensuales")

before = d["player"]["performance"]
r = c.post("/api/matches", json={"player_id": 937958, "date": "2026-05-20T20:00:00Z", "competition_id": "ES1",
                                 "goals": 2, "assists": 1, "minutes": 90, "result": "W"})
check("POST match 201", r.status_code == 201, str(r.status_code))
after = r.json()["player"]["performance"]
check("upsert agregado (+1 PJ, +2 G, +1 A, ga y per90 recalculados)",
      (after["apps"], after["goals"], after["assists"], after["ga"]) ==
      (before["apps"] + 1, before["goals"] + 2, before["assists"] + 1, before["ga"] + 3)
      and after["ga_per90"] != before["ga_per90"], f"{before['ga']} -> {after['ga']}, per90 {after['ga_per90']}")
check("re-scoring ML de la temporada", r.json()["rescored_documents"] > 3000)

r = c.post("/api/matches", json={"player_id": 937958, "date": "2026-05-21T20:00:00Z", "competition_id": "ES1",
                                 "goals": 99, "result": "W"})
check("validación Pydantic (goals=99 -> 422)", r.status_code == 422)

r = c.post("/api/matches", json={"player_id": 937958, "date": "2026-09-01T20:00:00Z", "competition_id": "ES1",
                                 "goals": 1, "minutes": 80, "result": "D"})
up = dst.player_seasons.find_one({"player_id": 937958, "season": 2026})
check("upsert crea temporada nueva 2026-27", r.status_code == 201 and up and up["performance"]["apps"] == 1,
      f"{up['season_label'] if up else '-'} {up['performance'] if up else ''}")

r = c.patch("/api/players/937958/seasons/2025", json={"add_title": {"competition_id": "CDR", "name": "Copa del Rey",
                                                                   "weight": 1}, "market_value_eur": 210000000})
check("PATCH título + valor", r.status_code == 200 and r.json()["market_value_eur"] == 210000000, str(r.status_code))
r2 = c.patch("/api/players/937958/seasons/2025", json={"add_title": {"competition_id": "CDR", "name": "Copa del Rey",
                                                                    "weight": 1}})
check("PATCH título duplicado -> 409", r2.status_code == 409)
r = c.patch("/api/players/937958/seasons/2025", json={"market_value_eur": 900000000})
check("$jsonSchema rechaza valor > 500 M€ -> 422", r.status_code == 422)

h = c.get("/api/history").json()
check("history", len(h["podiums"]) == 36, f"MRR {h['metrics']['logistic_regression']['winner_mrr']:.3f}")

MongoClient(MONGO_URI).drop_database(TEST_DB)
print(f"\n{len(fails)} fallos" if fails else "\nTodos los tests OK")
sys.exit(1 if fails else 0)
