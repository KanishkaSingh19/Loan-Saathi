"""Numbers the way people say them, for text-to-speech.

Saathi's replies are written for the screen ("₹3,00,000", "₹14,977/month", "24 months"). Gnani's
TTS asks for numbers, dates and currency to be spelled out, and a caller expects "teen lakh rupaye",
not a digit string. `speakable(text, lang)` rewrites only what is spoken; the screen keeps the digits.

    speakable("EMI ₹14,977/month", "hi") -> "EMI chaudah hazaar nau sau sathattar rupaye mahina"
    speakable("Loan of ₹3,00,000", "en") -> "Loan of three lakh rupees"
"""
import re

HI = ("shoonya ek do teen chaar paanch chhah saat aath nau das gyaarah baarah terah chaudah pandrah solah satrah "
      "athaarah unnees bees ikkees baaees teyees chaubees pachchees chhabbees sattaaees atthaaees untees tees "
      "ikattees battees taintees chauntees paintees chhattees saintees adtees untaalees chaalees iktaalees "
      "bayaalees taintaalees chavaalees paintaalees chhiyaalees saintaalees adtaalees unchaas pachaas ikyaavan "
      "baavan tirpan chauvan pachpan chhappan sattaavan atthaavan unsath saath iksath baasath tirsath chausath "
      "painsath chhiyaasath sadsath adsath unhattar sattar ikhattar bahattar tihattar chauhattar pachhattar "
      "chhihattar sathattar athhattar unaasi assi ikyaasi bayaasi tiraasi chauraasi pachaasi chhiyaasi sattaasi "
      "athaasi navaasi nabbe ikyaanave baanave tiraanave chauraanave pachaanave chhiyaanave sattaanave "
      "atthaanave ninyaanave").split()
EN_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
           "sixteen seventeen eighteen nineteen").split()
EN_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
assert len(HI) == 100 and len(EN_ONES) == 20

# Indian place values, largest first: crore, lakh, thousand, hundred.
PLACES = {"hi": [(10_000_000, "crore"), (100_000, "lakh"), (1000, "hazaar"), (100, "sau")],
          "en": [(10_000_000, "crore"), (100_000, "lakh"), (1000, "thousand"), (100, "hundred")]}
HALVES = {"hi": {1.5: "dedh", 2.5: "dhai"}, "en": {}}


def _below_100(n, lang):
    if lang == "hi":
        return HI[n]
    if n < 20:
        return EN_ONES[n]
    tens, ones = divmod(n, 10)
    return EN_TENS[tens] + (f"-{EN_ONES[ones]}" if ones else "")


def words(n, lang="hi"):
    """A whole number in words, Indian style: 340000 -> 'teen lakh chaalees hazaar'."""
    lang = "en" if lang == "en" else "hi"
    n = int(n)
    if n < 0:
        return ("minus " if lang == "en" else "maainus ") + words(-n, lang)
    if n < 100:
        return _below_100(n, lang)
    parts = []
    for size, name in PLACES[lang]:
        if n >= size:
            count, n = divmod(n, size)
            parts.append(f"{words(count, lang)} {name}")
    if n:
        parts.append(_below_100(n, lang))
    return " ".join(parts)


def _decimal(text, lang):
    v = float(text)
    if v == int(v):
        return words(int(v), lang)
    if v in HALVES[lang]:
        return HALVES[lang][v]
    whole, frac = text.split(".", 1)
    point = "point" if lang == "en" else "dashamlav"
    return f"{words(int(whole), lang)} {point} " + " ".join(words(int(d), lang) for d in frac)


MONEY = re.compile(r"(-?)₹\s?(\d[\d,]*(?:\.\d+)?)(\s?/\s?month|/mo\b)?")
NUMBER = re.compile(r"(?<![\w#.])(\d[\d,]*(?:\.\d+)?)(?![\w.]*[A-Za-z])")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s?%")
UNITS = {"hi": {"rupees": "rupaye", "month": "mahina", "percent": "pratishat"},
         "en": {"rupees": "rupees", "month": "a month", "percent": "percent"}}


def speakable(text, lang="hi"):
    """The same sentence with every amount, number and percentage spelled out for TTS."""
    if not text:
        return text
    lang = "en" if lang == "en" else "hi"
    u = UNITS[lang]

    def money(m):
        sign = "minus " if m.group(1) and lang == "en" else "maainus " if m.group(1) else ""
        said = f"{sign}{_decimal(m.group(2).replace(',', ''), lang)} {u['rupees']}"
        return said + (f" {u['month']}" if m.group(3) else "")

    text = MONEY.sub(money, text)
    text = PERCENT.sub(lambda m: f"{_decimal(m.group(1), lang)} {u['percent']}", text)
    text = NUMBER.sub(lambda m: _decimal(m.group(1).replace(",", ""), lang), text)
    return re.sub(r"\s{2,}", " ", text)
