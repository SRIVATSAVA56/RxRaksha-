import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.phase1_load import DEFAULT_DB
from app.normalize.engine import NormalizeEngine, apply_phase2_migration, load_combos

DEMO = [
    "Tab Crocin 500mg 1-0-1",
    "Dolo-650 BD",
    "Warf 5mg OD",
    "Augmentin 625 TDS",
    "Combiflam SOS",
    "Pan 40 OD",
    "Thyronorm 100mcg OD",
    "Krocin 500mg OD",        # OCR typo -> fuzzy, needs review
    "Blorfazep 10mg OD",      # not in KB -> unmatched
]

def main():
    if not DEFAULT_DB.exists():
        raise SystemExit(f"DB not found at {DEFAULT_DB} — run Phase 1 first.")
    conn = sqlite3.connect(DEFAULT_DB)
    apply_phase2_migration(conn)
    n = load_combos(conn)
    print(f"combo components loaded: {n}")

    eng = NormalizeEngine(conn)
    result = eng.normalize_prescription(DEMO)

    print("\n===== LINE NORMALIZATION =====")
    for ln in result.lines:
        print(f"\n  '{ln.raw}'  [{ln.status}]")
        print(f"    parsed: strength={ln.parsed.strength_mg}  doses/day={ln.parsed.doses_per_day}")
        for i in ln.ingredients:
            flag = "  << REVIEW" if i.needs_review else ""
            print(f"    -> {i.generic_name:20} {str(i.strength_mg):8}mg  "
                  f"daily={str(i.daily_mg):8}mg  via={i.match_type} ({i.source_alias}){flag}")
        if not ln.ingredients:
            print("    -> NO MATCH — flagged for manual entry")

    print("\n===== DAILY TOTALS PER INGREDIENT =====")
    for t in result.daily_totals.values():
        unk = f"  (+{t['unknown_dose_lines']} line(s) with unknown dose)" if t["unknown_dose_lines"] else ""
        print(f"  {t['generic_name']:20} {t['total_daily_mg']:>8} mg/day   from {t['sources']}{unk}")

    print(f"\n===== COVERAGE =====\n  {result.coverage}\n")

if __name__ == "__main__":
    main()
