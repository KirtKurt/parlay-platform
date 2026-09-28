"""Fail-closed champion/challenger qualification."""
MIN_UNTOUCHED=300
REQUIRED=("brier","logloss","ece")

def qualify(champion, challenger, *, untouched_rows, provenance_ok, leakage_tests_ok, missingness_ok):
    reasons=[]
    if untouched_rows < MIN_UNTOUCHED: reasons.append("insufficient_untouched_chronological_holdout")
    if not provenance_ok: reasons.append("provenance_failed")
    if not leakage_tests_ok: reasons.append("leakage_protection_failed")
    if not missingness_ok: reasons.append("missing_data_robustness_failed")
    for m in REQUIRED:
        if champion.get(m) is None or challenger.get(m) is None: reasons.append(f"{m}_missing")
        elif challenger[m] > champion[m]: reasons.append(f"{m}_worse_than_champion")
    return {"qualified":not reasons,"reasons":reasons,"automatic_promotion":False}
