"""Feature engineering for the Home Credit data.

Two feature sets:
- VOICE features: things an applicant can tell a voice agent in a conversation
  (income, loan amount, job, family, existing loans). Used by the live system.
- FULL features: everything, including bureau scores and payment history.
  Used only as a benchmark to show how much the voice-only model gives up.

The big side tables are aggregated in chunks so this runs on a laptop with
little free RAM.
"""
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent
CHUNK = 1_000_000
EMPLOYED_SENTINEL = 365243  # dataset's code for "not employed" (mostly pensioners)

# Sensitive attributes we deliberately never use.
EXCLUDED = ["CODE_GENDER", "NAME_FAMILY_STATUS"]

VOICE_CATEGORICAL = [
    "NAME_CONTRACT_TYPE",
    "NAME_INCOME_TYPE",
    "NAME_EDUCATION_TYPE",
    "NAME_HOUSING_TYPE",
    "OCCUPATION_TYPE",
]
# No raw EMI or loan length: in this data credit/EMI mostly encodes the interest
# rate the lender priced in (its own risk view), which a voice agent can't know.
VOICE_NUMERIC = [
    "AMT_INCOME_TOTAL",
    "AMT_CREDIT",
    "AMT_GOODS_PRICE",
    "CNT_CHILDREN",
    "CNT_FAM_MEMBERS",
    "OWNS_CAR",
    "OWNS_REALTY",
    "AGE_YEARS",
    "YEARS_EMPLOYED",
    "NOT_EMPLOYED",
    "CREDIT_TO_INCOME",
    "ANNUITY_TO_INCOME",
    "CREDIT_TO_GOODS",
    "INCOME_PER_PERSON",
    "BUREAU_ACTIVE_LOANS",
    "BUREAU_ACTIVE_DEBT",
    "DEBT_TO_INCOME",
]
VOICE_FEATURES = VOICE_NUMERIC + VOICE_CATEGORICAL
# Risk may only go up as these go up (keeps explanations and loan suggestions sane).
VOICE_MONOTONE_UP = ["ANNUITY_TO_INCOME", "DEBT_TO_INCOME"]


def _downcast(df):
    for c in df.select_dtypes("float64").columns:
        df[c] = df[c].astype("float32")
    for c in df.select_dtypes("int64").columns:
        if c not in ("SK_ID_CURR", "SK_ID_PREV", "SK_ID_BUREAU"):
            df[c] = pd.to_numeric(df[c], downcast="integer")
    return df


def add_derived(df):
    """Ratios and human-friendly units. Works on the dataset or on one voice applicant."""
    df["AGE_YEARS"] = -df["DAYS_BIRTH"] / 365.25
    emp = df["DAYS_EMPLOYED"].replace(EMPLOYED_SENTINEL, np.nan)
    df["NOT_EMPLOYED"] = df["DAYS_EMPLOYED"].eq(EMPLOYED_SENTINEL).astype("int8")
    df["YEARS_EMPLOYED"] = -emp / 365.25
    df["OWNS_CAR"] = df["FLAG_OWN_CAR"].eq("Y").astype("int8")
    df["OWNS_REALTY"] = df["FLAG_OWN_REALTY"].eq("Y").astype("int8")
    df["CREDIT_TO_INCOME"] = df["AMT_CREDIT"] / df["AMT_INCOME_TOTAL"]
    df["ANNUITY_TO_INCOME"] = df["AMT_ANNUITY"] / df["AMT_INCOME_TOTAL"]
    df["CREDIT_TO_GOODS"] = df["AMT_CREDIT"] / df["AMT_GOODS_PRICE"]
    df["LOAN_TERM_MONTHS"] = df["AMT_CREDIT"] / df["AMT_ANNUITY"]
    df["INCOME_PER_PERSON"] = df["AMT_INCOME_TOTAL"] / df["CNT_FAM_MEMBERS"]
    return df


def add_debt_ratio(df):
    df["DEBT_TO_INCOME"] = df["BUREAU_ACTIVE_DEBT"] / df["AMT_INCOME_TOTAL"]
    return df


def load_applications():
    train = pd.read_csv(DATA_DIR / "application_train.csv")
    test = pd.read_csv(DATA_DIR / "application_test.csv")
    df = pd.concat([train, test], ignore_index=True)
    df = df.drop(columns=EXCLUDED)
    df = add_derived(_downcast(df).copy())
    # Extra full-model ratios
    df["EXT_MEAN"] = df[["EXT_SOURCE_1", "EXT_SOURCE_2", "EXT_SOURCE_3"]].mean(axis=1)
    df["EXT_PROD"] = df["EXT_SOURCE_1"] * df["EXT_SOURCE_2"] * df["EXT_SOURCE_3"]
    df["PHONE_CHANGE_TO_AGE"] = df["DAYS_LAST_PHONE_CHANGE"] / df["DAYS_BIRTH"]
    df["ID_PUBLISH_TO_AGE"] = df["DAYS_ID_PUBLISH"] / df["DAYS_BIRTH"]
    return df.copy()


def bureau_features():
    b = _downcast(pd.read_csv(DATA_DIR / "bureau.csv", usecols=[
        "SK_ID_CURR", "CREDIT_ACTIVE", "DAYS_CREDIT", "CREDIT_DAY_OVERDUE",
        "AMT_CREDIT_SUM", "AMT_CREDIT_SUM_DEBT", "AMT_CREDIT_SUM_OVERDUE",
        "AMT_CREDIT_MAX_OVERDUE", "CNT_CREDIT_PROLONG", "DAYS_CREDIT_ENDDATE",
    ]))
    active = b["CREDIT_ACTIVE"].eq("Active")
    b["ACTIVE"] = active.astype("int8")
    b["ACTIVE_DEBT"] = b["AMT_CREDIT_SUM_DEBT"].clip(lower=0).where(active, 0)
    g = b.groupby("SK_ID_CURR")
    out = pd.DataFrame({
        "BUREAU_LOANS": g.size(),
        "BUREAU_ACTIVE_LOANS": g["ACTIVE"].sum(),
        "BUREAU_ACTIVE_DEBT": g["ACTIVE_DEBT"].sum(),
        "BUREAU_CREDIT_SUM": g["AMT_CREDIT_SUM"].sum(),
        "BUREAU_OVERDUE_SUM": g["AMT_CREDIT_SUM_OVERDUE"].sum(),
        "BUREAU_MAX_OVERDUE": g["AMT_CREDIT_MAX_OVERDUE"].max(),
        "BUREAU_DAY_OVERDUE_MAX": g["CREDIT_DAY_OVERDUE"].max(),
        "BUREAU_PROLONG_SUM": g["CNT_CREDIT_PROLONG"].sum(),
        "BUREAU_DAYS_CREDIT_MEAN": g["DAYS_CREDIT"].mean(),
        "BUREAU_DAYS_CREDIT_MAX": g["DAYS_CREDIT"].max(),
        "BUREAU_ENDDATE_MAX": g["DAYS_CREDIT_ENDDATE"].max(),
    })
    out["BUREAU_DEBT_TO_CREDIT"] = out["BUREAU_ACTIVE_DEBT"] / out["BUREAU_CREDIT_SUM"].replace(0, np.nan)
    return out.astype("float32")


def previous_features():
    p = _downcast(pd.read_csv(DATA_DIR / "previous_application.csv", usecols=[
        "SK_ID_CURR", "NAME_CONTRACT_STATUS", "AMT_APPLICATION", "AMT_CREDIT",
        "AMT_ANNUITY", "CNT_PAYMENT", "DAYS_DECISION",
    ]))
    p["APPROVED"] = p["NAME_CONTRACT_STATUS"].eq("Approved").astype("int8")
    p["REFUSED"] = p["NAME_CONTRACT_STATUS"].eq("Refused").astype("int8")
    p["APP_TO_CREDIT"] = p["AMT_APPLICATION"] / p["AMT_CREDIT"].replace(0, np.nan)
    g = p.groupby("SK_ID_CURR")
    out = pd.DataFrame({
        "PREV_COUNT": g.size(),
        "PREV_APPROVED": g["APPROVED"].sum(),
        "PREV_REFUSED": g["REFUSED"].sum(),
        "PREV_APP_TO_CREDIT_MEAN": g["APP_TO_CREDIT"].mean(),
        "PREV_ANNUITY_MEAN": g["AMT_ANNUITY"].mean(),
        "PREV_CNT_PAYMENT_MEAN": g["CNT_PAYMENT"].mean(),
        "PREV_DAYS_DECISION_MAX": g["DAYS_DECISION"].max(),
    })
    out["PREV_REFUSED_RATE"] = out["PREV_REFUSED"] / out["PREV_COUNT"]
    return out.astype("float32")


def _chunked_agg(path, usecols, prepare, aggs):
    """Aggregate a large CSV chunk by chunk with mergeable stats (sum/count/max/min)."""
    parts = []
    for chunk in pd.read_csv(path, usecols=usecols, chunksize=CHUNK):
        chunk = prepare(_downcast(chunk))
        parts.append(chunk.groupby("SK_ID_CURR").agg(**aggs))
    merged = pd.concat(parts)
    final = {}
    for name, (_, how) in aggs.items():
        final[name] = {"sum": "sum", "count": "sum", "size": "sum", "max": "max", "min": "min"}[how]
    return merged.groupby(level=0).agg(final)


def installment_features():
    def prepare(c):
        c["DPD"] = (c["DAYS_ENTRY_PAYMENT"] - c["DAYS_INSTALMENT"]).clip(lower=0)
        c["LATE"] = (c["DPD"] > 0).astype("int8")
        c["UNDERPAID"] = (c["AMT_INSTALMENT"] - c["AMT_PAYMENT"]).clip(lower=0)
        c["RECENT"] = (c["DAYS_INSTALMENT"] > -365).astype("int8")
        c["RECENT_LATE"] = c["LATE"] * c["RECENT"]
        c["N"] = 1
        return c

    a = _chunked_agg(
        DATA_DIR / "installments_payments.csv",
        ["SK_ID_CURR", "DAYS_INSTALMENT", "DAYS_ENTRY_PAYMENT", "AMT_INSTALMENT", "AMT_PAYMENT"],
        prepare,
        {
            "INST_N": ("N", "sum"),
            "INST_LATE": ("LATE", "sum"),
            "INST_DPD_SUM": ("DPD", "sum"),
            "INST_DPD_MAX": ("DPD", "max"),
            "INST_UNDERPAID_SUM": ("UNDERPAID", "sum"),
            "INST_AMT_SUM": ("AMT_INSTALMENT", "sum"),
            "INST_RECENT_N": ("RECENT", "sum"),
            "INST_RECENT_LATE": ("RECENT_LATE", "sum"),
        },
    )
    out = pd.DataFrame(index=a.index)
    out["INST_COUNT"] = a["INST_N"]
    out["INST_LATE_RATE"] = a["INST_LATE"] / a["INST_N"]
    out["INST_DPD_MEAN"] = a["INST_DPD_SUM"] / a["INST_N"]
    out["INST_DPD_MAX"] = a["INST_DPD_MAX"]
    out["INST_UNDERPAID_RATE"] = a["INST_UNDERPAID_SUM"] / a["INST_AMT_SUM"].replace(0, np.nan)
    out["INST_RECENT_LATE_RATE"] = a["INST_RECENT_LATE"] / a["INST_RECENT_N"].replace(0, np.nan)
    return out.astype("float32")


def pos_features():
    def prepare(c):
        c["N"] = 1
        c["HAS_DPD"] = (c["SK_DPD"] > 0).astype("int8")
        return c

    a = _chunked_agg(
        DATA_DIR / "POS_CASH_balance.csv",
        ["SK_ID_CURR", "SK_DPD", "SK_DPD_DEF", "CNT_INSTALMENT_FUTURE"],
        prepare,
        {
            "POS_N": ("N", "sum"),
            "POS_DPD_MAX": ("SK_DPD", "max"),
            "POS_DPD_DEF_MAX": ("SK_DPD_DEF", "max"),
            "POS_HAS_DPD": ("HAS_DPD", "sum"),
            "POS_FUTURE_SUM": ("CNT_INSTALMENT_FUTURE", "sum"),
        },
    )
    out = pd.DataFrame(index=a.index)
    out["POS_MONTHS"] = a["POS_N"]
    out["POS_DPD_MAX"] = a["POS_DPD_MAX"]
    out["POS_DPD_DEF_MAX"] = a["POS_DPD_DEF_MAX"]
    out["POS_DPD_RATE"] = a["POS_HAS_DPD"] / a["POS_N"]
    out["POS_FUTURE_MEAN"] = a["POS_FUTURE_SUM"] / a["POS_N"]
    return out.astype("float32")


def credit_card_features():
    def prepare(c):
        c["N"] = 1
        c["HAS_DPD"] = (c["SK_DPD"] > 0).astype("int8")
        return c

    a = _chunked_agg(
        DATA_DIR / "credit_card_balance.csv",
        ["SK_ID_CURR", "AMT_BALANCE", "AMT_CREDIT_LIMIT_ACTUAL", "AMT_DRAWINGS_CURRENT", "SK_DPD"],
        prepare,
        {
            "CC_N": ("N", "sum"),
            "CC_BAL_SUM": ("AMT_BALANCE", "sum"),
            "CC_BAL_MAX": ("AMT_BALANCE", "max"),
            "CC_LIMIT_SUM": ("AMT_CREDIT_LIMIT_ACTUAL", "sum"),
            "CC_DRAW_SUM": ("AMT_DRAWINGS_CURRENT", "sum"),
            "CC_DPD_MAX": ("SK_DPD", "max"),
            "CC_HAS_DPD": ("HAS_DPD", "sum"),
        },
    )
    out = pd.DataFrame(index=a.index)
    out["CC_MONTHS"] = a["CC_N"]
    out["CC_UTILIZATION"] = a["CC_BAL_SUM"] / a["CC_LIMIT_SUM"].replace(0, np.nan)
    out["CC_BAL_MAX"] = a["CC_BAL_MAX"]
    out["CC_DRAW_MEAN"] = a["CC_DRAW_SUM"] / a["CC_N"]
    out["CC_DPD_MAX"] = a["CC_DPD_MAX"]
    out["CC_DPD_RATE"] = a["CC_HAS_DPD"] / a["CC_N"]
    return out.astype("float32")


def build_dataset(log=print):
    """Return (df, categorical_cols) with every application row and all features."""
    log("Loading applications...")
    df = load_applications()
    for name, fn in [
        ("bureau", bureau_features),
        ("previous applications", previous_features),
        ("installments", installment_features),
        ("POS cash", pos_features),
        ("credit cards", credit_card_features),
    ]:
        log(f"Aggregating {name}...")
        df = df.join(fn(), on="SK_ID_CURR")
    # Someone with no bureau record has no active loans and no debt.
    df["BUREAU_ACTIVE_LOANS"] = df["BUREAU_ACTIVE_LOANS"].fillna(0)
    df["BUREAU_ACTIVE_DEBT"] = df["BUREAU_ACTIVE_DEBT"].fillna(0)
    df = add_debt_ratio(df)

    cat_cols = [c for c in df.columns if df[c].dtype == object or str(df[c].dtype) in ("str", "string")]
    for c in cat_cols:
        df[c] = df[c].astype("category")
    return df, cat_cols
