"""Questions a caller asks mid-call ("EMI kya hota hai?", "How long can I repay?"), answered in their language.

Fixed answers, written and tested here, from the policy's own numbers: no LLM writes them, so they can't promise
an approval, name a lender or invent a rate. After the answer Saathi goes back to what it was asking. A question
with no fixed answer gets an honest "a loan advisor can answer that", and the call goes on.
"""
import re

import scoring
from policy import POLICY
from scoring import inr

# A question: a question mark, or a question word at the start ("what is ...", "kya hota hai", "kitna milega").
# Not "do": in Hinglish it is "two" ("do saal mein chukaunga").
QUESTION = re.compile(
    r"\?|^\s*(?:what|what's|whats|how|why|when|which|who|can|could|will|would|is|are|does|should|tell me|explain)\b|"
    r"^\s*(?:kya|kaise|kyun|kyon|kab|kitna|kitne|kitni|kaun|kahan|kis|batao|bataiye|samjhao|samjhaiye)\b|"
    r"\b(?:kya\s+(?:hota|hoti|hai|hain|matlab)|kaise\s+(?:milega|milegi|hota|karu|karun|kare)|kitna\s+(?:milega|"
    r"lagega|hoga)|kitne\s+(?:din|saal|mahine)\s+(?:mein|me)\s+milega|matlab\s+kya)\b|"
    r"^\s*(?:क्या|कैसे|क्यों|कब|कितना|कितने|कितनी|कौन|कहाँ)|क्या\s+(?:होता|होती|है)|कितना\s+(?:मिलेगा|लगेगा)", re.I)


def is_question(text):
    return bool(text) and bool(QUESTION.search(text))


def _pct(x):
    return f"{x * 100:g}%"


def _emi_example():
    lo, hi = scoring.emi_range(100000, 24)
    return inr(lo), inr(hi)


def _answers():
    lo_r, hi_r = _pct(POLICY["emi_rate_low"]), _pct(POLICY["emi_rate_high"])
    foir, days = _pct(POLICY["max_foir"]), POLICY["retention_days"]
    e_lo, e_hi = _emi_example()
    return [
        ("approval", r"approv|guarantee|pakka|confirm\w* (?:loan|milega)|sanction|पक्का|मंज़ूर|मंजूर",
         "Saathi doesn't approve loans. This is a pre-screening: an estimate of whether a loan fits you. A lender "
         "decides only after checking your documents.",
         "Saathi loan approve nahi karta. Yeh sirf pre-screening hai, yaani andaaza ki loan aapke liye theek baithta "
         "hai ya nahi. Lender documents jaanchne ke baad hi faisla karta hai."),
        ("who", r"\bbank\b|who are you|are you (?:a|an) (?:bank|lender|human|person|robot|ai)|aap kaun|kaun ho|"
                r"kya aap (?:bank|insaan|robot)|आप कौन",
         "I'm Loan Saathi, a free pre-screening assistant. I'm not a bank or a lender, and I don't give loans: I help "
         "you see whether a loan fits before you apply.",
         "Main Loan Saathi hoon, ek free pre-screening sahayak. Main bank ya lender nahi hoon, aur loan nahi deta: "
         "apply karne se pehle main bataata hoon ki loan aapke liye theek baithta hai ya nahi."),
        ("foir", r"\bfoir\b|50 ?%|50 percent|pachas (?:pratishat|percent)|aadhi kamaai",
         f"FOIR is the share of your monthly income that goes to EMIs. Saathi's rule: all your EMIs together, old "
         f"and new, should stay within {foir} of your income.",
         f"FOIR matlab aapki mahine ki kamaai ka kitna hissa EMI mein jaata hai. Saathi ka niyam: saari EMI milakar, "
         f"puraani aur nayi, kamaai ke {foir} ke andar honi chahiye."),
        ("how_much", r"how much (?:loan )?(?:can|will|could) i get|how much (?:can|will) i borrow|eligib|"
                     r"kitna (?:loan )?mil(?:ega|egi|ta|sakta)|kitne ka loan mil|mil sakta hai kya|"
                     r"कितना (?:लोन )?मिल",
         f"That depends on your income and the EMIs you already pay: all your EMIs together should stay within {foir} "
         f"of your income. Answer the remaining questions and I'll estimate it for you.",
         f"Yeh aapki kamaai aur chal rahi EMI par nirbhar hai: saari EMI milakar kamaai ke {foir} ke andar rehni "
         f"chahiye. Baaki sawaalon ke jawab dijiye, phir main andaaza bata dunga."),
        ("emi", r"\bemi\b|\bkist\b|\bkisht\b|installment|instalment|किस्त|ई\s*एम\s*आई",
         f"An EMI is the fixed amount you pay every month until the loan is repaid; it includes the interest. For "
         f"example, ₹1 lakh over 2 years is about {e_lo} to {e_hi} a month at {lo_r} to {hi_r} a year.",
         f"EMI woh fix rakam hai jo loan chukne tak har mahine deni hoti hai; isme byaaj bhi shaamil hai. Jaise, "
         f"₹1 lakh 2 saal ke liye lagbhag {e_lo} se {e_hi} mahina, {lo_r} se {hi_r} saalana byaaj par."),
        ("interest", r"interest|\brate\b|byaaj|byaj|vyaaj|sood|ब्याज",
         f"The lender sets your interest rate after checking your documents. For loans like this it is often "
         f"{lo_r} to {hi_r} a year, so Saathi shows EMIs across that range, not one number.",
         f"Byaaj ka rate lender documents dekhkar tay karta hai. Aise loan par aksar {lo_r} se {hi_r} saalana hota "
         f"hai, isliye Saathi EMI ek number nahi, is range mein dikhata hai."),
        ("fees", r"processing fee|\bfees?\b|charges?|shulk|शुल्क|फीस",
         "Lenders often charge a processing fee and other charges on top of interest. Ask for every charge in "
         "writing. If you already have an offer, say 'mujhe ek offer mila hai' and I'll work out its real cost.",
         "Lender aksar byaaj ke alawa processing fee aur doosre charges lete hain. Har charge likhit mein maangiye. "
         "Agar offer mil chuka hai, toh 'mujhe ek offer mila hai' boliye, main uska asli kharcha nikaal dunga."),
        ("documents", r"document|kaagaz|kagaz|papers?\b|kya kya chahiye|कागज़|कागज|दस्तावेज़",
         "Usually PAN, Aadhaar, bank statements and proof of your income. At the end I'll give you a list that fits "
         "your loan and your work. I never ask for the documents themselves.",
         "Aam taur par PAN, Aadhaar, bank statement aur kamaai ka saboot. Aakhir mein main aapke loan aur kaam ke "
         "hisaab se list dunga. Main khud koi document nahi maangta."),
        ("score", r"cibil|credit score|\bscore\b|स्कोर|सिबिल",
         "Saathi doesn't check your credit score; a lender will. Paying every EMI on time is the best way to keep it "
         "healthy.",
         "Saathi aapka credit score nahi dekhta; lender dekhega. Har EMI samay par bharna score achha rakhne ka sabse "
         "achha tareeka hai."),
        ("privacy", r"privacy|my data|meri jaankari|data (?:safe|kahan)|safe hai|kaun dekhega|share|record|"
                    r"सुरक्षित|डेटा",
         f"Your answers are kept for {days} days only if you agree; say 'save mat karo' any time to stop. Phone, "
         f"Aadhaar and PAN numbers are never sent to the AI, and Saathi doesn't share your answers with lenders.",
         f"Aapke jawab sirf aapki marzi se {days} din tak rakhe jaate hain; kabhi bhi 'save mat karo' boliye. Phone, "
         f"Aadhaar aur PAN number AI ko kabhi nahi bheje jaate, aur Saathi aapke jawab lenders ko nahi deta."),
        ("why_ask", r"why (?:do|are) you (?:ask|need)|why (?:my|is my) (?:age|income|salary|work)|kyun (?:pooch|chahiye)|"
                    r"kyon (?:pooch|chahiye)|क्यों पूछ",
         "Saathi asks only what decides whether a loan fits: income and EMIs for the affordability rule, and work and "
         "repayment history for the risk estimate. It never asks your gender or marital status.",
         "Saathi sirf woh poochta hai jisse pata chale ki loan theek baithta hai ya nahi: kamaai aur EMI affordability "
         "ke liye, kaam aur purana record risk ke andaaze ke liye. Gender ya shaadi ke baare mein kabhi nahi poochta."),
        ("tenure", r"how long|tenure|kitne (?:saal|mahine)|kab tak|repay|chuka(?:na|ne|unga)|कितने साल|कब तक",
         "That's up to you and the lender, often 1 to 5 years for personal and vehicle loans. A longer time means a "
         "smaller EMI, but more interest in total.",
         "Yeh aap aur lender tay karte hain, personal aur gaadi loan mein aksar 1 se 5 saal. Lamba samay matlab "
         "chhoti EMI, lekin kul byaaj zyada."),
        ("time", r"kitne din mein milega|how (?:soon|fast)|when will i get|kab milega|कब मिलेगा",
         "Saathi gives an estimate right away. Getting the money depends on the lender: after you apply and they "
         "check your documents, it often takes a few days.",
         "Saathi andaaza turant deta hai. Paisa milna lender par nirbhar hai: apply karne aur documents jaanchne ke "
         "baad aksar kuch din lagte hain."),
    ]


FAQ = [(key, re.compile(pattern, re.I), en, hi) for key, pattern, en, hi in _answers()]


def answer_question(text, lang="en"):
    """(topic, answer) for a question Saathi has a fixed answer to, or None."""
    if not is_question(text):
        return None
    for key, pattern, en, hi in FAQ:
        if pattern.search(text):
            return key, (hi if lang == "hi" else en)
    return None


UNKNOWN_QUESTION = {"en": "Good question. I can't answer that one here, but a loan advisor can: just say 'loan advisor'.",
           "hi": "Achha sawaal hai. Iska jawab main yahan nahi de sakta, lekin loan advisor de sakta hai: bas "
                 "'loan advisor' boliye."}
RESUME = {"en": " Now, ", "hi": " Ab aage: "}
