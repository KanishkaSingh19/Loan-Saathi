"""Work answers become a category the caller hears back (shop owner / self-employed, salaried, driver, farmer,
daily-wage worker, ...); the caller's own words are kept as evidence, and the model still gets its own
categories (NAME_INCOME_TYPE / OCCUPATION_TYPE), unchanged for every keyword it knew before.

Run with `python -m pytest tests/test_work_category.py`.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("WEBHOOK_KEY", "wc-test-key")
os.environ.setdefault("DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
os.environ["DATABASE_URL"] = ""

import advisor  # noqa: E402
import converse as cv  # noqa: E402
import normalize as norm  # noqa: E402
from test_multifield import answer  # noqa: E402

META = __import__("json").loads((ROOT / "models" / "voice_model_meta.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("said, category", [
    ("kirana dukaan chalata hoon", "self_employed"),
    ("sabzi bechta hoon", "self_employed"),
    ("सब्ज़ी बेचता हूँ", "self_employed"),
    ("main auto chalata hoon", "driver"),
    ("I drive a taxi", "driver"),
    ("private company mein naukri", "salaried"),
    ("school mein teacher hoon", "salaried"),
    ("kheti karta hoon", "farmer"),
    ("मैं किसान हूँ", "farmer"),
    ("construction mazdoor", "daily_wage"),
    ("dihadi pe kaam", "daily_wage"),
    ("सरकारी नौकरी है", "government"),
    ("retired teacher hoon", "retired"),
    ("main ghar pe tiffin banati hoon", "other"),
])
def test_free_text_maps_to_a_category(said, category):
    assert norm.work_category(said) == category


def test_every_category_has_a_label_in_both_languages_and_blank_is_none():
    assert norm.work_category("") is None and norm.work_label("") == ""
    for key, (en, hi) in norm.WORK_CATEGORIES.items():
        assert en and hi, key
    assert norm.work_label("kheti karta hoon", "hi") == "Kisan"
    assert norm.work_label("tiffin service", "en") == "Other work (tiffin service)"  # unknown work keeps its words


def test_model_inputs_are_only_the_models_own_categories():
    income_types = set(META["categorical"]["NAME_INCOME_TYPE"])
    occupations = set(META["categorical"]["OCCUPATION_TYPE"])
    for keys, income_type, occupation, category in norm._WORK:
        assert income_type in income_types, keys
        assert occupation is None or occupation in occupations, keys
        assert category in norm.WORK_CATEGORIES, keys


def test_read_back_card_and_advisor_case_show_the_category_with_the_quote():
    s, _ = cv.start("en")
    cv._set(s, "work", "kirana dukaan", "kirana dukaan chalata hoon")
    assert s["work_category"] == "self_employed"
    assert cv.fmt("work", "kirana dukaan") == "Shop owner / self-employed"
    row = next(r for r in cv.summary(s) if r["field"] == "work")
    assert row["category"] == "self_employed" and row["said"] == "kirana dukaan chalata hoon"
    assert row["text"] == 'Shop owner / self-employed — "kirana dukaan"'
    case = advisor.build({}, answers=s["answers"])
    assert {r["label"]: r["text"] for r in case["applicant"]}["Work"] == 'Shop owner / self-employed — "kirana dukaan"'


def test_spoken_summary_reads_back_the_category():
    s, _ = cv.start("hi")
    cv._set(s, "monthly_income", 30000, "30 hazaar")
    cv._set(s, "work", "auto chalata hoon", "auto chalata hoon")
    assert "(Driver)" in cv._spoken_summary(s)


def test_devanagari_multi_field_answer_fills_work_and_years():
    t = answer("work", "सब्ज़ी बेचता हूँ, 10 साल से")
    assert "सब्ज़ी" in t["answers"].get("work", ""), t["answers"]
    assert abs(float(t["answers"]["years_in_work"]) - 10) < 0.5, t["answers"]
    assert t["asking"] != "years_in_work", t["reply"]
