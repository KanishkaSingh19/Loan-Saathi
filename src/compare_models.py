"""Compare LightGBM, XGBoost and CatBoost on the same split and features.

Run:  python -I -X utf8 src/compare_models.py
Writes models/comparison.json and models/comparison.md.

Settings are matched as closely as the libraries allow: learning rate 0.05, early
stopping after 200 rounds, depth/leaves of similar size, L2 = 5, 50% feature sampling,
same monotone constraints on the voice feature set.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import catboost as cb
import lightgbm as lgb
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import train_test_split

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import DATA_DIR, VOICE_CATEGORICAL, VOICE_FEATURES, VOICE_MONOTONE_UP, build_dataset  # noqa: E402
from train import BANDS  # noqa: E402

MODEL_DIR = DATA_DIR / "models"
SEED, LR, PATIENCE, THREADS = 42, 0.05, 200, 6
T0 = time.time()


def log(msg):
    print(f"[{time.time() - T0:6.0f}s] {msg}", flush=True)


def bands(y, p):
    out = {}
    edges = [("low", 0, BANDS["low"]), ("medium", BANDS["low"], BANDS["medium"]), ("high", BANDS["medium"], 1.01)]
    for name, lo, hi in edges:
        m = (p >= lo) & (p < hi)
        out[name] = {"share": round(float(m.mean()), 3), "default_rate": round(float(y[m].mean()), 3) if m.any() else None}
    return out


def single_row_ms(predict, X, n=200):
    rows = [X.iloc[[i]] for i in range(n)]
    predict(rows[0])  # warm up
    t = time.perf_counter()
    for r in rows:
        predict(r)
    return (time.perf_counter() - t) / n * 1000


def file_mb(save):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "m"
        save(str(path))
        return path.stat().st_size / 1e6


def run_lightgbm(Xtr, ytr, Xva, yva, cats, mono):
    params = {"objective": "binary", "metric": "auc", "learning_rate": LR, "num_leaves": 32,
              "min_child_samples": 100, "feature_fraction": 0.5, "bagging_fraction": 0.8, "bagging_freq": 1,
              "lambda_l2": 5.0, "verbose": -1, "seed": SEED, "num_threads": THREADS}
    if mono:
        params.update(monotone_constraints=mono, monotone_constraints_method="advanced")
    t = time.time()
    m = lgb.train(params, lgb.Dataset(Xtr, ytr, categorical_feature=cats), 5000,
                  valid_sets=[lgb.Dataset(Xva, yva, categorical_feature=cats)],
                  callbacks=[lgb.early_stopping(PATIENCE, verbose=False)])
    fit_s = time.time() - t
    p = m.predict(Xva, num_iteration=m.best_iteration)
    ms = single_row_ms(lambda r: m.predict(r, num_iteration=m.best_iteration), Xva)
    mb = file_mb(lambda f: m.save_model(f, num_iteration=m.best_iteration))
    return p, fit_s, m.best_iteration, ms, mb


def run_xgboost(Xtr, ytr, Xva, yva, cats, mono):
    params = {"objective": "binary:logistic", "eval_metric": "auc", "eta": LR, "max_depth": 6,
              "min_child_weight": 5, "subsample": 0.8, "colsample_bytree": 0.5, "lambda": 5.0,
              "tree_method": "hist", "max_cat_to_onehot": 4, "nthread": THREADS, "seed": SEED}
    if mono:
        params["monotone_constraints"] = "(" + ",".join(map(str, mono)) + ")"
    dtr = xgb.DMatrix(Xtr, ytr, enable_categorical=True)
    dva = xgb.DMatrix(Xva, yva, enable_categorical=True)
    t = time.time()
    m = xgb.train(params, dtr, 5000, evals=[(dva, "valid")], early_stopping_rounds=PATIENCE, verbose_eval=False)
    fit_s = time.time() - t
    rng = (0, m.best_iteration + 1)
    p = m.predict(dva, iteration_range=rng)
    ms = single_row_ms(lambda r: m.predict(xgb.DMatrix(r, enable_categorical=True), iteration_range=rng), Xva)
    mb = file_mb(lambda f: m.save_model(f + ".ubj") or Path(f + ".ubj").rename(f))
    return p, fit_s, m.best_iteration + 1, ms, mb


def run_catboost(Xtr, ytr, Xva, yva, cats, mono):
    def prep(X):
        X = X.copy()
        for c in cats:
            col = X[c].astype(object)
            X[c] = col.where(col.notna(), "missing").map(str)
        return X

    Xtr, Xva = prep(Xtr), prep(Xva)
    params = dict(iterations=5000, learning_rate=LR, depth=6, l2_leaf_reg=5.0, eval_metric="AUC",
                  od_type="Iter", od_wait=PATIENCE, use_best_model=True, random_seed=SEED,
                  thread_count=THREADS, verbose=False, allow_writing_files=False)
    if mono:
        params["monotone_constraints"] = {c: v for c, v in zip(Xtr.columns, mono) if v}
    else:
        params["rsm"] = 0.5  # feature sampling; not combinable with monotone constraints on CPU
    m = cb.CatBoostClassifier(**params)
    t = time.time()
    m.fit(cb.Pool(Xtr, ytr, cat_features=cats), eval_set=cb.Pool(Xva, yva, cat_features=cats))
    fit_s = time.time() - t
    p = m.predict_proba(Xva)[:, 1]
    ms = single_row_ms(lambda r: m.predict_proba(r), Xva)
    mb = file_mb(lambda f: m.save_model(f))
    return p, fit_s, m.get_best_iteration() + 1, ms, mb


def compare(name, X, y, cats, mono=None):
    idx_tr, idx_va = train_test_split(np.arange(len(y)), test_size=0.2, stratify=y, random_state=SEED)
    Xtr, Xva, ytr, yva = X.iloc[idx_tr], X.iloc[idx_va], y[idx_tr], y[idx_va]
    results, preds = {}, {}
    for algo, fn in (("LightGBM", run_lightgbm), ("XGBoost", run_xgboost), ("CatBoost", run_catboost)):
        log(f"{name}: training {algo}...")
        p, fit_s, trees, ms, mb = fn(Xtr, ytr, Xva, yva, cats, mono)
        preds[algo] = p
        results[algo] = {
            "auc": round(roc_auc_score(yva, p), 4),
            "brier": round(brier_score_loss(yva, p), 5),
            "log_loss": round(log_loss(yva, p), 4),
            "train_seconds": round(fit_s, 1),
            "trees": int(trees),
            "predict_one_ms": round(ms, 2),
            "model_mb": round(mb, 2),
            "mean_pred": round(float(p.mean()), 4),
            "bands": bands(yva, p),
        }
        log(f"{name}: {algo} AUC {results[algo]['auc']} in {fit_s:.0f}s")
    blend = np.mean(list(preds.values()), axis=0)
    results["Blend (average)"] = {
        "auc": round(roc_auc_score(yva, blend), 4),
        "brier": round(brier_score_loss(yva, blend), 5),
        "log_loss": round(log_loss(yva, blend), 4),
        "mean_pred": round(float(blend.mean()), 4),
        "bands": bands(yva, blend),
    }
    corr = pd.DataFrame(preds).corr(method="spearman").round(3).to_dict()
    return {"n_features": X.shape[1], "valid_default_rate": round(float(yva.mean()), 4),
            "results": results, "rank_correlation": corr}


def to_markdown(out):
    lines = []
    for name, block in out.items():
        lines.append(f"**{name}** ({block['n_features']} features, validation default rate "
                     f"{block['valid_default_rate']:.1%})\n")
        lines.append("| Model | AUC | Brier | Log loss | Train time | Trees | Score 1 caller | Model size |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for algo, r in block["results"].items():
            if "train_seconds" in r:
                lines.append(f"| {algo} | {r['auc']:.4f} | {r['brier']:.4f} | {r['log_loss']:.4f} | "
                             f"{r['train_seconds']:.0f}s | {r['trees']} | {r['predict_one_ms']:.1f} ms | "
                             f"{r['model_mb']:.1f} MB |")
            else:
                lines.append(f"| {algo} | {r['auc']:.4f} | {r['brier']:.4f} | {r['log_loss']:.4f} | | | | |")
        lines.append("")
        lines.append("| Model | Low band: share / default rate | Medium | High |")
        lines.append("|---|---|---|---|")
        for algo, r in block["results"].items():
            b = r["bands"]
            cell = lambda k: f"{b[k]['share']:.0%} / {b[k]['default_rate']:.1%}" if b[k]["default_rate"] is not None else f"{b[k]['share']:.0%} / –"
            lines.append(f"| {algo} | {cell('low')} | {cell('medium')} | {cell('high')} |")
        lines.append("")
    return "\n".join(lines)


def main():
    df, cat_cols = build_dataset(log)
    train = df[df["TARGET"].notna()]
    del df
    y = train["TARGET"].astype(int).to_numpy()
    mono = [1 if c in VOICE_MONOTONE_UP else 0 for c in VOICE_FEATURES]

    out = {}
    out["Voice model"] = compare("voice", train[VOICE_FEATURES], y, VOICE_CATEGORICAL, mono)
    (MODEL_DIR / "comparison.json").write_text(json.dumps(out, indent=2))
    full_features = [c for c in train.columns if c not in ("SK_ID_CURR", "TARGET")]
    out["Full model"] = compare("full", train[full_features], y, cat_cols)
    (MODEL_DIR / "comparison.json").write_text(json.dumps(out, indent=2))
    md = to_markdown(out)
    (MODEL_DIR / "comparison.md").write_text(md, encoding="utf-8")
    print(md)
    log("Done.")


if __name__ == "__main__":
    main()
