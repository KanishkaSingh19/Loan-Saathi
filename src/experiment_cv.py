"""How much is the voice model really worth? 5-fold cross-validation and three questions, on Home Credit.

    python -I -X utf8 -W ignore src/experiment_cv.py      (~30-60 min; writes models/experiment_cv.json/.md)

1. 5-fold stratified CV of the current voice model, instead of one 80/20 split, so the AUC has a spread.
2. The same model without the loan-size features (loan amount, purchase price, loan/income, EMI/income,
   loan/price). In this data safer people were given bigger loans, so these partly encode the lender's
   own judgement; this shows what the model is worth on what the caller says about themselves.
3. Three extra questions a caller could answer, each added alone and then together:
   - employer type          ORGANIZATION_TYPE ("Do you work for government, a company, yourself...?")
   - PIN-code tier          REGION_RATING_CLIENT_W_CITY (Home Credit's 1-3 rating of where the client
                            lives; the closest the data has to a PIN-code tier)
   - years at the address   years since DAYS_REGISTRATION (when the client last changed registration;
                            the closest the data has to years at the current address)
All configurations use the same folds and settings. For speed the learning rate is 0.05 here (train.py
uses 0.03), so absolute AUCs differ slightly from models/metrics.json; compare configurations, not files.
Each fold stops early on a 10% slice of its own training part, never on the scored fold.
"""
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import ROOT, VOICE_CATEGORICAL, VOICE_FEATURES, VOICE_MONOTONE_UP, build_dataset  # noqa: E402
from train import PARAMS, SEED  # noqa: E402

LOAN_SIZE = ["AMT_CREDIT", "AMT_GOODS_PRICE", "CREDIT_TO_INCOME", "ANNUITY_TO_INCOME", "CREDIT_TO_GOODS"]
EXTRA = {
    "employer type": ("ORGANIZATION_TYPE", True),
    "PIN-code tier": ("REGION_RATING_CLIENT_W_CITY", False),
    "years at the address": ("YEARS_AT_REGISTRATION", False),
}
OUT_JSON = ROOT / "models" / "experiment_cv.json"
OUT_MD = ROOT / "models" / "experiment_cv.md"
T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:5.0f}s] {m}", flush=True)


def cv(train, y, folds, feats, cats):
    params = {**PARAMS, "learning_rate": 0.05,
              "monotone_constraints": [1 if c in VOICE_MONOTONE_UP else 0 for c in feats],
              "monotone_constraints_method": "advanced"}
    aucs, briers, trees = [], [], []
    for k, (tr, te) in enumerate(folds):
        fit, stop = train_test_split(tr, test_size=0.1, stratify=y[tr], random_state=SEED + k)
        X = train[feats]
        m = lgb.train(params, lgb.Dataset(X.iloc[fit], y[fit], categorical_feature=cats), num_boost_round=5000,
                      valid_sets=[lgb.Dataset(X.iloc[stop], y[stop], categorical_feature=cats)],
                      callbacks=[lgb.early_stopping(200, verbose=False)])
        p = m.predict(X.iloc[te], num_iteration=m.best_iteration)
        aucs.append(roc_auc_score(y[te], p))
        briers.append(brier_score_loss(y[te], p))
        trees.append(m.best_iteration)
    r = lambda v, d=4: round(float(v), d)  # noqa: E731
    return {"auc_mean": r(np.mean(aucs)), "auc_std": r(np.std(aucs)), "auc_folds": [r(a) for a in aucs],
            "brier_mean": r(np.mean(briers), 5), "brier_std": r(np.std(briers), 5), "trees_mean": int(np.mean(trees)),
            "features": len(feats)}


def main():
    df, cat_cols = build_dataset(log)
    df["YEARS_AT_REGISTRATION"] = (-df["DAYS_REGISTRATION"] / 365.25).astype("float32")
    train = df[df["TARGET"].notna()].reset_index(drop=True)
    y = train["TARGET"].astype(int).to_numpy()
    folds = list(StratifiedKFold(5, shuffle=True, random_state=SEED).split(np.zeros(len(y)), y))
    log(f"{len(y)} applications, default rate {y.mean():.3f}, 5 folds")

    configs = {"current voice model": (VOICE_FEATURES, VOICE_CATEGORICAL),
               "without loan-size features": ([f for f in VOICE_FEATURES if f not in LOAN_SIZE], VOICE_CATEGORICAL)}
    for name, (col, is_cat) in EXTRA.items():
        configs[f"+ {name}"] = (VOICE_FEATURES + [col], VOICE_CATEGORICAL + ([col] if is_cat else []))
    configs["+ all three"] = (VOICE_FEATURES + [c for c, _ in EXTRA.values()],
                              VOICE_CATEGORICAL + [c for c, cat in EXTRA.values() if cat])
    results = {}
    for name, (feats, cats) in configs.items():
        for c in cats:
            if str(train[c].dtype) != "category":
                train[c] = train[c].astype("category")
        results[name] = cv(train, y, folds, feats, cats)
        log(f"{name:<28} AUC {results[name]['auc_mean']:.4f} ± {results[name]['auc_std']:.4f}  "
            f"Brier {results[name]['brier_mean']:.5f}")
    base = results["current voice model"]["auc_mean"]
    for r in results.values():
        r["auc_change"] = round(r["auc_mean"] - base, 4)
    report = {"ts": time.time(), "folds": 5, "rows": int(len(y)), "learning_rate": 0.05,
              "loan_size_features": LOAN_SIZE,
              "extra_questions": {k: v[0] for k, v in EXTRA.items()}, "results": results,
              "note": ("Home Credit data, not Indian borrowers. The extra questions are proxies: region rating "
                       "for a PIN-code tier, years since registration change for years at the address.")}
    OUT_JSON.write_text(json.dumps(report, indent=1), encoding="utf-8")
    lines = ["| Configuration | AUC (5-fold mean ± sd) | Change | Brier |", "|---|---|---|---|"]
    for name, r in results.items():
        lines.append(f"| {name} | {r['auc_mean']:.4f} ± {r['auc_std']:.4f} | {r['auc_change']:+.4f} | "
                     f"{r['brier_mean']:.5f} |")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
