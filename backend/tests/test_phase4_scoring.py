"""Phase 4 tests — scoring math + API end-to-end via TestClient."""
import pytest
from fastapi.testclient import TestClient

from app.db import phase1_load as p1
from app.normalize.engine import NormalizeEngine, apply_phase2_migration, load_combos
from app.rules.engine import RulesEngine, Finding, RulesReport
from app.scoring.engine import score as score_report, score, WEIGHTS, CAPS

# ---------- scoring unit tests (temp DB) ----------

@pytest.fixture()
def env(tmp_path):
    conn = p1.init_db(db_path=tmp_path / "t.db", fresh=True)
    p1.load_aliases(conn); p1.load_dose_rules(conn)
    apply_phase2_migration(conn); load_combos(conn)
    cur = conn.execute(f"SELECT {p1.C_DRUG_ID} FROM {p1.T_DRUG} WHERE {p1.C_DRUG_NAME}='paracetamol'")
    pa = cur.fetchone()[0]
    cur_wf = conn.execute(f"SELECT {p1.C_DRUG_ID} FROM {p1.T_DRUG} WHERE {p1.C_DRUG_NAME}='warfarin'").fetchone()
    if cur_wf:
        wf = cur_wf[0]
    else:
        wf = conn.execute(f"INSERT INTO {p1.T_DRUG} ({p1.C_DRUG_NAME}) VALUES ('warfarin')").lastrowid
    conn.execute(
        f"INSERT INTO {p1.T_INTER} ({p1.C_IA_A},{p1.C_IA_B},{p1.C_IA_SEV},{p1.C_IA_SRC},{p1.C_IA_KB}) "
        "VALUES (?,?,?,?,?)", (min(pa, wf), max(pa, wf), "moderate", "test", p1.KB_VERSION))
    conn.commit()
    yield NormalizeEngine(conn), RulesEngine(conn)
    conn.close()

def run(env, lines, patient=None):
    ne, re_ = env
    rx = ne.normalize_prescription(lines)
    rep = re_.analyze(rx, patient or {})
    return score_report(rep, rx.coverage)

def test_clean_prescription_scores_100(env):
    r = run(env, ["Thyronorm 100mcg OD"])
    # 0 deductions -> safe score 100
    assert r.score == 100 and r.band == "Safe" and [l for l in r.ledger if l["delta"] != 0] == []

def test_moderate_pair_caps_at_75(env):
    # warfarin+paracetamol (moderate DDI) + duplicate (moderate) -> deductions 12+12=24 -> raw 76, cap 75
    r = run(env, ["Warf 5mg OD", "Crocin 500mg 1-0-1", "Dolo-650 1-0-1"])
    assert r.score == 75 and r.band == "Caution"
    assert r.cap_applied and "moderate" in r.cap_applied
    assert any(l["source"] == "DDI-001" for l in r.ledger)
    assert any(l["source"] == "DUP-001" for l in r.ledger)

def test_major_finding_caps_at_50(env):
    r = run(env, ["Crocin 500mg QID", "Dolo-650 QID"])   # 4600 > 4000 -> DOSE-001 major
    assert r.score == 50 and r.band == "Warning"
    assert any(l["source"] == "DOSE-001" and l["delta"] == -WEIGHTS["major"] for l in r.ledger)

def test_unmatched_lines_cost_points(env):
    r = run(env, ["Thyronorm 100mcg OD", "Blorfazep 10mg OD"])   # 1 unmatched
    assert r.score == 100 - 3 and r.band == "Safe"
    assert any(l["source"] == "coverage" for l in r.ledger)

def test_skipped_checks_recorded_zero_delta(env):
    r = run(env, ["Thyronorm 100mcg OD"])   # no patient -> allergy+organ skipped
    zeros = [l for l in r.ledger if l["delta"] == 0]
    assert len(zeros) == 2   # allergy + organ, recorded honestly

# ---------- API tests (TestClient, monkeypatched state) ----------

@pytest.fixture()
def client(env, monkeypatch):
    ne, re_ = env
    import app.main as m
    conn = ne.conn
    monkeypatch.setitem(m.state, "conn", conn)
    monkeypatch.setitem(m.state, "normalizer", ne)
    monkeypatch.setitem(m.state, "rules", re_)
    return TestClient(m.app)

def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["interactions"] >= 1

def test_analyze_endpoint_case_b(client):
    body = client.post("/analyze", json={
        "lines": ["Warf 5mg OD", "Crocin 500mg 1-0-1", "Dolo-650 1-0-1"],
        "patient": {}}).json()
    assert body["score"] == 75 and body["band"] == "Caution"
    rules = {f["rule_id"] for f in body["findings"]}
    assert {"DDI-001", "DUP-001"} <= rules
    assert body["findings"][0]["reasoning_chain"]          # explanation contract
    assert body["coverage"]["normalization"]["ok"] == 3
    assert body["disclaimer"]

def test_analyze_rejects_empty(client):
    assert client.post("/analyze", json={"lines": ["", "  "], "patient": {}}).status_code == 400

def test_analyze_patient_allergy_flows_through(client):
    body = client.post("/analyze", json={
        "lines": ["Augmentin 625 TDS"],
        "patient": {"allergies": ["penicillin"]}}).json()
    a = [f for f in body["findings"] if f["rule_id"].startswith("ALLERGY")]
    assert len(a) == 1 and a[0]["severity"] == "major" and body["score"] <= CAPS["major"]


def test_single_major_floored_at_25():
    """Major finding + heavy coverage penalties floors at 25, not Critical.
    raw = 100 - 25(major) - 27(coverage) = 48 -> cap 50 -> raw 48...
    -> force raw < 25 with more unmatched lines; floor rescues to 25."""
    f = Finding(rule_id="DDI-001", category="ddi", severity="major",
                title="Drug-drug interaction: warfarin + aspirin",
                mechanism="Additive bleeding risk", evidence_source="ddinter",
                kb_version="1.0", reasoning_chain=[], ingredients=["warfarin", "aspirin"], data={})
    report = RulesReport(findings=[f], coverage={"status": "partial", "checks": []})
    # 20 unmatched lines: raw = 100 - 25 - 60 = 15 -> floored to 25
    result = score(report, {"unmatched": 20, "review": 0})
    assert result.score == 25
    assert result.band == "High Risk"
    assert any("floored" in l["detail"] for l in result.ledger)

def test_major_alone_caps_at_50():
    """Guard: single major with FULL coverage stays at cap (50/Warning)."""
    f = Finding(rule_id="DDI-001", category="ddi", severity="major",
                title="Drug-drug interaction: warfarin + aspirin",
                mechanism="Additive bleeding risk", evidence_source="ddinter",
                kb_version="1.0", reasoning_chain=[], ingredients=["warfarin", "aspirin"], data={})
    report = RulesReport(findings=[f], coverage={"status": "full", "checks": []})
    result = score(report, {"unmatched": 0, "review": 0})
    assert result.score == 50
    assert result.band == "Warning"

def test_contraindicated_still_critical():
    """Contraindicated finding(s) can reach 0/Critical — floor must NOT rescue."""
    fs = [Finding(rule_id="ALLERGY-001", category="allergy", severity="contraindicated",
                  title="Allergy: penicillin", mechanism="IgE-mediated",
                  evidence_source="seed", kb_version="1.0", reasoning_chain=[],
                  ingredients=["amoxicillin"], data={})
          for _ in range(3)]
    report = RulesReport(findings=fs, coverage={"status": "full", "checks": []})
    result = score(report, {"unmatched": 0, "review": 0})
    # raw = 100 - 120 = 0, capped at 25 -> 0. Floor skips (worst != major).
    assert result.score == 0
    assert result.band == "Critical"


