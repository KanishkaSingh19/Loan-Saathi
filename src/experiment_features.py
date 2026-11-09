"""Which questions would make the voice model more accurate? An ablation on the Home Credit data.

    python -I -X utf8 -W ignore src/experiment_features.py      (~10 min; writes models/experiment_features.json)

Every candidate is something a caller can answer about themselves in a few words (yes/no or a small
count), built from the side tables the full benchmark model already uses. Each is added to the current
voice features alone and then all together, on the same 80/20 split and settings as src/train.py, so the
AUC differences are comparable. Answers on a real call are self-reported (people may forget or shade the
truth), so real-world gains will be smaller than these dataset gains.
"""
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import ROOT, VOICE_CATEGORICAL, VOICE_FEATURES, VOICE_MONOTONE_UP, build_dataset  # noqa: E402
from train import PARAMS, SEED  # noqa: E402

# feature -> (question, how to build it from the full dataset, monotone direction)
CANDIDATES = {
    "PAST_LOANS": ("Including closed ones, how many loans have you ever taken?",
                   lambda d: d["BUREAU_LOANS"].fillna(0).clip(upper=20), 0),
    "EVER_REFUSED": ("Has a loan application of yours ever been refused?",
                     lambda d: (d["PREV_REFUSED"].fillna(0) > 0).astype("float32"), 1),
    "EVER_LATE": ("Have you ever paid an EMI late?",
                  lambda d: (d["INST_LATE_RATE"].fillna(0) > 0).astype("float32"), 1),
    "LATE_LAST_YEAR": ("In the last 12 months, did you pay any EMI late?",
                       lambda d: (d["INST_RECENT_LATE_RATE"].fillna(0) > 0).astype("float32"), 1),
    "LATE_OVER_MONTH": ("Have you ever been more than a month late on an EMI?",
                        lambda d: (d["INST_DPD_MAX"].fillna(0) > 30).astype("float32"), 1),
    "OVERDUE_NOW": ("Is any EMI overdue right now?",
                    lambda d: ((d["BUREAU_DAY_OVERDUE_MAX"].fillna(0) > 0)
                               | (d["BUREAU_OVERDUE_SUM"].fillna(0) > 0)).astype("float32"), 1),
}
OUT = ROOT / "models" / "experiment_features.json"
T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:5.0f}s] {m}", flush=True)


def run(train, y, idx_tr, idx_va, feats, monotone):
    params = {**PARAMS, "monotone_constraints": [monotone.get(c, 1 if c in VOICE_MONOTONE_UP else 0) for c in feats],
              "monotone_constraints_method": "advanced"}
    X = train[feats]
    m = lgb.train(params, lgb.Dataset(X.iloc[idx_tr], y[idx_tr], categorical_feature=VOICE_CATEGORICAL),
                  num_boost_round=5000,
                  valid_sets=[lgb.Dataset(X.iloc[idx_va], y[idx_va], categorical_feature=VOICE_CATEGORICAL)],
                  callbacks=[lgb.early_stopping(200, verbose=False)])
    p = m.predict(X.iloc[idx_va], num_iteration=m.best_iteration)
    return round(float(roc_auc_score(y[idx_va], p)), 4), m.best_iteration


def main():
    df, _ = build_dataset(log)
    train = df[df["TARGET"].notna()].copy()
    del df
    for name, (_, build, _) in CANDIDATES.items():
        train[name] = build(train).astype("float32")
    y = train["TARGET"].astype(int).to_numpy()
    idx_tr, idx_va = train_test_split(np.arange(len(train)), test_size=0.2, stratify=y, random_state=SEED)
    monotone = {k: v[2] for k, v in CANDIDATES.items()}

    results = {"questions": {k: v[0] for k, v in CANDIDATES.items()},
               "prevalence": {k: round(float(train[k].mean()), 3) for k in CANDIDATES if k != "PAST_LOANS"},
               "default_rate_if_yes": {k: round(float(y[train[k].to_numpy() > 0].mean()), 3) for k in CANDIDATES
                                       if k != "PAST_LOANS"}}
    base, trees = run(train, y, idx_tr, idx_va, VOICE_FEATURES, monotone)
    log(f"baseline (current voice features): AUC {base}")
    results["baseline"] = base
    results["single"] = {}
    for name in CANDIDATES:
        auc, _ = run(train, y, idx_tr, idx_va, VOICE_FEATURES + [name], monotone)
        results["single"][name] = {"auc": auc, "gain": round(auc - base, 4)}
        log(f"+ {name:<16} AUC {auc}  ({auc - base:+.4f})")
    every = VOICE_FEATURES + list(CANDIDATES)
    auc_all, _ = run(train, y, idx_tr, idx_va, every, monotone)
    results["all"] = {"auc": auc_all, "gain": round(auc_all - base, 4)}
    log(f"+ all six             AUC {auc_all}  ({auc_all - base:+.4f})")
    # A short list: the three questions with the biggest single gains.
    top3 = sorted(CANDIDATES, key=lambda k: results["single"][k]["gain"], reverse=True)[:3]
    auc3, _ = run(train, y, idx_tr, idx_va, VOICE_FEATURES + top3, monotone)
    results["top3"] = {"features": top3, "auc": auc3, "gain": round(auc3 - base, 4)}
    log(f"+ top three {top3}: AUC {auc3}  ({auc3 - base:+.4f})")
    OUT.write_text(json.dumps(results, indent=2))
    log(f"written {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
