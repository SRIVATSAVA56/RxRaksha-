import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import app.main as m
from app.db.phase1_load import DEFAULT_DB
from app.normalize.engine import NormalizeEngine
from app.rules.engine import RulesEngine

CASES = {
    "Case A (clean)": {"lines": ["Tab Crocin 500mg 1-0-1", "Thyronorm 100mcg OD"], "patient": {}},
    "Case B (SPEC 2)": {"lines": ["Warf 5mg OD", "Crocin 500mg 1-0-1", "Dolo-650 1-0-1"], "patient": {}},
    "Case C (allergy+kidney+OD)": {
        "lines": ["Augmentin 625 TDS", "Brufen 400mg TDS", "Crocin 500mg QID", "Dolo-650 QID"],
        "patient": {"allergies": ["penicillin"], "kidney_impairment": True}},
    "Case D (SOS fail-safe)": {"lines": ["Combiflam SOS", "Crocin 500mg TDS"], "patient": {}},
}

def main():
    conn = sqlite3.connect(DEFAULT_DB, check_same_thread=False)
    m.state.update(conn=conn, normalizer=NormalizeEngine(conn), rules=RulesEngine(conn))
    c = TestClient(m.app)

    print("HEALTH:", json.dumps(c.get("/health").json(), indent=2))
    for name, payload in CASES.items():
        body = c.post("/analyze", json=payload).json()
        print(f"\n{'='*64}\n{name}\n{'='*64}")
        print(f"  SCORE: {body['score']}/100  [{body['band']}]  -> {body['action']}")
        if body["cap_applied"]:
            print(f"  CAP: {body['cap_applied']}")
        for f in body["findings"]:
            print(f"    [{f['severity'].upper():>15}] {f['rule_id']}: {f['title']}")
        print("  LEDGER:")
        for l in body["score_ledger"]:
            print(f"    {l['delta']:>4}  {l['source']}: {l['detail']}")

if __name__ == "__main__":
    main()
