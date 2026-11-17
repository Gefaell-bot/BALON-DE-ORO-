"""Entrena el modelo de probabilidad del Balón de Oro y escribe las predicciones en MongoDB.

Uso:  python -m ml.train

- Etiqueta: jugador en el podio real (ballon_dor_rank 1-3) de su temporada.
- Validación: Leave-One-Season-Out. Ninguna temporada participa en su propia evaluación.
- Probabilidad de ganar: softmax por temporada con temperatura calibrada sobre los ganadores reales.
- Las temporadas con premio ya entregado reciben su predicción *fuera de muestra* (LOSO);
  las demás (p. ej. 2025-26) reciben la del modelo final entrenado con todas.
"""
from datetime import datetime, timezone

import joblib
import numpy as np
from pymongo import MongoClient
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneGroupOut
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from config import DB_NAME, MODEL_PATH, MONGO_URI
from ml.features import FEATURES, load_frame
from ml.predict import write_predictions

CANDIDATES = {
    "logistic_regression": lambda: Pipeline([
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(C=0.3, max_iter=5000)),
    ]),
    "hist_gradient_boosting": lambda: HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=40,
        l2_regularization=1.0, class_weight="balanced", random_state=42),
}


def logit_scores(model, X):
    if hasattr(model, "decision_function"):
        return model.decision_function(X)
    p = np.clip(model.predict_proba(X)[:, 1], 1e-9, 1 - 1e-9)
    return np.log(p / (1 - p))


def fit_temperature(scores, seasons, winners):
    """Temperatura que maximiza la log-verosimilitud de los ganadores reales."""
    best_t, best_ll = 1.0, -np.inf
    for t in np.geomspace(0.05, 10, 120):
        ll = 0.0
        for s in np.unique(seasons[winners]):
            m = seasons == s
            z = scores[m] / t
            ll += z[winners[m]].sum() - (z.max() + np.log(np.exp(z - z.max()).sum()))
        if ll > best_ll:
            best_t, best_ll = t, ll
    return float(best_t), float(best_ll)


def evaluate(make_model, df):
    """LOSO: devuelve scores fuera de muestra y métricas por temporada."""
    X, y, groups = df[FEATURES].to_numpy(float), (df.ballon_dor_rank <= 3).to_numpy(), df.season.to_numpy()
    oof = np.zeros(len(df))
    for tr, te in LeaveOneGroupOut().split(X, y, groups):
        model = make_model().fit(X[tr], y[tr])
        oof[te] = logit_scores(model, X[te])
    per_season = []
    for s in sorted(np.unique(groups)):
        m = groups == s
        order = (-oof[m]).argsort().argsort() + 1  # rango 1 = mejor score
        ranks = df.ballon_dor_rank.to_numpy()[m]
        winner_rank = int(order[ranks == 1][0])
        podium_ranks = sorted(int(r) for r in order[ranks <= 3])
        per_season.append({"season": int(s), "winner_pred_rank": winner_rank, "podium_pred_ranks": podium_ranks,
                           "podium_in_top3": sum(r <= 3 for r in podium_ranks),
                           "podium_in_top10": sum(r <= 10 for r in podium_ranks)})
    w = np.array([p["winner_pred_rank"] for p in per_season])
    metrics = {
        "winner_mrr": float(np.mean(1 / w)),
        "winner_top1": float(np.mean(w == 1)), "winner_top3": float(np.mean(w <= 3)),
        "winner_top10": float(np.mean(w <= 10)),
        "podium_recall_top10": float(np.mean([p["podium_in_top10"] / 3 for p in per_season])),
    }
    return oof, metrics, per_season


def main():
    db = MongoClient(MONGO_URI)[DB_NAME]
    df = load_frame(db)
    labeled_seasons = set(db.ballon_dor_history.distinct("season"))
    lab = df[df.season.isin(labeled_seasons)].reset_index(drop=True)
    print(f"{len(df):,} filas · entrenamiento: {len(lab):,} filas en {len(labeled_seasons)} temporadas, "
          f"{int((lab.ballon_dor_rank <= 3).sum())} positivos")

    results = {}
    for name, make in CANDIDATES.items():
        oof, metrics, per_season = evaluate(make, lab)
        results[name] = (oof, metrics, per_season)
        print(f"\n{name}: " + ", ".join(f"{k}={v:.3f}" for k, v in metrics.items()))
        for p in per_season:
            print(f"   {p['season']}-{str(p['season'] + 1)[-2:]}: ganador real en puesto {p['winner_pred_rank']:>3} "
                  f"· podio real en puestos {p['podium_pred_ranks']}")

    best = max(results, key=lambda k: results[k][1]["winner_mrr"])
    oof, metrics, per_season = results[best]
    winners = (lab.ballon_dor_rank == 1).to_numpy()
    temperature, ll = fit_temperature(oof, lab.season.to_numpy(), winners)
    print(f"\nModelo elegido: {best} (MRR del ganador {metrics['winner_mrr']:.3f}) · temperatura {temperature:.3f}")

    final = CANDIDATES[best]().fit(lab[FEATURES].to_numpy(float), (lab.ballon_dor_rank <= 3).to_numpy())
    coefs = None
    if best == "logistic_regression":
        coefs = dict(sorted(zip(FEATURES, final.named_steps["clf"].coef_[0].round(4).tolist()),
                            key=lambda kv: -abs(kv[1])))
        print("Coeficientes (features estandarizadas):", coefs)

    version = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    bundle = {"model": final, "features": FEATURES, "temperature": temperature,
              "name": best, "version": version}
    MODEL_PATH.parent.mkdir(exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)

    # Puntuaciones: fuera de muestra en temporadas etiquetadas, modelo final en el resto.
    oof_by_key = dict(zip(zip(lab.player_id, lab.season), oof))
    scores = np.array([oof_by_key.get(k, np.nan) for k in zip(df.player_id, df.season)])
    unl = np.isnan(scores)
    scores[unl] = logit_scores(final, df.loc[unl, FEATURES].to_numpy(float))
    n = write_predictions(db, df, scores, bundle)
    print(f"Predicciones escritas en {n:,} documentos")

    db.model_runs.insert_one({
        "version": version, "created_at": datetime.now(timezone.utc), "model": best,
        "features": FEATURES, "temperature": temperature, "log_likelihood": ll,
        "metrics": {k: v[1] for k, v in results.items()}, "per_season": per_season,
        "coefficients": coefs, "train_rows": len(lab), "positives": int((lab.ballon_dor_rank <= 3).sum()),
    })

    for s in sorted(df.season.unique())[-2:]:
        top = db.player_seasons.find({"season": int(s)}, {"name": 1, "ml": 1, "club.name": 1}) \
                               .sort("ml.probability", -1).limit(8)
        print(f"\nTop 8 {s}-{str(s + 1)[-2:]}:")
        for d in top:
            print(f"   {d['ml']['rank']:>2}. {d['name']:<24} {d['club']['name']:<28} {d['ml']['probability']:.1%}")


if __name__ == "__main__":
    main()
