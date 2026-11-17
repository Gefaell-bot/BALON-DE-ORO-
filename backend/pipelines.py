"""Pipelines de agregación de MongoDB usados por la API.

Puntuación Balón de Oro (0-100) = suma ponderada de 6 componentes normalizados en [0, 1]:

  ga        25%  goles + asistencias / máximo de la temporada
  titles    25%  puntos de títulos / máximo de la temporada
  market    15%  sqrt(valor de mercado / máximo)          (sqrt: comprime la cola de 200 M€)
  ucl       15%  G+A en Champions / máximo de la temporada
  ga90      10%  min(G+A por 90, 1.5) / 1.5              (tope fijo: evita premiar muestras cortas)
  win_rate  10%  % de victorias del equipo en sus partidos

Los máximos se calculan sobre TODA la temporada (no sobre el subconjunto filtrado), así la
puntuación de un jugador no cambia al aplicar filtros.
"""

WEIGHTS = {"ga": 0.25, "titles": 0.25, "market": 0.15, "ucl": 0.15, "ga90": 0.10, "win_rate": 0.10}
GA90_CAP = 1.5
TIERS = [  # límite inferior de $bucket -> etiqueta
    (55, "Favoritos"), (40, "Contendientes"), (25, "Aspirantes"), (0, "Resto"),
]
PROMISE_MAX_AGE = 21


def season_maxima_pipeline(season: int):
    return [
        {"$match": {"season": season}},
        {"$group": {
            "_id": None,
            "ga": {"$max": "$performance.ga"},
            "titles": {"$max": "$title_points"},
            "market": {"$max": "$market_value_eur"},
            "ucl": {"$max": "$ucl.ga"},
        }},
    ]


def _safe_ratio(field, maximum):
    return {"$cond": [{"$gt": [maximum, 0]}, {"$divide": [{"$ifNull": [field, 0]}, maximum]}, 0]}


def score_stages(maxima):
    """$addFields que calculan componentes, puntuación y categoría."""
    comp = {
        "ga": _safe_ratio("$performance.ga", maxima["ga"]),
        "titles": _safe_ratio("$title_points", maxima["titles"]),
        "market": {"$sqrt": _safe_ratio("$market_value_eur", maxima["market"])},
        "ucl": _safe_ratio("$ucl.ga", maxima["ucl"]),
        "ga90": {"$divide": [{"$min": ["$performance.ga_per90", GA90_CAP]}, GA90_CAP]},
        "win_rate": {"$ifNull": ["$performance.win_rate", 0]},
    }
    weighted = [{"$multiply": [w, f"$bdo.components.{k}"]} for k, w in WEIGHTS.items()]
    return [
        {"$addFields": {"bdo.components": comp}},
        {"$addFields": {"bdo.score": {"$round": [{"$multiply": [100, {"$add": weighted}]}, 1]}}},
        {"$addFields": {"bdo.tier": {"$switch": {
            "branches": [{"case": {"$gte": ["$bdo.score", lo]}, "then": label} for lo, label in TIERS[:-1]],
            "default": TIERS[-1][1]}}}},
    ]


CARD_PROJECTION = {
    "_id": 0, "player_id": 1, "season": 1, "name": 1, "image_url": 1, "age": 1, "nationality": 1,
    "position": 1, "sub_position": 1, "club": 1, "league": 1, "performance": 1, "ucl": 1,
    "titles": 1, "title_points": 1, "market_value_eur": 1, "ml": 1, "bdo": 1, "ballon_dor_rank": 1,
}


def build_match(season, league=None, position=None, club=None, age_min=None, age_max=None, player_ids=None):
    """$match con el orden de campos del índice season_league_position_age (igualdad -> rango)."""
    q = {"season": season}
    if league:
        q["league.id"] = league
    if position:
        q["position"] = position
    if club:
        q["club.id"] = club
    if age_min is not None or age_max is not None:
        q["age"] = {k: v for k, v in (("$gte", age_min), ("$lte", age_max)) if v is not None}
    if player_ids:
        q["player_id"] = {"$in": player_ids}
    return q


def ranking_pipeline(match, maxima, sort="score", limit=50):
    """Ranking + categorías en una sola consulta con $facet."""
    pipeline = [{"$match": match}]
    if sort == "ml":
        # Ordenar ANTES de calcular la puntuación permite usar el índice {season, ml.probability}.
        pipeline.append({"$sort": {"ml.probability": -1}})
    pipeline += score_stages(maxima)
    if sort != "ml":
        pipeline.append({"$sort": {"bdo.score": -1, "player_id": 1}})
    pipeline.append({"$facet": {
        "leaderboard": [{"$limit": limit}, {"$project": CARD_PROJECTION}],
        "tiers": [
            {"$bucket": {
                "groupBy": "$bdo.score",
                "boundaries": [lo for lo, _ in reversed(TIERS)] + [100.1],
                "default": "sin_puntuar",
                "output": {
                    "count": {"$sum": 1},
                    "avg_age": {"$avg": "$age"},
                    "top": {"$topN": {"n": 5, "sortBy": {"bdo.score": -1},
                                      "output": {"player_id": "$player_id", "name": "$name",
                                                 "score": "$bdo.score"}}},
                },
            }},
        ],
        "promesas": [
            {"$match": {"age": {"$lte": PROMISE_MAX_AGE}}},
            {"$sort": {"bdo.score": -1}}, {"$limit": 6},
            {"$project": {"_id": 0, "player_id": 1, "name": 1, "age": 1, "club": 1, "image_url": 1,
                          "bdo.score": 1, "ml.probability": 1, "performance.ga": 1}},
        ],
        "market_bands": [
            {"$bucketAuto": {"groupBy": "$market_value_eur", "buckets": 5,
                             "output": {"count": {"$sum": 1}, "avg_score": {"$avg": "$bdo.score"}}}},
        ],
        "summary": [
            {"$group": {"_id": None, "candidates": {"$sum": 1}, "avg_score": {"$avg": "$bdo.score"},
                        "goals": {"$sum": "$performance.goals"}, "assists": {"$sum": "$performance.assists"},
                        "max_score": {"$max": "$bdo.score"}}},
            {"$project": {"_id": 0}},
        ],
    }})
    return pipeline


def tier_label(lower_bound):
    return dict(TIERS).get(lower_bound, str(lower_bound))


def player_timeline_pipeline(player_id, season):
    """TimeSeries: forma partido a partido con ventanas deslizantes ($setWindowFields)."""
    return [
        {"$match": {"meta.player_id": player_id, "season": season}},
        {"$sort": {"date": 1}},
        {"$set": {"ga": {"$add": ["$goals", "$assists"]}}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "cum_goals": {"$sum": "$goals", "window": {"documents": ["unbounded", "current"]}},
                "cum_ga": {"$sum": "$ga", "window": {"documents": ["unbounded", "current"]}},
                "form5": {"$avg": "$ga", "window": {"documents": [-4, "current"]}},
            },
        }},
        {"$project": {"_id": 0, "date": 1, "competition_id": "$meta.competition_id", "club_name": 1,
                      "goals": 1, "assists": 1, "minutes": 1, "result": 1, "yellow": 1, "red": 1,
                      "cum_goals": 1, "cum_ga": 1, "form5": {"$round": ["$form5", 2]}, "source": 1}},
    ]


def monthly_ga_pipeline(player_ids, season):
    """TimeSeries agregada por mes ($dateTrunc) para el gráfico comparativo."""
    return [
        {"$match": {"meta.player_id": {"$in": player_ids}, "season": season}},
        {"$group": {"_id": {"player_id": "$meta.player_id",
                            "month": {"$dateTrunc": {"date": "$date", "unit": "month"}}},
                    "ga": {"$sum": {"$add": ["$goals", "$assists"]}}, "minutes": {"$sum": "$minutes"}}},
        {"$sort": {"_id.month": 1}},
        {"$setWindowFields": {"partitionBy": "$_id.player_id", "sortBy": {"_id.month": 1},
                              "output": {"cum_ga": {"$sum": "$ga", "window": {"documents": ["unbounded", "current"]}}}}},
        {"$project": {"_id": 0, "player_id": "$_id.player_id", "month": "$_id.month", "ga": 1, "cum_ga": 1}},
    ]


def recompute_after_match(m):
    """Update con pipeline: suma el partido y recalcula los derivados de forma atómica."""
    inc = lambda path, v: {"$add": [{"$ifNull": [f"${path}", 0]}, v]}  # noqa: E731
    win, draw, loss = (int(m.result == r) for r in ("W", "D", "L"))
    is_cl = m.competition_id == "CL"
    return [
        {"$set": {
            "performance.apps": inc("performance.apps", 1),
            "performance.minutes": inc("performance.minutes", m.minutes),
            "performance.goals": inc("performance.goals", m.goals),
            "performance.assists": inc("performance.assists", m.assists),
            "performance.yellow": inc("performance.yellow", m.yellow),
            "performance.red": inc("performance.red", m.red),
            "performance.wins": inc("performance.wins", win),
            "performance.draws": inc("performance.draws", draw),
            "performance.losses": inc("performance.losses", loss),
            "ucl.apps": inc("ucl.apps", int(is_cl)),
            "ucl.goals": inc("ucl.goals", m.goals if is_cl else 0),
            "ucl.assists": inc("ucl.assists", m.assists if is_cl else 0),
            "updated_at": "$$NOW",
        }},
        {"$set": {
            "performance.ga": {"$add": ["$performance.goals", "$performance.assists"]},
            "ucl.ga": {"$add": ["$ucl.goals", "$ucl.assists"]},
            "performance.goals_per90": {"$round": [{"$divide": [
                {"$multiply": ["$performance.goals", 90]}, {"$max": ["$performance.minutes", 1]}]}, 3]},
            "performance.ga_per90": {"$round": [{"$divide": [
                {"$multiply": [{"$add": ["$performance.goals", "$performance.assists"]}, 90]},
                {"$max": ["$performance.minutes", 1]}]}, 3]},
            "performance.win_rate": {"$round": [{"$divide": [
                "$performance.wins",
                {"$max": [{"$add": ["$performance.wins", "$performance.draws", "$performance.losses"]}, 1]}]}, 4]},
        }},
    ]
