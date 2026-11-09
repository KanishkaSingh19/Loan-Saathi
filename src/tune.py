"""Small hyperparameter search for the voice model.

    python -I -X utf8 -W ignore src/tune.py [--extra FEATURE,FEATURE]   (writes models/tuning.json)

Search on a tuning slice carved out of the training 80% (the 20% validation set is untouched), so the
chosen settings don't get credit for overfitting the number we report. A handful of combinations
around the current settings: on this data the algorithm and its knobs move AUC by thousandths, while
new information moves it by hundredths (see experiment_features.py), so a big grid isn't worth it.
"""
import itertools
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from experiment_features import CANDIDATES  # noqa: E402
from features import ROOT, VOICE_CATEGORICAL, VOICE_FEATURES, VOICE_MONOTONE_UP, build_dataset  # noqa: E402
from train import PARAMS, SEED  # noqa: E402

GRID = {"num_leaves": [16, 32, 64], "min_child_samples": [100, 300], "feature_fraction": [0.5, 0.8]}
OUT = ROOT / "models" / "tuning.json"
T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:5.0f}s] {m}", flush=True)


def main():
    extra = [f for f in (sys.argv[sys.argv.index("--extra") + 1].split(",") if "--extra" in sys.argv else []) if f]
    df, _ = build_dataset(log)
    train = df[df["TARGET"].notna()].copy()
    del df
    for name in extra:
        train[name] = CANDIDATES[name][1](train).astype("float32")
    feats = [f for f in VOICE_FEATURES if f in train.columns] + extra
    y = train["TARGET"].astype(int).to_numpy()
    idx_tr, idx_va = train_test_split(np.arange(len(train)), test_size=0.2, stratify=y, random_state=SEED)
    idx_fit, idx_tune = train_test_split(idx_tr, test_size=0.15, stratify=y[idx_tr], random_state=SEED)
    mono = [1 if (c in VOICE_MONOTONE_UP or CANDIDATES.get(c, (0, 0, 0))[2] == 1) else 0 for c in feats]
    X = train[feats]
    dfit = lgb.Dataset(X.iloc[idx_fit], y[idx_fit], categorical_feature=VOICE_CATEGORICAL, free_raw_data=False)
    dtune = lgb.Dataset(X.iloc[idx_tune], y[idx_tune], categorical_feature=VOICE_CATEGORICAL, free_raw_data=False)
    results = []
    for nl, mcs, ff in itertools.product(*GRID.values()):
        params = {**PARAMS, "num_leaves": nl, "min_child_samples": mcs, "feature_fraction": ff,
                  "monotone_constraints": mono, "monotone_constraints_method": "advanced"}
        m = lgb.train(params, dfit, num_boost_round=5000, valid_sets=[dtune],
                      callbacks=[lgb.early_stopping(200, verbose=False)])
        auc = roc_auc_score(y[idx_tune], m.predict(X.iloc[idx_tune], num_iteration=m.best_iteration))
        results.append({"num_leaves": nl, "min_child_samples": mcs, "feature_fraction": ff,
                        "tune_auc": round(float(auc), 4), "trees": m.best_iteration})
        log(f"leaves {nl:>2} min_child {mcs:>3} ff {ff}: tune AUC {auc:.4f} ({m.best_iteration} trees)")
    best = max(results, key=lambda r: r["tune_auc"])
    current = next(r for r in results if r["num_leaves"] == PARAMS["num_leaves"]
                   and r["min_child_samples"] == PARAMS["min_child_samples"]
                   and r["feature_fraction"] == PARAMS["feature_fraction"])
    OUT.write_text(json.dumps({"features": feats, "results": results, "best": best, "current": current}, indent=2))
    log(f"best {best}  vs current settings {current['tune_auc']}")


if __name__ == "__main__":
    main()
