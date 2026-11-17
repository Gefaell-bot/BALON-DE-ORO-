"""Demuestra el uso de índices con explain("executionStats"): COLLSCAN -> IXSCAN.

Uso:  python -m scripts.explain_indexes

Para cada consulta: borra su índice, ejecuta explain, recrea el índice y repite.
Guarda el informe en docs/explain_report.md y los explain completos en docs/explain/*.json.
"""
import json

from bson import json_util
from pymongo import MongoClient

from backend import pipelines as P
from config import DB_NAME, MONGO_URI, ROOT
from etl.schema import INDEXES

db = MongoClient(MONGO_URI)[DB_NAME]
OUT = ROOT / "docs"
(OUT / "explain").mkdir(parents=True, exist_ok=True)

maxima = next(db.player_seasons.aggregate(P.season_maxima_pipeline(2025)))
CASES = [
    {"title": "Máximos goleadores (índice compuesto goals_assists)", "index": "goals_assists",
     "cmd": {"find": "player_seasons", "filter": {"performance.goals": {"$gte": 25}},
             "sort": {"performance.goals": -1, "performance.assists": -1}, "limit": 20}},
    {"title": "Jugadores más valiosos (índice market_value)", "index": "market_value",
     "cmd": {"find": "player_seasons", "filter": {"market_value_eur": {"$gte": 100_000_000}},
             "sort": {"market_value_eur": -1}}},
    {"title": "Filtros del dashboard: temporada + liga + posición + edad (ESR)", "index": "season_league_position_age",
     "cmd": {"find": "player_seasons",
             "filter": {"season": 2025, "league.id": "ES1", "position": "Attack", "age": {"$lte": 23}}}},
    {"title": "Pipeline de ranking ordenado por probabilidad ML ($match + $sort -> índice)",
     "index": "season_ml_probability",
     "cmd": {"aggregate": "player_seasons",
             "pipeline": P.ranking_pipeline({"season": 2025}, maxima, sort="ml", limit=10), "cursor": {}}},
    {"title": "TimeSeries: partidos de un jugador por fecha", "index": "player_date", "coll": "match_performances",
     "cmd": {"find": "match_performances", "filter": {"meta.player_id": 937958}, "sort": {"date": -1}}},
]


def walk(node, found):
    """Recorre el explain y recoge etapas, índices y contadores (find, aggregate y TimeSeries)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "stage" and isinstance(v, str):
                found["stages"].append(v)
            elif k == "indexName":
                found["indexes"].add(v)
            elif k in ("totalDocsExamined", "totalKeysExamined", "nReturned", "executionTimeMillis") \
                    and isinstance(v, int) and k not in found:
                found[k] = v
            elif k in ("rejectedPlans", "allPlansExecution"):
                continue
            walk(v, found)
    elif isinstance(node, list):
        for v in node:
            walk(v, found)
    return found


def explain(cmd):
    raw = db.command("explain", cmd, verbosity="executionStats")
    s = walk(raw, {"stages": [], "indexes": set()})
    scan = "IXSCAN" if "IXSCAN" in s["stages"] else ("COLLSCAN" if "COLLSCAN" in s["stages"] else "?")
    return raw, {"scan": scan, "index": ", ".join(sorted(s["indexes"])) or "—",
                 "sort": "sí" if "SORT" in s["stages"] else "no",
                 "docs": s.get("totalDocsExamined", 0), "keys": s.get("totalKeysExamined", 0),
                 "returned": s.get("nReturned", 0), "ms": s.get("executionTimeMillis", 0)}


def spec(name, coll):
    return next((c, k, o) for c, k, o in INDEXES if o["name"] == name and c == coll)


lines = ["# Informe explain(\"executionStats\")", "",
         "En TimeSeries, *docs examinados* son **buckets** internos (cada uno agrupa varios partidos).", "",
         "| Consulta | Sin índice | Docs examinados | SORT en memoria | Con índice | Índice usado "
         "| Claves / Docs examinados | SORT en memoria | ms antes → después |",
         "|---|---|---|---|---|---|---|---|---|"]
for i, case in enumerate(CASES, 1):
    coll = case.get("coll", "player_seasons")
    c, keys, opts = spec(case["index"], coll)
    db[coll].drop_index(case["index"])
    raw_before, before = explain(case["cmd"])
    db[coll].create_index(keys, **opts)
    raw_after, after = explain(case["cmd"])
    for tag, raw in (("antes", raw_before), ("despues", raw_after)):
        (OUT / "explain" / f"{i}_{case['index']}_{tag}.json").write_text(
            json.dumps(json.loads(json_util.dumps(raw)), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n{i}. {case['title']}")
    for tag, r in (("SIN", before), ("CON", after)):
        print(f"   {tag} índice: {r['scan']:<8} índice={r['index']:<28} docs={r['docs']:>7,} "
              f"claves={r['keys']:>7,} SORT en memoria={r['sort']:<2} {r['ms']} ms")
    lines.append(f"| {case['title']} | {before['scan']} ({before['index']}) | {before['docs']:,} | {before['sort']} "
                 f"| {after['scan']} | `{after['index']}` | {after['keys']:,} / {after['docs']:,} | {after['sort']} "
                 f"| {before['ms']} → {after['ms']} |")

(OUT / "explain_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"\nInforme: {OUT / 'explain_report.md'}")
