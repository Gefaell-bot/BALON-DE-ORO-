"""Construcción de features a partir de los documentos `player_seasons` de MongoDB."""
import numpy as np
import pandas as pd

TOP5 = {"GB1", "ES1", "IT1", "L1", "FR1"}
INTL_TITLES = {"FIWC", "EURO", "COPA"}
POSITIONS = ["Attack", "Midfield", "Defender", "Goalkeeper"]

PROJECTION = {
    "_id": 0, "player_id": 1, "season": 1, "name": 1, "age": 1, "position": 1, "league.id": 1,
    "performance": 1, "ucl": 1, "titles.competition_id": 1, "title_points": 1,
    "market_value_eur": 1, "ballon_dor_rank": 1,
}

FEATURES = [
    "goals", "assists", "ga_per90", "minutes", "cl_ga", "cl_apps", "title_points",
    "won_cl", "won_top5_league", "won_intl", "win_rate", "log_mv", "age", "is_top5",
    "ga_pct", "mv_pct", "titles_pct",
] + [f"pos_{p}" for p in POSITIONS]


def load_frame(db, query=None) -> pd.DataFrame:
    docs = list(db.player_seasons.find(query or {}, PROJECTION))
    return to_frame(docs)


def to_frame(docs) -> pd.DataFrame:
    rows = []
    for d in docs:
        p, u = d["performance"], d.get("ucl") or {}
        won = {t["competition_id"] for t in d.get("titles", [])}
        rows.append({
            "player_id": d["player_id"], "season": d["season"], "name": d["name"],
            "ballon_dor_rank": d.get("ballon_dor_rank"),
            "goals": p["goals"], "assists": p["assists"], "ga": p["ga"],
            "ga_per90": p.get("ga_per90", 0.0), "minutes": p["minutes"], "win_rate": p.get("win_rate", 0.0),
            "cl_ga": u.get("ga", 0), "cl_apps": u.get("apps", 0),
            "title_points": d["title_points"], "won_cl": int("CL" in won),
            "won_top5_league": int(bool(won & TOP5)), "won_intl": int(bool(won & INTL_TITLES)),
            "log_mv": np.log1p(d["market_value_eur"]), "age": d["age"],
            "is_top5": int(d["league"]["id"] in TOP5), "position": d["position"],
        })
    df = pd.DataFrame(rows)
    # Rankings relativos dentro de la temporada: comparan contra los rivales del mismo año.
    g = df.groupby("season")
    df["ga_pct"] = g.ga.rank(pct=True)
    df["mv_pct"] = g.log_mv.rank(pct=True)
    df["titles_pct"] = g.title_points.rank(pct=True)
    for p in POSITIONS:
        df[f"pos_{p}"] = (df.position == p).astype(int)
    return df


def season_softmax(scores: np.ndarray, seasons: np.ndarray, temperature: float) -> np.ndarray:
    """P(ganar) = softmax(score / T) dentro de cada temporada: un único ganador por año."""
    out = np.empty_like(scores, dtype=float)
    for s in np.unique(seasons):
        m = seasons == s
        z = scores[m] / temperature
        e = np.exp(z - z.max())
        out[m] = e / e.sum()
    return out
