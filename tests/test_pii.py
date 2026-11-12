"""Personal identifiers are masked before any text reaches a language model, the pre-screen's own facts
(amounts, counts, years, job words) pass through unchanged, and nothing quoted from a masked span can be
stored as an answer.

Run with `python -m pytest tests/test_pii.py`.
"""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import converse as cv  # noqa: E402
import pii  # noqa: E402


@pytest.mark.parametrize("said, kind, secret", [
    ("mera number 9876543210 hai", "phone", "9876543210"),
    ("+91 98765 43210 pe call karo", "phone", "98765 43210"),
    ("call me on 098765-43210", "phone", "98765-43210"),
    ("मेरा नंबर ९८७६५४३२१० है", "phone", "९८७६५४३२१०"),
    ("aadhaar 2345 6789 0123 hai", "aadhaar", "2345 6789 0123"),
    ("aadhaar number 234567890123", "aadhaar", "234567890123"),
    ("PAN card ABCDE1234F", "pan", "ABCDE1234F"),
    ("pan abcde1234f hai", "pan", "abcde1234f"),
    ("ifsc SBIN0001234", "ifsc", "SBIN0001234"),
    ("account number 123456789012345", "account", "123456789012345"),
    ("card 4111 1111 1111 1111", "account", "4111 1111 1111 1111"),
    ("email ramesh.k@gmail.com", "email", "ramesh.k@gmail.com"),
    ("dob 12/05/1990", "date", "12/05/1990"),
    ("pincode 110001", "pincode", "110001"),
    ("mera naam Ramesh Kumar hai", "name", "Ramesh Kumar"),
    ("my name is Sunita Devi and I earn 40,000", "name", "Sunita Devi"),
    ("मेरा नाम रमेश है", "name", "रमेश"),
])
def test_identifiers_are_masked(said, kind, secret):
    out, found = pii.mask(said)
    assert secret not in out and f"[{kind}]" in out and found.get(kind) == 1, (out, found)


@pytest.mark.parametrize("said", [
    "mujhe 300000 chahiye", "salary 40,000 hai", "1.5 lakh baaki", "₹1,20,000 ka loan", "dedh lakh",
    "2 saal mein", "35 saal umar hai", "10000 EMI", "I am 35 years old", "do loan hain, kul EMI 6000",
    "kirana dukaan 6 saal se", "50 hazaar ka loan chahiye", "₹2,50,000", "since 2019", "12 months",
    "3-4 lakh", "30-35 hazaar", "2.5 lakh over 36 months", "pichhle 12 mahine mein",
])
def test_pre_screen_facts_pass_through_unchanged(said):
    assert pii.mask(said) == (said, {})


def test_mask_never_raises_on_odd_input():
    assert pii.mask(None) == (None, {}) and pii.mask("") == ("", {}) and pii.mask(5) == (5, {})


class CaptureRouter:
    available = True

    def __init__(self, reply):
        self.reply, self.sent = reply, []

    async def call(self, system, user, schema, prompt_version=""):
        self.sent.append(system + "\n" + user)
        return dict(self.reply), "groq"


SAID = "mera naam Ramesh Kumar hai, number 9876543210, aadhaar 2345 6789 0123, mujhe 3 lakh chahiye"


def test_no_identifier_reaches_the_llm_and_the_trail_counts_without_values():
    s, _ = cv.start("hi")
    s["asking"] = "loan_amount"
    router = CaptureRouter({"answers": [{"field": "loan_amount", "value": "300000", "quote": "3 lakh"}],
                            "reply": "", "asking": ""})
    s["history"].append(("Caller", SAID))
    out, g = asyncio.run(cv._extract(s, {"llm": router}, SAID, "loan_amount"))
    sent = "\n".join(router.sent)
    for secret in ("Ramesh", "Kumar", "9876543210", "2345 6789 0123"):
        assert secret not in sent, secret
    assert "3 lakh" in sent                             # the pre-screen fact is still there to extract
    assert ("loan_amount", "300000", "3 lakh") in out and g["used"] == "groq"
    trail = s["llm_trail"][-1]
    assert trail["masked"] == {"name": 1, "phone": 1, "aadhaar": 1}, trail   # the transcript (caller_said came masked)
    assert "9876543210" not in json.dumps(s["llm_trail"]) and "Ramesh" not in json.dumps(s["llm_trail"])


def test_a_quote_from_a_masked_span_is_never_stored():
    s, _ = cv.start("en")
    s["asking"] = "age"
    said = "my number is 9876543210"
    router = CaptureRouter({"answers": [{"field": "age", "value": "98", "quote": "[phone]"},
                                        {"field": "other_loans_count", "value": "9", "quote": "9876543210"}],
                            "reply": "", "asking": ""})
    out, _ = asyncio.run(cv._extract(s, {"llm": router}, said, "age"))
    assert not [c for c in out if c[0] == "age" and c[2] == "[phone]"], out
    assert "9876543210" not in "\n".join(router.sent)
