"""Phase 5 tests — templates present, D10 gate airtight."""
import pytest
from fastapi.testclient import TestClient

from app.db import phase1_load as p1
from app.normalize.engine import NormalizeEngine, apply_phase2_migration, load_combos
from app.rules.engine import RulesEngine
from app.explain import summarizer

@pytest.fixture()
def env(tmp_path):
    conn = p1.init_db(db_path=tmp_path / "t.db", fresh=True)
    p1.load_aliases(conn); p1.load_dose_rules(conn)
    apply_phase2_migration(conn); load_combos(conn)
    pa = conn.execute(f"SELECT {p1.C_DRUG_ID} FROM {p1.T_DRUG} WHERE {p1.C_DRUG_NAME}='paracetamol'").fetchone()[0]
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

@pytest.fixture()
def client(env, monkeypatch):
    import app.main as m
    ne, re_ = env
    monkeypatch.setitem(m.state, "conn", ne.conn)
    monkeypatch.setitem(m.state, "normalizer", ne)
    monkeypatch.setitem(m.state, "rules", re_)
    from app.auth import create_token
    c = TestClient(m.app)
    c.headers["Authorization"] = f"Bearer {create_token('demo:demo-user')}"
    return c

CASE_B = {"lines": ["Warf 5mg OD", "Crocin 500mg 1-0-1", "Dolo-650 1-0-1"], "patient": {}}

# ---- templates ----
def test_explanations_and_summary_present(client):
    body = client.post("/analyze", json=CASE_B).json()
    assert body["summary"] and "Risk score 25/100" in body["summary"]
    assert len(body["explanations"]) == 2
    heads = {e["rule_id"]: e["headline"] for e in body["explanations"]}
    assert "Drug–drug interaction" in heads["DDI-001"]
    assert "Duplicate therapy" in heads["DUP-001"]
    assert body["ai_summary"] is None                       # never present by default

def test_summary_reports_skipped_checks(client):
    body = client.post("/analyze", json=CASE_B).json()
    assert "Not assessed: allergy, organ" in body["summary"]

# ---- D10 gate ----
def test_unverified_draft_not_attached(client):
    report = client.post("/analyze", json=CASE_B).json()
    res = client.post("/analyze/summary", json={"report": report}).json()
    assert res["report"]["ai_summary"] is None              # report untouched
    draft = res["ai_draft_unverified"]
    assert draft["verified"] is False and "UNVERIFIED" in draft["text"]

def test_verified_draft_attaches(client):
    report = client.post("/analyze", json=CASE_B).json()
    res = client.post("/analyze/summary", json={
        "report": report, "verifier_id": "PHARM-1143"}).json()
    ai = res["report"]["ai_summary"]
    assert ai and ai["verified"] is True and ai["verified_by"] == "PHARM-1143"
    assert ai["method"] == "manual_review"

def test_tampered_verification_rejected():
    draft = summarizer.generate("Safety score 75/100.")
    v = summarizer.Verification(verifier_id="PHARM-1").sign(draft)
    draft.text = "tampered text"                            # modify after signing
    with pytest.raises(ValueError, match="does not match"):
        summarizer.attach_verified({}, draft, v)

def test_no_verification_no_attachment():
    draft = summarizer.generate("Safety score 75/100.")
    report = {"score": 75}
    out = summarizer.attach_verified(report, draft, None)
    assert "ai_summary" not in out                          # gate holds
