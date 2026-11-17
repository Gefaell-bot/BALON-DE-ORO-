"""Exporta la colección principal a un único fichero importable en MongoDB.

Uso:  python -m scripts.export_dataset

Genera en data/export/:
  balon_de_oro.json  -> array JSON en formato Extended JSON (fechas como {"$date": ...}).
                        Compass: Import Data -> JSON.  CLI: mongoimport --jsonArray
  balon_de_oro.csv   -> misma información aplanada (campos anidados con notación punto,
                        títulos como texto). Compass: Import Data -> CSV.

Cada documento es un jugador-temporada (2012-13 a 2025-26) con perfil, club, liga,
rendimiento, Champions, selección, títulos, valor de mercado, podio real y predicción ML.
"""
import csv
import json

from bson import json_util
from pymongo import MongoClient

from config import DB_NAME, MONGO_URI, ROOT

OUT = ROOT / "data" / "export"
OUT.mkdir(parents=True, exist_ok=True)

db = MongoClient(MONGO_URI)[DB_NAME]
docs = list(db.player_seasons.find({}, {"_id": 0, "name_search": 0}).sort([("season", -1), ("ml.rank", 1)]))

json_path = OUT / "balon_de_oro.json"
json_path.write_text(json_util.dumps(docs, json_options=json_util.RELAXED_JSON_OPTIONS, ensure_ascii=False),
                     encoding="utf-8")


def flatten(d, prefix=""):
    row = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            row.update(flatten(v, key + "."))
        elif k == "titles":
            row[key] = "; ".join(t["name"] for t in v)
        elif isinstance(v, list):
            row[key] = "; ".join(map(str, v))
        elif hasattr(v, "isoformat"):
            row[key] = v.date().isoformat()
        else:
            row[key] = v
    return row


rows = [flatten(d) for d in docs]
fields = list(dict.fromkeys(k for r in rows for k in r))
csv_path = OUT / "balon_de_oro.csv"
with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

for p in (json_path, csv_path):
    print(f"{p}  {p.stat().st_size / 1e6:.1f} MB")
print(f"{len(docs):,} documentos · {len(fields)} columnas en el CSV")
