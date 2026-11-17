"""Definición de colecciones: validadores $jsonSchema, colecciones TimeSeries e índices."""
from pymongo import ASCENDING as ASC, DESCENDING as DESC

INT = ["int", "long"]
NUM = ["int", "long", "double"]
POSITIONS = ["Attack", "Midfield", "Defender", "Goalkeeper"]


def non_neg(kind=INT, **extra):
    return {"bsonType": kind, "minimum": 0, **extra}


PLAYER_SEASON_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "title": "Temporada de un jugador candidato al Balón de Oro",
        "required": ["player_id", "season", "season_label", "name", "position", "age",
                     "club", "league", "performance", "title_points", "market_value_eur"],
        "properties": {
            "player_id": {"bsonType": INT, "minimum": 1, "description": "ID de Transfermarkt"},
            "season": {"bsonType": INT, "minimum": 2000, "maximum": 2100},
            "season_label": {"bsonType": "string", "pattern": "^[0-9]{4}-[0-9]{2}$"},
            "name": {"bsonType": "string", "minLength": 1},
            "name_search": {"bsonType": "string"},
            "image_url": {"bsonType": ["string", "null"]},
            "birth_date": {"bsonType": ["date", "null"]},
            "age": {"bsonType": NUM, "minimum": 15, "maximum": 50},
            "nationality": {"bsonType": ["string", "null"]},
            "citizenships": {"bsonType": "array", "items": {"bsonType": "string"}},
            "position": {"enum": POSITIONS},
            "sub_position": {"bsonType": ["string", "null"]},
            "foot": {"enum": ["left", "right", "both", None]},
            "height_cm": {"bsonType": ["int", "long", "double", "null"], "minimum": 140, "maximum": 220},
            "club": {"bsonType": "object", "required": ["id", "name"],
                     "properties": {"id": {"bsonType": INT}, "name": {"bsonType": "string"}}},
            "league": {"bsonType": "object", "required": ["id", "name"],
                       "properties": {"id": {"bsonType": "string"}, "name": {"bsonType": "string"},
                                      "country": {"bsonType": ["string", "null"]}}},
            "performance": {
                "bsonType": "object",
                "required": ["apps", "minutes", "goals", "assists", "ga"],
                "properties": {
                    "apps": non_neg(maximum=120), "minutes": non_neg(maximum=12000),
                    "goals": non_neg(maximum=150), "assists": non_neg(maximum=100),
                    "ga": non_neg(maximum=250), "goals_per90": non_neg(NUM, maximum=5),
                    "ga_per90": non_neg(NUM, maximum=6), "yellow": non_neg(), "red": non_neg(),
                    "top5_apps": non_neg(), "wins": non_neg(), "draws": non_neg(), "losses": non_neg(),
                    "win_rate": {"bsonType": NUM, "minimum": 0, "maximum": 1},
                },
            },
            "ucl": {"bsonType": "object", "properties": {
                "apps": non_neg(), "goals": non_neg(), "assists": non_neg(), "ga": non_neg()}},
            "national": {"bsonType": "object", "properties": {
                "apps": non_neg(), "goals": non_neg(), "assists": non_neg(),
                "caps": non_neg(), "career_goals": non_neg()}},
            "titles": {"bsonType": "array", "items": {
                "bsonType": "object", "required": ["competition_id", "name", "weight"],
                "properties": {"competition_id": {"bsonType": "string"}, "name": {"bsonType": "string"},
                               "weight": non_neg(NUM, maximum=10)}}},
            "title_points": non_neg(NUM, maximum=60),
            "market_value_eur": non_neg(NUM, maximum=500_000_000),
            "market_value_start_eur": non_neg(NUM, maximum=500_000_000),
            "ballon_dor_rank": {"bsonType": ["int", "null"], "minimum": 1, "maximum": 30},
            "ml": {"bsonType": ["object", "null"], "properties": {
                "probability": {"bsonType": "double", "minimum": 0, "maximum": 1},
                "raw_score": {"bsonType": "double", "minimum": 0, "maximum": 1},
                "rank": {"bsonType": INT, "minimum": 1}}},
            "updated_at": {"bsonType": "date"},
        },
    }
}

COLLECTIONS = {
    "player_seasons": {"validator": PLAYER_SEASON_SCHEMA, "validationLevel": "strict",
                       "validationAction": "error"},
    # TimeSeries: rendimiento partido a partido. metaField agrupa los buckets por jugador.
    # MongoDB no admite `validator` en colecciones TimeSeries (ni en create ni en collMod):
    # su integridad la garantiza el modelo Pydantic MatchIn del backend (mismos rangos).
    "match_performances": {"timeseries": {"timeField": "date", "metaField": "meta", "granularity": "hours"}},
    # TimeSeries: histórico de valor de mercado (una tasación cada pocos meses).
    "market_values": {"timeseries": {"timeField": "date", "metaField": "meta", "granularity": "hours"}},
    "ballon_dor_history": {},
    "competitions": {},
    "model_runs": {},
}

# (colección, claves, opciones). Los nombres se reutilizan en scripts/explain_indexes.py
INDEXES = [
    ("player_seasons", [("player_id", ASC), ("season", ASC)], {"name": "uniq_player_season", "unique": True}),
    ("player_seasons", [("performance.goals", DESC), ("performance.assists", DESC)], {"name": "goals_assists"}),
    ("player_seasons", [("market_value_eur", DESC)], {"name": "market_value"}),
    ("player_seasons", [("season", ASC), ("league.id", ASC), ("position", ASC), ("age", ASC)],
     {"name": "season_league_position_age"}),
    ("player_seasons", [("season", ASC), ("club.id", ASC)], {"name": "season_club"}),
    ("player_seasons", [("season", ASC), ("ml.probability", DESC)], {"name": "season_ml_probability"}),
    ("player_seasons", [("name_search", ASC)], {"name": "name_search"}),
    ("match_performances", [("meta.player_id", ASC), ("date", DESC)], {"name": "player_date"}),
    ("market_values", [("meta.player_id", ASC), ("date", ASC)], {"name": "player_date"}),
    ("ballon_dor_history", [("award_year", ASC), ("rank", ASC)], {"name": "year_rank", "unique": True}),
]


def create_collections(db, drop=True):
    for name, opts in COLLECTIONS.items():
        if drop:
            db.drop_collection(name)
        db.create_collection(name, **opts)


def ensure_indexes(db):
    for coll, keys, opts in INDEXES:
        db[coll].create_index(keys, **opts)
