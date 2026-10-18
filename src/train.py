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
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import DATA_DIR, VOICE_CATEGORICAL, VOICE_FEATURES, VOICE_MONOTONE_UP, build_dataset  # noqa: E402

MODEL_DIR = DATA_DIR / "models"
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
    metrics = {"n_train": int(len(idx_tr)), "n_valid": int(len(idx_va)), "default_rate": round(float(y.mean()), 4)}

    # ---- Voice-only model: what the live agent uses ----
    Xv = train[VOICE_FEATURES]
    voice_params = {
        **PARAMS,
        "monotone_constraints": [1 if c in VOICE_MONOTONE_UP else 0 for c in VOICE_FEATURES],
        "monotone_constraints_method": "advanced",
    }
    voice, pv, auc_v = fit(Xv.iloc[idx_tr], y[idx_tr], Xv.iloc[idx_va], y[idx_va], VOICE_CATEGORICAL,
                           "voice-only", voice_params)
    voice.save_model(str(MODEL_DIR / "voice_model.txt"), num_iteration=voice.best_iteration)
    meta = {
        "features": VOICE_FEATURES,
        "categorical": {c: [str(v) for v in train[c].cat.categories] for c in VOICE_CATEGORICAL},
        "bands": BANDS,
        "validation_auc": round(auc_v, 4),
        # Typical values, used to fill gaps and to phrase explanations.
        "medians": {c: float(train[c].median()) for c in VOICE_FEATURES if c not in VOICE_CATEGORICAL},
        "modes": {c: str(train[c].mode().iloc[0]) for c in VOICE_CATEGORICAL},
    }
    (MODEL_DIR / "voice_model_meta.json").write_text(json.dumps(meta, indent=2))
    metrics["voice"] = {
        "auc": round(auc_v, 4),
        "trees": voice.best_iteration,
        "bands": band_report(y[idx_va], pv),
        "top_features": importance(voice),
    }

    metrics_path = MODEL_DIR / "metrics.json"
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
    sub.to_csv(DATA_DIR / "submission.csv", index=False)

    metrics_path.write_text(json.dumps(metrics, indent=2))
    log("Done.")
    print(json.dumps({k: metrics[k] for k in ("voice", "full")}, indent=2))


if __name__ == "__main__":
    main(voice_only="--voice-only" in sys.argv)
