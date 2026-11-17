"""Verificación de integridad: contrasta lo cargado en MongoDB con el Data Lake original.

Uso:  python -m scripts.verify_data
Sale con código 1 si alguna comprobación bloqueante falla.
"""
import sys
from datetime import datetime

import pandas as pd
from pymongo import MongoClient
from pymongo.errors import WriteError

from config import DB_NAME, MONGO_URI, RAW_DIR
from etl.transform import TOP5, league_champions

db = MongoClient(MONGO_URI)[DB_NAME]
failures = []


def check(name, ok, detail=""):
    print(f"[{'OK ' if ok else 'FALLO'}] {name}" + (f"  ->  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


# 1. Campeones de liga calculados (desde partidos) vs clasificación oficial del Data Lake.
games = pd.read_csv(RAW_DIR / "games.csv.gz")
mine = league_champions(games)
mine = mine[mine.competition_id.isin(TOP5) & mine.season.between(2015, 2024)]
tcs = pd.read_csv(RAW_DIR / "team_competitions_seasons.csv", low_memory=False,
                  usecols=["competition_id", "season_id", "season_rank", "club_id", "team_name"])
lake = tcs[(tcs.season_rank == 1) & tcs.competition_id.isin(TOP5)].rename(columns={"season_id": "season"})
cmp = mine.merge(lake, on=["competition_id", "season"], how="left", suffixes=("", "_lake"))
bad = cmp[cmp.club_id != cmp.club_id_lake]
check("Campeones top-5 2015-2024 coinciden con el Data Lake", bad.empty,
      f"{len(cmp) - len(bad)}/{len(cmp)} coinciden" + ("" if bad.empty else f"; difieren: {bad.to_dict('records')}"))

# 2. Goles de liga 2024-25 (suma por partido) vs rendimiento por temporada del Data Lake.
perf = pd.read_csv(RAW_DIR / "player_performances.csv", low_memory=False,
                   usecols=["player_id", "season_name", "competition_id", "goals", "assists"])
perf = perf[(perf.season_name == "24/25") & perf.competition_id.isin(TOP5)] \
    .groupby("player_id")[["goals", "assists"]].sum()
app = pd.read_csv(RAW_DIR / "appearances.csv.gz", usecols=["player_id", "date", "competition_id", "goals", "assists"])
app = app[app.competition_id.isin(TOP5) & app.date.between("2024-08-01", "2025-07-31")] \
    .groupby("player_id")[["goals", "assists"]].sum()
j = app.join(perf, rsuffix="_lake", how="inner")
j = j[j.goals_lake.notna()]
goal_match = (j.goals == j.goals_lake).mean()
ast_match = (j.assists == j.assists_lake).mean()
check("Goles 24/25 top-5: por partido == Data Lake (≥97% jugadores)", goal_match >= 0.97,
      f"goles {goal_match:.1%}, asistencias {ast_match:.1%} sobre {len(j):,} jugadores")
worst = j.assign(diff=(j.goals - j.goals_lake).abs()).sort_values("diff", ascending=False).head(3)
print("      mayores diferencias de goles:", worst[["goals", "goals_lake"]].reset_index().to_dict("records"))

# 3. Integridad de tipos en MongoDB (lo que el validador garantiza, comprobado con $type).
bad_types = db.player_seasons.count_documents({"$or": [
    {"performance.goals": {"$not": {"$type": "number"}}},
    {"market_value_eur": {"$not": {"$type": "number"}}},
    {"age": {"$lt": 15}}, {"performance.ga": {"$lt": 0}},
]})
check("Tipos y rangos numéricos en player_seasons", bad_types == 0, f"{bad_types} documentos inválidos")
dupes = list(db.player_seasons.aggregate([
    {"$group": {"_id": {"p": "$player_id", "s": "$season"}, "n": {"$sum": 1}}}, {"$match": {"n": {"$gt": 1}}}]))
check("Sin duplicados (player_id, season)", not dupes, f"{len(dupes)} duplicados")

# 4. El validador rechaza documentos inválidos.
try:
    db.player_seasons.insert_one({"player_id": -5, "season": 2025, "name": "Test",
                                  "performance": {"goals": "muchos"}, "updated_at": datetime.now()})
    db.player_seasons.delete_one({"player_id": -5})
    check("El $jsonSchema rechaza un documento inválido", False, "se insertó")
except WriteError as e:
    check("El $jsonSchema rechaza un documento inválido", e.code == 121, f"código {e.code} DocumentValidationFailure")

# 5. Coherencia entre colecciones: G+A del perfil == suma de la TimeSeries.
for pid, label in ((937958, "Lamine Yamal"), (342229, "Kylian Mbappé")):
    prof = db.player_seasons.find_one({"player_id": pid, "season": 2025}, {"performance": 1, "titles": 1})
    ts = next(db.match_performances.aggregate([
        {"$match": {"meta.player_id": pid, "season": 2025}},
        {"$group": {"_id": None, "goals": {"$sum": "$goals"}, "assists": {"$sum": "$assists"}, "n": {"$sum": 1}}}]))
    p = prof["performance"]
    check(f"{label} 2025-26: perfil == TimeSeries",
          (p["goals"], p["assists"], p["apps"]) == (ts["goals"], ts["assists"], ts["n"]),
          f"G {p['goals']} A {p['assists']} PJ {p['apps']} · títulos: {[t['name'] for t in prof['titles']]}")

# 6. Títulos de control conocidos.
for pid, season, comp, label in ((288230, 2024, "CL", "Dembélé Champions 24/25"),
                                 (937958, 2024, "ES1", "Yamal LaLiga 24/25"),
                                 (357565, 2023, "GB1", "Rodri Premier 23/24"),
                                 (357565, 2023, "EURO", "Rodri EURO 2024")):
    d = db.player_seasons.find_one({"player_id": pid, "season": season, "titles.competition_id": comp})
    check(f"Título de control: {label}", d is not None)

print(f"\n{len(failures)} fallos" if failures else "\nTodas las comprobaciones superadas")
sys.exit(1 if failures else 0)
