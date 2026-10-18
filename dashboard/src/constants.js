export const DECISIONS = {
  pre_approved: { label: "Pre-approved", tone: "good" },
  counter_offer: { label: "Safer counter-offer", tone: "warn" },
  needs_review: { label: "Officer review", tone: "warn" },
  not_eligible_now: { label: "Not eligible now", tone: "bad" },
  need_more_info: { label: "Needs more info", tone: "info" },
};

export const FILTERS = [
  { id: "all", label: "All" },
  { id: "pre_approved", label: "Pre-approved" },
  { id: "offer", label: "Counter-offer / review", match: ["counter_offer", "needs_review"] },
  { id: "not_eligible_now", label: "Not eligible" },
  { id: "need_more_info", label: "Needs info" },
];

export const SAID_LABELS = {
  loan_amount: "Loan amount",
  tenure_months: "Repay in",
  monthly_income: "Monthly income",
  work: "Work",
  years_in_work: "Years in work",
  age: "Age",
  other_loans_count: "Other loans",
  other_loans_outstanding: "Still owed",
  other_loans_emi: "Existing EMI",
  housing: "Housing",
  family_members: "Family size",
  children: "Children",
  education: "Education",
  owns_vehicle: "Owns vehicle",
};

// The three demo scripts from agent/SETUP.md, as the agent would send them.
export const SAMPLES = {
  "Kirana owner": {
    monthly_income: "30 hazaar", loan_amount: "50,000", tenure_months: "1 saal", age: "45",
    work: "kirana ki dukaan", years_in_work: "15 saal", education: "10th pass", housing: "apna ghar",
    family_members: "5", children: "3", owns_vehicle: "haan", other_loans_count: "0",
  },
  "Asks too much": {
    monthly_income: "25 hazaar", loan_amount: "3 lakh", tenure_months: "2 saal", age: "34",
    work: "private company me sales", years_in_work: "6 saal", education: "graduate",
    housing: "kiraye ka ghar", family_members: "4", children: "2", owns_vehicle: "nahi",
    other_loans_count: "1", other_loans_outstanding: "40 hazaar", other_loans_emi: "2000",
  },
  "Heavy debt": {
    monthly_income: "15000", loan_amount: "2 lakh", tenure_months: "2 saal", age: "22",
    work: "construction mazdoor", years_in_work: "6 mahine", education: "8th", housing: "rent",
    family_members: "1", children: "0", owns_vehicle: "no", other_loans_count: "3",
    other_loans_outstanding: "1.5 lakh",
  },
};

// The model's category names come from the (non-Indian) dataset; show plainer words.
const FRIENDLY = {
  "Commercial associate": "Self-employed / business",
  "State servant": "Government job",
  Working: "Salaried / wage work",
  Pensioner: "Retired / pension",
  Businessman: "Business owner",
  "House / apartment": "Own house",
  "Rented apartment": "Rented",
  "Office apartment": "Company quarters",
  "Municipal apartment": "Government housing",
  "Secondary / secondary special": "School (10th/12th)",
  "Higher education": "College degree",
  "Lower secondary": "Below 10th",
  "Incomplete higher": "College, not finished",
  "Low-skill Laborers": "Helper",
  Laborers: "Labourer",
};
const FRIENDLY_RE = new RegExp(
  Object.keys(FRIENDLY).map((k) => k.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")).join("|"),
  "g",
);
export const friendly = (s) => String(s).replace(FRIENDLY_RE, (m) => FRIENDLY[m]);

// Values the agent sends for questions the caller skipped.
export const isBlank = (v) =>
  v === undefined || v === null || v === "" || String(v).includes("{{");
