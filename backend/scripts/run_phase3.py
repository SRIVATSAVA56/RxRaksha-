import sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db.phase1_load import DEFAULT_DB
from app.normalize.engine import NormalizeEngine
from app.rules.engine import RulesEngine

CASES = {
    "Case A: clean prescription": {
        "lines": ["Tab Crocin 500mg 1-0-1", "Thyronorm 100mcg OD"],
        "patient": {},
    },
    "Case B: warfarin + duplicate paracetamol (SPEC Case 2)": {
        "lines": ["Warf 5mg OD", "Crocin 500mg 1-0-1", "Dolo-650 1-0-1"],
        "patient": {},
    },
    "Case C: penicillin allergy + kidney impairment + overdose": {
        "lines": ["Augmentin 625 TDS", "Brufen 400mg TDS", "Crocin 500mg QID"],
        "patient": {"allergies": ["penicillin"], "kidney_impairment": True},
    },
    "Case D: unquantified PRN line (fail-safe)": {
        "lines": ["Combiflam SOS", "Crocin 500mg TDS"],
        "patient": {},
    },
}

def main():
    conn = sqlite3.connect(DEFAULT_DB)
    norm_eng = NormalizeEngine(conn)
    rules_eng = RulesEngine(conn)

    for case_name, case in CASES.items():
        print(f"\n{'='*64}\n{case_name}\n{'='*64}")
        norm_rx = norm_eng.normalize_prescription(case["lines"])
        report = rules_eng.analyze(norm_rx, case["patient"])

        if not report.findings:
            print("  No findings.")
        for f in report.findings:
            print(f"\n  [{f.severity.upper():>15}] {f.rule_id}: {f.title}")
            print(f"      mechanism: {f.mechanism}")
            print(f"      evidence : {f.evidence_source} | kb={f.kb_version}")
            for step in f.reasoning_chain:
                print(f"        - {step}")

        print(f"\n  COVERAGE:")
        for c in report.coverage["checks"]:
            print(f"    {c}")

if __name__ == "__main__":
    main()
