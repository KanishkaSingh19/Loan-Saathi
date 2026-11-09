"""Spoken values -> numbers: the table of cases the voice path depends on.

    python -m pytest tests/test_normalize.py -q

The phone agent (Agent Builder) and the web call's no-Gemini fallback feed raw speech straight into
normalize.py, so every phrase here is something a caller (or speech-to-text) really produces.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import normalize as norm  # noqa: E402

AMOUNTS = [
    # digits, Indian grouping, symbols
    ("40000", 40000), ("40,000", 40000), ("₹40,000", 40000), ("Rs. 40,000", 40000), ("₹1,20,000", 120000),
    ("3,00,000", 300000), ("1.5 lakh", 150000), ("2.5 lakh", 250000), ("40k", 40000), ("25k", 25000),
    # English words
    ("forty thousand", 40000), ("forty five thousand", 45000), ("four lakhs", 400000), ("1 crore", 10_000_000),
    ("2 crore 50 lakh", 25_000_000), ("one and a half lakh", 150000), ("two and a half lakh", 250000),
    # Hinglish
    ("pachas hazaar", 50000), ("tees hazaar", 30000), ("das hazaar", 10000), ("do lakh", 200000),
    ("dedh lakh", 150000), ("dhai lakh", 250000), ("adhai lakh", 250000), ("saadhe teen lakh", 350000),
    ("sava lakh", 125000), ("sawa lakh", 125000), ("paune do lakh", 175000), ("paune lakh", 75000),
    ("aadha lakh", 50000), ("half lakh", 50000), ("dhai sau", 250), ("barah sau", 1200),
    # compound amounts (the reviewer's cases)
    ("ek lakh pachas hazaar", 150000), ("1 lakh 50 hazaar", 150000), ("teen hazaar paanch sau", 3500),
    ("1 lakh aur 50 hazaar", 150000),
    # Devanagari, as Gnani Prisma returns Hindi
    ("चालीस हज़ार", 40000), ("५० हज़ार", 50000), ("डेढ़ लाख", 150000),
    # speech-to-text quirks
    ("one and a 1/2 lakh", 150000), ("1 1/2 lakh", 150000),
    # an amount inside a sentence
    ("mujhe 50 hazaar ka loan chahiye", 50000), ("mujhe loan de do 50 hazaar ka", 50000),
    ("my salary is 40,000 a month", 40000),
]


@pytest.mark.parametrize("said,expected", AMOUNTS)
def test_amounts(said, expected):
    assert norm.number(said) == expected


@pytest.mark.parametrize("said", ["loan de do", "bata do", "how much do you earn", "", "nahi", "pata nahi",
                                  "{{monthly_income}}", "null", None])
def test_no_amount(said):
    """'do' as a verb ("loan de do" = "give the loan") must never become ₹2; blanks are None."""
    assert norm.number(said) is None


@pytest.mark.parametrize("said,expected", [("do", 2), ("do loan hain", 2), ("ek", 1), ("35", 35)])
def test_small_counts(said, expected):
    assert norm.number(said) == expected


@pytest.mark.parametrize("said,values", [
    ("40-50 hazaar", [40000, 50000]),
    ("40 ya 50 hazaar", [40000, 50000]),
    ("13,00,000 का 12,00,000 का", [1300000, 1200000]),
    ("5 hazaar aur 3 hazaar", [5000, 3000]),
])
def test_several_amounts_are_kept_apart(said, values):
    """Ranges and lists stay separate so Saathi can ask "which one?" instead of guessing."""
    assert [a["value"] for a in norm.amounts(said)] == values


@pytest.mark.parametrize("said,months", [("2 saal", 24), ("3 years", 36), ("18 mahine", 18), ("18", 18),
                                         ("2", 24), ("teen saal", 36)])
def test_tenure_months(said, months):
    assert norm.months(said) == months


@pytest.mark.parametrize("said,years", [("6 mahine", 0.5), ("2 saal", 2), ("18 months", 1.5), ("das saal", 10)])
def test_years(said, years):
    assert norm.years(said) == years


@pytest.mark.parametrize("said,expected", [
    ("haan", True), ("haan ji", True), ("yes", True), ("हाँ", True),
    ("nahi", False), ("no", False), ("never", False), ("नहीं", False), ("koi loan nahi", False),
    ("blue", None), ("", None),
])
def test_yes_no(said, expected):
    assert norm.boolean(said) is expected


@pytest.mark.parametrize("said,category", [
    ("apna ghar", "House / apartment"), ("own house", "House / apartment"), ("अपना घर", "House / apartment"),
    ("kiraye pe", "Rented apartment"), ("किराए पे", "Rented apartment"), ("pg mein", "Rented apartment"),
    ("parivaar ke saath", "With parents"), ("परिवार के साथ", "With parents"),
    ("sarkari quarter", "Office apartment"),
])
def test_housing(said, category):
    assert norm.housing(said)[0] == category


@pytest.mark.parametrize("said,income_type", [
    ("kirana ki dukaan", "Commercial associate"), ("auto driver", "Working"), ("retired", "Pensioner"),
    ("sarkari naukri", "State servant"), ("student", "Student"), ("berozgar", "Unemployed"),
])
def test_work(said, income_type):
    assert norm.work(said)[0] == income_type


@pytest.mark.parametrize("said,negative", [("-5000", True), ("minus 5 hazaar", True), ("5-10 hazaar", False),
                                           ("40,000", False)])
def test_negative(said, negative):
    assert norm.negative(said) is negative
