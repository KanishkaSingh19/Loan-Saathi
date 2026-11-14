"""The documents to keep ready, for this caller: what a lender usually asks for at the KYC and document steps,
from what the loan is for (normalize.loan_purpose) and how they earn (normalize.work_category).

Loan-Saathi never asks for, sees or stores any of these; it only tells the caller what to keep ready. The list
is general guidance (lenders differ), and says so.
"""
import normalize as norm

NOTE = ("Usually asked for; your lender may ask for others. Loan-Saathi never asks for or keeps these documents.")
NOTE_HI = ("Aam taur par yeh maange jaate hain; aapka lender kuch aur bhi maang sakta hai. Loan-Saathi in "
           "documents ko na maangta hai, na rakhta hai.")


def _d(key, en, hi):
    return {"key": key, "en": en, "hi": hi}


KYC = [_d("pan", "PAN card", "PAN card"),
       _d("aadhaar", "Aadhaar (or another address proof)", "Aadhaar (ya koi aur pate ka saboot)"),
       _d("photo", "Two recent passport-size photos", "Do haal ki passport size photo")]
BANK_6 = _d("bank_6m", "Bank statements for the last 6 months", "Pichhle 6 mahine ki bank statement")
BANK_3 = _d("bank_3m", "Bank statements for the last 3 months (the account your salary comes into)",
            "Pichhle 3 mahine ki bank statement (jis khaate mein salary aati hai)")
INCOME = {
    "salaried": [_d("salary_slips", "Your last 3 salary slips", "Pichhli 3 salary slip"),
                 _d("form16", "Form 16 from your employer", "Employer ka Form 16"), BANK_3],
    "government": [_d("salary_slips", "Your last 3 salary slips", "Pichhli 3 salary slip"),
                   _d("service_id", "Your department ID card", "Vibhaag ka ID card"), BANK_3],
    "self_employed": [_d("itr", "Income tax returns (ITR) for the last 2 years, if you file them",
                         "Pichhle 2 saal ka ITR, agar bharte hain"),
                      _d("business_proof", "Proof of your business: shop licence, Udyam or GST registration, if any",
                         "Business ka saboot: dukaan ka licence, Udyam ya GST registration, agar hai"), BANK_6],
    "driver": [_d("driving_licence", "Your driving licence", "Driving licence"),
               _d("vehicle_rc", "Your vehicle's RC, if you own it", "Gaadi ki RC, agar aapki apni hai"), BANK_6],
    "farmer": [_d("land_records", "Land records (khatauni / 7-12 extract), if the land is yours",
                  "Zameen ke kaagaz (khatauni / 7-12), agar zameen aapki hai"),
               _d("kcc", "Kisan Credit Card or bank passbook", "Kisan Credit Card ya bank passbook"), BANK_6],
    "daily_wage": [BANK_6, _d("employer_letter", "A letter from your employer or contractor, if you can get one",
                             "Maalik ya thekedaar ki chitthi, agar mil sake")],
    "skilled": [BANK_6, _d("work_proof", "Proof of your work (a shop or trade registration, invoices), if any",
                           "Kaam ka saboot (dukaan ya hunar ka registration, bills), agar hai")],
    "retired": [_d("pension", "Pension slips or your PPO", "Pension slip ya PPO"), BANK_6],
    "student": [_d("co_applicant", "Income proof of a co-applicant (usually a parent)",
                   "Co-applicant (aam taur par mata-pita) ki kamaai ka saboot")],
    "unemployed": [_d("co_applicant", "Income proof of a co-applicant or guarantor",
                      "Co-applicant ya guarantor ki kamaai ka saboot")],
    "other": [BANK_6, _d("income_proof", "Any proof of your income you have", "Kamaai ka jo bhi saboot ho")],
}
PURPOSE = {
    "vehicle": [_d("quotation", "The dealer's quotation (proforma invoice) for the vehicle",
                   "Gaadi ka dealer quotation (proforma invoice)")],
    "education": [_d("admission", "The admission letter and the college's fee structure",
                     "Admission letter aur college ki fees ki list"),
                  _d("marksheets", "Mark sheets of your last exams", "Pichhli pareekshaon ki marksheet"),
                  _d("co_applicant", "Income proof of a co-applicant (usually a parent)",
                     "Co-applicant (aam taur par mata-pita) ki kamaai ka saboot")],
    "business": [_d("business_proof", "Proof of your business: shop licence, Udyam or GST registration, if any",
                    "Business ka saboot: dukaan ka licence, Udyam ya GST registration, agar hai"),
                 _d("quotes", "Quotes or bills for the stock or machine you'll buy", "Maal ya machine ke quotation/bill")],
    "home": [_d("property", "Property papers (sale deed or allotment letter)", "Ghar/zameen ke kaagaz (registry ya allotment)"),
             _d("estimate", "A builder's or contractor's estimate for the work", "Builder ya thekedaar ka kharche ka estimate")],
    "personal": [],
    "other": [],
}


def checklist(loan_purpose=None, work=None, lang="en"):
    """[{"key", "text"}] in the order a lender asks: identity first, then income, then the loan's own papers.
    `loan_purpose` and `work` are the caller's own words (or a category key); either may be missing."""
    purpose = loan_purpose if loan_purpose in PURPOSE else norm.loan_purpose(loan_purpose)
    work_cat = work if work in INCOME else norm.work_category(work)
    items, seen = [], set()
    for d in KYC + INCOME.get(work_cat or "", [BANK_6]) + PURPOSE.get(purpose or "", []):
        if d["key"] not in seen:
            seen.add(d["key"])
            items.append({"key": d["key"], "text": d["hi" if lang == "hi" else "en"]})
    return items


def spoken(loan_purpose=None, work=None, lang="en", n=4):
    """A short sentence for the call: the first few items after PAN and Aadhaar, and that the screen has the rest."""
    items = checklist(loan_purpose, work, lang)
    names = [i["text"] for i in items if i["key"] not in ("photo",)][:n]
    more = len(items) - len(names)
    if lang == "hi":
        tail = f" Baaki {more} cheezein screen par hain." if more > 0 else ""
        return "Yeh documents taiyaar rakhiye: " + "; ".join(names) + "." + tail
    tail = f" The other {more} are on your screen." if more > 0 else ""
    return "Keep these documents ready: " + "; ".join(names) + "." + tail
