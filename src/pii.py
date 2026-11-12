"""Masks personal identifiers before any text reaches a language model (converse._llm is the only way out).

What is masked: phone numbers, Aadhaar, PAN, IFSC, bank account / card numbers, email addresses, dates of
birth, PIN codes said as a PIN code, and a name the caller introduces ("mera naam Ramesh hai",
"my name is Sunita Devi"). What is kept: everything the pre-screen needs, so amounts ("3 lakh", "40,000",
"300000", "₹1,20,000"), counts, years, ages and job words pass through unchanged.

Masked text keeps its shape ("[phone]") so the model still reads a sensible sentence. The caller's own words
never leave the server in clear; values the model returns are checked against the unmasked words
(converse.quoted), so a quote that contains a mask token can never be stored.
"""
import re

D = "[0-9०-९]"  # ASCII and Devanagari digits
_NB = r"(?<![0-9०-९A-Za-z])"   # not glued to a digit or letter before
_NA = r"(?![0-9०-९A-Za-z])"    # ... or after

PATTERNS = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.I)),
    # PAN: 5 letters, 4 digits, 1 letter. IFSC: 4 letters, 0, 6 letters/digits.
    ("pan", re.compile(rf"{_NB}[A-Z]{{5}}{D}{{4}}[A-Z]{_NA}", re.I)),
    ("ifsc", re.compile(rf"{_NB}[A-Z]{{4}}0[A-Z0-9]{{6}}{_NA}", re.I)),
    # Card numbers first (16 digits in fours would otherwise read as an Aadhaar plus a stray group).
    ("account", re.compile(rf"{_NB}{D}{{4}}(?:[\s-]{D}{{4}}){{3}}(?:[\s-]{D}{{1,3}})?{_NA}")),
    # Aadhaar: 12 digits (first 2-9), often in groups of four.
    ("aadhaar", re.compile(rf"{_NB}[2-9२-९]{D}{{3}}[\s-]?{D}{{4}}[\s-]?{D}{{4}}{_NA}")),
    # Indian mobile: optional +91 / 0, then 10 digits starting 6-9, optionally split 5+5 or 3+3+4.
    ("phone", re.compile(rf"{_NB}(?:\+?91[\s-]?|0)?[6-9६-९]{D}{{2}}[\s-]?{D}{{2}}[\s-]?{D}[\s-]?{D}{{4}}{_NA}")),
    # Bank accounts and unspaced card numbers: 9-18 digits in a row, no commas (an amount has them or is shorter).
    ("account", re.compile(rf"{_NB}{D}{{9,18}}{_NA}")),
    # Dates of birth: 12/05/1990, 12-5-90, 1990-05-12.
    ("date", re.compile(rf"{_NB}(?:{D}{{1,2}}[/.-]{D}{{1,2}}[/.-](?:{D}{{4}}|{D}{{2}})|{D}{{4}}-{D}{{1,2}}-{D}{{1,2}}){_NA}")),
    # A PIN code only when it is called one: a bare 6-digit number is usually an amount.
    ("pincode", re.compile(rf"((?:pin\s*code|pincode|pin|पिन\s*कोड|पिनकोड)\s*(?:hai|is|:|-)?\s*){D}{{6}}{_NA}", re.I)),
]
# A name the caller introduces: up to three words after "my name is" / "mera naam" / "मेरा नाम".
NAME = re.compile(
    r"((?:my\s+name\s+is|name\s*[:-]|i\s+am\s+called|mera\s+naam|mera\s+nam|naam\s+hai|मेरा\s+नाम)\s+)"
    r"((?:(?!hai\b|hain\b|है|and\b|aur\b|और)[A-Za-zऀ-ॿ.'-]+\s*){1,3})", re.I)


TOKEN = re.compile(r"\[(?:email|pan|ifsc|aadhaar|phone|account|date|pincode|name)\]")


def has_token(text):
    """True when text contains a mask token: such a span can never be an answer."""
    return bool(TOKEN.search(str(text or "")))


def mask(text):
    """(masked text, {kind: count}). Never raises; non-strings come back unchanged."""
    if not isinstance(text, str) or not text:
        return text, {}
    found = {}

    def sub(kind, keep_group=False):
        def f(m):
            found[kind] = found.get(kind, 0) + 1
            return (m.group(1) if keep_group else "") + f"[{kind}]"
        return f

    out = text
    for kind, pat in PATTERNS:
        out = pat.sub(sub(kind, keep_group=kind == "pincode"), out)

    def name(m):
        found["name"] = found.get("name", 0) + 1
        tail = " " if m.group(2).endswith(" ") else ""
        return m.group(1) + "[name]" + tail
    out = NAME.sub(name, out)
    return out, found
