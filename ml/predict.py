"""Aplica el modelo guardado y escribe `ml.*` en player_seasons (lo usa también el backend)."""
import joblib
import numpy as np
from pymongo import UpdateOne

from config import MODEL_PATH
from ml.features import load_frame, season_softmax


def load_bundle():
    return joblib.load(MODEL_PATH) if MODEL_PATH.exists() else None


def write_predictions(db, df, scores, bundle):
    prob = season_softmax(scores, df.season.to_numpy(), bundle["temperature"])
    podium = 1 / (1 + np.exp(-scores))
    df = df.assign(prob=prob, podium=podium)
    df["rank"] = df.groupby("season").prob.rank(ascending=False, method="first").astype(int)
    ops = [UpdateOne({"player_id": int(r.player_id), "season": int(r.season)},
                     {"$set": {"ml": {"probability": float(r.prob), "raw_score": float(r.podium),
                                      "rank": int(r.rank), "model": bundle["name"],
                                      "version": bundle["version"]}}})
           for r in df.itertuples()]
    for i in range(0, len(ops), 5000):
        db.player_seasons.bulk_write(ops[i:i + 5000], ordered=False)
    return len(ops)


def rescore_season(db, season: int, bundle=None):
    """Recalcula la probabilidad de toda una temporada (tras insertar/editar estadísticas)."""
    bundle = bundle or load_bundle()
    if bundle is None:
        return 0
    df = load_frame(db, {"season": season})
    model = bundle["model"]
    X = df[bundle["features"]].to_numpy(float)
    if hasattr(model, "decision_function"):
        scores = model.decision_function(X)
    else:
        p = np.clip(model.predict_proba(X)[:, 1], 1e-9, 1 - 1e-9)
        scores = np.log(p / (1 - p))
    return write_predictions(db, df, scores, bundle)
