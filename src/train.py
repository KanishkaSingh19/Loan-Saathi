"""Train the voice-only model (used live) and the full benchmark model.

Run:  python -I src/train.py
Writes models/ (LightGBM models + metadata) and submission.csv (Kaggle, full model).
"""
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import ROOT, VOICE_CATEGORICAL, VOICE_FEATURES, VOICE_MONOTONE_UP, build_dataset  # noqa: E402

MODEL_DIR = ROOT / "models"
SEED = 42
PARAMS = {
    "objective": "binary",
    "metric": "auc",
    "learning_rate": 0.03,
    "num_leaves": 32,
    "min_child_samples": 100,
    "feature_fraction": 0.5,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "verbose": -1,
    "seed": SEED,
    "num_threads": 6,
}
# Risk bands used by the voice agent. Base default rate is about 8%.
BANDS = {"low": 0.05, "medium": 0.12}  # < low -> low, < medium -> medium, else high

T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.0f}s] {msg}", flush=True)


def fit(X_tr, y_tr, X_va, y_va, cats, name, params=PARAMS):
    log(f"Training {name} model on {X_tr.shape[1]} features...")
    model = lgb.train(
        params,
        lgb.Dataset(X_tr, y_tr, categorical_feature=cats),
        num_boost_round=5000,
        valid_sets=[lgb.Dataset(X_va, y_va, categorical_feature=cats)],
        callbacks=[lgb.early_stopping(200, verbose=False), lgb.log_evaluation(500)],
    )
    p = model.predict(X_va, num_iteration=model.best_iteration)
    auc = roc_auc_score(y_va, p)
    log(f"{name}: validation AUC {auc:.4f} at {model.best_iteration} trees")
    return model, p, auc


def band_report(y, p):
    edges = [0, BANDS["low"], BANDS["medium"], 1.0001]
    labels = ["low", "medium", "high"]
    band = pd.cut(p, edges, labels=labels, right=False)
    rep = pd.DataFrame({"y": y, "band": band}).groupby("band", observed=False)["y"].agg(["size", "mean"])
    return {
        b: {"share": round(float(rep.loc[b, "size"] / len(y)), 3), "default_rate": round(float(rep.loc[b, "mean"]), 3)}
        for b in labels
    }


def calibration_report(y, p, bins=10):
    """Expected calibration error and per-decile predicted vs actual default rates."""
    order = np.argsort(p)
    rows, ece = [], 0.0
    for chunk in np.array_split(order, bins):
        pred, real = float(p[chunk].mean()), float(y[chunk].mean())
        ece += len(chunk) / len(p) * abs(pred - real)
        rows.append({"predicted": round(pred, 4), "actual": round(real, 4)})
    return {"brier": round(float(brier_score_loss(y, p)), 5), "ece": round(ece, 5), "deciles": rows}


def fit_calibration(y, p, points=200):
    """Isotonic map raw -> calibrated probability, stored as a small piecewise-linear table."""
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(p, y)
    xs = np.quantile(p, np.linspace(0, 1, points))
    xs = np.unique(np.concatenate([[0.0], xs, [1.0]]))
    return {"x": [round(float(v), 6) for v in xs], "y": [round(float(v), 6) for v in iso.predict(xs)]}


def apply_calibration(cal, p):
    return np.interp(p, cal["x"], cal["y"])


def importance(model, n=15):
    imp = pd.Series(model.feature_importance("gain"), index=model.feature_name())
    return {k: round(float(v), 1) for k, v in imp.sort_values(ascending=False).head(n).items()}


def main(voice_only=False):
    MODEL_DIR.mkdir(exist_ok=True)
    df, cat_cols = build_dataset(log)
    is_train = df["TARGET"].notna()
    train, test = df[is_train], df[~is_train]
    y = train["TARGET"].astype(int).to_numpy()
    log(f"Train {train.shape}, test {test.shape}, default rate {y.mean():.3f}")

    idx_tr, idx_va = train_test_split(np.arange(len(train)), test_size=0.2, stratify=y, random_state=SEED)
    # A separate calibration slice (from the training 80%): early stopping and the isotonic fit use it,
    # so the 20% validation set stays untouched for the reported numbers.
    idx_fit, idx_cal = train_test_split(idx_tr, test_size=0.125, stratify=y[idx_tr], random_state=SEED)
    metrics_path = MODEL_DIR / "metrics.json"
    previous = json.loads(metrics_path.read_text()).get("voice") if metrics_path.exists() else None
    metrics = {"n_train": int(len(idx_fit)), "n_calibration": int(len(idx_cal)), "n_valid": int(len(idx_va)),
               "default_rate": round(float(y.mean()), 4)}

    # ---- Voice-only model: what the live agent uses ----
    Xv = train[VOICE_FEATURES]
    voice_params = {
        **PARAMS,
        "monotone_constraints": [1 if c in VOICE_MONOTONE_UP else 0 for c in VOICE_FEATURES],
        "monotone_constraints_method": "advanced",
    }
    voice, p_cal_raw, _ = fit(Xv.iloc[idx_fit], y[idx_fit], Xv.iloc[idx_cal], y[idx_cal], VOICE_CATEGORICAL,
                              "voice-only", voice_params)
    calibration = fit_calibration(y[idx_cal], p_cal_raw)
    pv_raw = voice.predict(Xv.iloc[idx_va], num_iteration=voice.best_iteration)
    pv = apply_calibration(calibration, pv_raw)
    raw_rep, cal_rep = calibration_report(y[idx_va], pv_raw), calibration_report(y[idx_va], pv)
    if not (cal_rep["brier"] < raw_rep["brier"] and cal_rep["ece"] < raw_rep["ece"]):
        # LightGBM's log-loss output is often calibrated already; only keep the map if it helps.
        log(f"Calibration not kept: ECE {raw_rep['ece']} raw vs {cal_rep['ece']} calibrated")
        calibration, pv = None, pv_raw
    auc_v = roc_auc_score(y[idx_va], pv)
    log(f"voice-only: held-out AUC {auc_v:.4f} (calibrated), Brier {brier_score_loss(y[idx_va], pv):.5f}")
    voice.save_model(str(MODEL_DIR / "voice_model.txt"), num_iteration=voice.best_iteration)
    meta = {
        "features": VOICE_FEATURES,
        "categorical": {c: [str(v) for v in train[c].cat.categories] for c in VOICE_CATEGORICAL},
        "bands": BANDS,
        "validation_auc": round(auc_v, 4),
        "monotone_up": VOICE_MONOTONE_UP,
        "calibration": calibration,  # applied by scoring.score() to every prediction
        # Typical values, used to fill gaps and to phrase explanations.
        "medians": {c: float(train[c].median()) for c in VOICE_FEATURES if c not in VOICE_CATEGORICAL},
        "modes": {c: str(train[c].mode().iloc[0]) for c in VOICE_CATEGORICAL},
    }
    (MODEL_DIR / "voice_model_meta.json").write_text(json.dumps(meta, indent=2))
    metrics["voice"] = {
        "auc": round(auc_v, 4),
        "trees": voice.best_iteration,
        "monotone_up": VOICE_MONOTONE_UP,
        "calibration_raw": raw_rep,
        "calibration_kept": calibration is not None,
        "calibration": calibration_report(y[idx_va], pv),  # what scoring will actually produce
        "bands": band_report(y[idx_va], pv),
        "top_features": importance(voice),
    }
    if previous:
        metrics["voice"]["previous_model"] = {k: previous.get(k) for k in ("auc", "trees", "bands")}
        log(f"AUC before {previous.get('auc')} -> after {round(auc_v, 4)}")

    if voice_only:
        old = json.loads(metrics_path.read_text()) if metrics_path.exists() else {}
        metrics_path.write_text(json.dumps({**old, **metrics}, indent=2))
        log("Done (voice model only).")
        print(json.dumps(metrics["voice"], indent=2))
        return

    # ---- Full benchmark model ----
    full_features = [c for c in train.columns if c not in ("SK_ID_CURR", "TARGET")]
    Xf = train[full_features]
    full, pf, auc_f = fit(Xf.iloc[idx_tr], y[idx_tr], Xf.iloc[idx_va], y[idx_va], cat_cols, "full")
    metrics["full"] = {
        "auc": round(auc_f, 4),
        "trees": full.best_iteration,
        "n_features": len(full_features),
        "bands": band_report(y[idx_va], pf),
        "top_features": importance(full),
    }

    # Refit full model on all training rows for the Kaggle submission.
    n_rounds = int(full.best_iteration * 1.1)
    log(f"Refitting full model on all rows ({n_rounds} trees) for submission...")
    final = lgb.train(PARAMS, lgb.Dataset(Xf, y, categorical_feature=cat_cols), num_boost_round=n_rounds)
    final.save_model(str(MODEL_DIR / "full_model.txt"))
    sub = pd.DataFrame({"SK_ID_CURR": test["SK_ID_CURR"].astype(int), "TARGET": final.predict(test[full_features])})
    sub.to_csv(ROOT / "submission.csv", index=False)

    metrics_path.write_text(json.dumps(metrics, indent=2))
    log("Done.")
    print(json.dumps({k: metrics[k] for k in ("voice", "full")}, indent=2))


if __name__ == "__main__":
    main(voice_only="--voice-only" in sys.argv)
