"""Synthetic dry run of the user study: every persona card through the live pipeline.

    python -I -X utf8 src/study_dryrun.py --base http://127.0.0.1:8000 --key <WEBHOOK_KEY> [--speech]

Each persona "speaks" scripted, natural lines (Hinglish, English or Devanagari Hindi). With --speech,
every line is first spoken by Gnani Timbre and transcribed by Gnani Prisma, so speech-recognition
errors are included. Calls are tagged synthetic and reported separately from real participants. No
survey is filled in: opinions can't be simulated. This tests the study kit and gives a baseline; it is
not user testing.
"""
import argparse
import sys
import time

import httpx

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
import study  # noqa: E402

LINES = {
    "P01": ("Mujhe 50 hazaar ka loan chahiye 1 saal ke liye, dukaan ke liye. Mahine mein 30 hazaar kamaata hoon.",
            {"work": "kirana ki dukaan hai", "years_in_work": "15 saal se", "other_loans_count": "koi loan nahi",
             "housing": "apna ghar hai", "age": "45 saal"}),
    "P02": ("Mujhe dedh lakh chahiye 2 saal ke liye, nayi auto ke liye. Kamaai 25 hazaar mahina.",
            {"work": "auto chalata hoon", "years_in_work": "6 saal", "other_loans_count": "haan ek loan hai",
             "other_loans_emi": "3 hazaar ki kist", "other_loans_outstanding": "40 hazaar baaki hai",
             "housing": "kiraye pe", "age": "32"}),
    "P03": ("I want a loan of 3 lakh for 3 years for my sister's wedding. My salary is 40,000 a month.",
            {"work": "I work as an office clerk", "years_in_work": "8 years", "other_loans_count": "one loan",
             "other_loans_emi": "8,000 EMI", "other_loans_outstanding": "1.5 lakh still owed",
             "housing": "with my family", "age": "29"}),
    "P04": ("Mujhe 30 hazaar chahiye ilaaj ke liye. Main roz 700 rupaye kamata hoon, mahine mein 26 din kaam.",
            {"tenure_months": "pata nahi, aap bataiye", "monthly_income": "mahine ka 18,200",
             "work": "construction mazdoor", "years_in_work": "3 saal", "other_loans_count": "nahi",
             "housing": "kiraye ka kamra", "age": "38"}),
    "P05": ("I need 1 lakh over 2 years. I earn 35,000 a month from my tailoring business.",
            {"work": "tailoring business", "years_in_work": "10 years", "other_loans_count": "3 loans",
             "other_loans_emi": "total EMI 12,000", "other_loans_outstanding": "about 9 lakh still owed",
             "housing": "own house", "age": "41"}),
    "P06": ("Mujhe 4 lakh chahiye 2 saal ke liye, chhoti dukaan lene ke liye. Kamaai 22 hazaar mahina.",
            {"work": "sabzi bechta hoon", "years_in_work": "12 saal", "other_loans_count": "nahi",
             "housing": "parivaar ke saath", "age": "36"}),
    "P07": ("I'd like 2 lakh for 4 years for a scooter. I'm a nurse earning 45,000 a month.",
            {"work": "nurse at a private hospital", "years_in_work": "5 years", "other_loans_count": "no loans",
             "housing": "on rent", "age": "27"}),
    "P08": ("Mujhe 75 hazaar chahiye 18 mahine ke liye, ghar ki marammat. Pension 28 hazaar milti hai.",
            {"work": "retired teacher hoon", "other_loans_count": "koi loan nahi", "housing": "apna ghar",
             "age": "63 saal"}),
    "P09": ("I need 60,000 for 1 year. I earn 20,000 a month.",
            {"work": "delivery rider", "years_in_work": "2 years", "other_loans_count": "no",
             "housing": "with family", "age": "24"}),
    "P10": ("मुझे 80 हज़ार का लोन 2 साल के लिए चाहिए। मैं महीने का 26 हज़ार कमाती हूँ।",
            {"work": "सिलाई का काम", "years_in_work": "9 साल से", "other_loans_count": "कोई लोन नहीं",
             "housing": "किराये के घर में", "age": "34 साल"}),
    "P11": ("Mujhe dedh lakh chahiye teen saal ke liye. Salary 32 hazaar hai.",
            {"work": "sarkari daftar mein chaprasi", "years_in_work": "11 saal", "other_loans_count": "ek loan",
             "other_loans_emi": "5 hazaar EMI", "other_loans_outstanding": "60 hazaar baaki",
             "housing": "sarkari quarter", "age": "48"}),
    "P12": ("I want 25,000 for one year for a laptop. I earn 12,000 a month from a part-time job.",
            {"work": "student with a part-time job", "other_loans_count": "no", "housing": "with my parents",
             "age": "20"}),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--key", required=True)
    ap.add_argument("--speech", action="store_true", help="speak each line with Timbre, transcribe with Prisma")
    ap.add_argument("--only", default="", help="comma-separated persona ids")
    args = ap.parse_args()
    H = {"X-Webhook-Key": args.key}
    client = httpx.Client(base_url=args.base, headers=H, timeout=90)

    def hear(text, lang):
        if not args.speech:
            return text
        wav = client.post("/v1/tts", json={"text": text, "language": "hi-IN" if lang == "hi" else "en-IN"}).content
        r = client.post("/v1/stt", files={"audio_file": ("s.wav", wav, "audio/wav")},
                        data={"language_code": "hi-IN" if lang == "hi" else "en-IN"})
        return r.json().get("transcript", "") if r.is_success else ""

    def say(sid, text="", action=""):
        r = client.post("/v1/converse", json={"session_id": sid, "text": text, "action": action})
        r.raise_for_status()
        return r.json()

    ids = [i for i in (args.only.split(",") if args.only else LINES)]
    for pid in ids:
        persona = study.BY_ID[pid]
        first, answers = LINES[pid]
        t0 = time.time()
        r = client.post("/v1/converse", json={"language": persona["language"], "persona": pid,
                                               "participant": "SYN", "synthetic": True}).json()
        sid = r["session_id"]
        heard = hear(first, persona["language"])
        t = say(sid, heard)
        for _ in range(20):
            if t["state"] != "COLLECTING_PROFILE":
                break
            p = t.get("pending")
            if p and p["kind"] == "choose":
                truth = persona["truth"].get(p["field"])
                pick = min(range(len(p["options"])), key=lambda i: abs(p["options"][i] - (truth or 0)))
                t = say(sid, "", f"choose:{pick}")
            elif p:
                truth = persona["truth"].get(p["field"])
                ok = truth is None or abs((p.get("value") or 0) - truth) <= max(1, 0.05 * truth)
                t = say(sid, "", "yes" if ok else "no")
                if not ok and p["field"] in answers:
                    t = say(sid, hear(answers[p["field"]], persona["language"]))
            else:
                line = answers.get(t["asking"])
                if line is None:  # the persona card doesn't cover it: an honest "don't know"
                    line = "pata nahi" if persona["language"] == "hi" else "I don't know"
                t = say(sid, hear(line, persona["language"]))
        if pid == "P09" and t["state"] == "VERIFYING_INFORMATION":  # the scripted correction
            t = say(sid, hear("My income is wrong", "en"))
            t = say(sid, hear("24,000", "en"))
        if t["state"] == "VERIFYING_INFORMATION":
            t = say(sid, "", "confirm")
        print(f"{pid} {persona['title']:<32} {t['state']:<22} first line heard as: {heard[:70]!r}"
              f"  ({time.time() - t0:.0f}s)", flush=True)
    rep = client.get("/v1/study/report").json()["synthetic_dry_runs"]
    print("\nSynthetic dry-run summary (not user results):")
    for k in ("calls", "reached_result", "median_seconds_to_result", "first_pass_accuracy", "final_accuracy",
              "readbacks_per_call", "corrections_per_call", "gemini_share"):
        print(f"  {k}: {rep[k]}")
    print("  first-pass accuracy by field:", rep["field_first_pass_accuracy"])


if __name__ == "__main__":
    main()
