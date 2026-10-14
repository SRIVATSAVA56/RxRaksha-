"""Phase 3 tests — temp DB, real seeds. No DDInter CSVs needed;
a small synthetic interaction pair is inserted directly."""
import pytest
from app.db import phase1_load as p1
from app.normalize.engine import NormalizeEngine, apply_phase2_migration, load_combos
from app.rules.engine import RulesEngine, SEVERITY_ORDER

@pytest.fixture()
def env(tmp_path):
    conn = p1.init_db(db_path=tmp_path / "t.db", fresh=True)
    p1.load_aliases(conn)
    p1.load_dose_rules(conn)
    apply_phase2_migration(conn)
    load_combos(conn)

    # synthetic interaction pair: paracetamol <-> warfarin (moderate)
    cur = conn.execute(f"SELECT {p1.C_DRUG_ID} FROM {p1.T_DRUG} WHERE {p1.C_DRUG_NAME}='paracetamol'")
    pa = cur.fetchone()[0]
    cur_wf = conn.execute(f"SELECT {p1.C_DRUG_ID} FROM {p1.T_DRUG} WHERE {p1.C_DRUG_NAME}='warfarin'").fetchone()
    if cur_wf:
        wf = cur_wf[0]
    else:
        wf = conn.execute(f"INSERT INTO {p1.T_DRUG} ({p1.C_DRUG_NAME}) VALUES ('warfarin')").lastrowid
    lo, hi = min(pa, wf), max(pa, wf)
    conn.execute(
        f"INSERT INTO {p1.T_INTER} ({p1.C_IA_A},{p1.C_IA_B},{p1.C_IA_SEV},{p1.C_IA_SRC},{p1.C_IA_KB}) "
        "VALUES (?,?,?,?,?)", (lo, hi, "moderate", "test", p1.KB_VERSION))
    conn.commit()
    yield NormalizeEngine(conn), RulesEngine(conn)
    conn.close()

def analyze(env, lines, patient=None):
    ne, re_ = env
    return re_.analyze(ne.normalize_prescription(lines), patient or {})

def by_rule(report, prefix):
    return [f for f in report.findings if f.rule_id.startswith(prefix)]

# ---- DDI ----
def test_ddi_fires(env):
    rep = analyze(env, ["Warf 5mg OD", "Crocin 500mg OD"])
    ddis = by_rule(rep, "DDI")
    assert len(ddis) == 1
    f = ddis[0]
    assert f.severity == "moderate"
    assert set(f.ingredients) == {"paracetamol", "warfarin"}
    assert f.kb_version and f.evidence_source
    assert len(f.reasoning_chain) >= 3

def test_ddi_silent_when_no_pair(env):
    rep = analyze(env, ["Crocin 500mg OD", "Thyronorm 100mcg OD"])
    assert by_rule(rep, "DDI") == []

# ---- DUP ----
def test_duplicate_two_brands(env):
    rep = analyze(env, ["Crocin 500mg 1-0-1", "Dolo-650 1-0-1"])
    dups = by_rule(rep, "DUP")
    assert len(dups) == 1
    assert dups[0].data["total_daily_mg"] == 2300
    assert set(dups[0].data["sources"]) == {"crocin", "dolo-650"}

def test_duplicate_via_combo(env):
    rep = analyze(env, ["Combiflam BD", "Brufen 400mg OD"])   # both give ibuprofen
    assert len(by_rule(rep, "DUP")) == 1

# ---- ALLERGY ----
def test_allergy_penicillin_flags_augmentin(env):
    rep = analyze(env, ["Augmentin 625 TDS"], {"allergies": ["penicillin"]})
    a = by_rule(rep, "ALLERGY")
    assert len(a) == 1 and "amoxicillin" in a[0].ingredients and a[0].severity == "major"

def test_allergy_skipped_when_no_profile(env):
    rep = analyze(env, ["Augmentin 625 TDS"])
    assert by_rule(rep, "ALLERGY") == []
    cov = next(c for c in rep.coverage["checks"] if c["check"] == "allergy")
    assert cov["ran"] is False and "not provided" in cov["reason"]

# ---- ORGAN ----
def test_organ_kidney_flags_nsaid_and_metformin(env):
    rep = analyze(env, ["Brufen 400mg TDS", "Glycomet 500mg BD"],
                  {"kidney_impairment": True})
    o = by_rule(rep, "ORGAN")
    assert len(o) == 1 and set(o[0].ingredients) == {"ibuprofen", "metformin"}

# ---- DOSE ----
def test_dose_over_limit(env):
    rep = analyze(env, ["Crocin 500mg QID", "Dolo-650 BD"])   # 2000+1300=3300? no: QID=4x500=2000, BD=2x650=1300 -> 3300 < 4000
    assert by_rule(rep, "DOSE-001") == []                     # under limit -> silent
    rep2 = analyze(env, ["Crocin 500mg QID", "Dolo-650 QID"]) # 2000+2600=4600 > 4000
    d = by_rule(rep2, "DOSE-001")
    assert len(d) == 1
    assert d[0].data["total_daily_mg"] == 4600 and d[0].data["limit_mg"] == 4000
    assert d[0].data["overage_mg"] == 600 and d[0].severity == "major"

def test_dose_review_when_unquantified(env):
    rep = analyze(env, ["Combiflam SOS", "Crocin 500mg TDS"]) # 1500 known + SOS unknown
    r = by_rule(rep, "DOSE-REVIEW")
    assert len(r) == 1 and r[0].severity == "unknown"
    assert r[0].data["unknown_lines"] == 1

# ---- ordering ----
def test_findings_sorted_by_severity(env):
    rep = analyze(env, ["Augmentin 625 TDS", "Crocin 500mg QID", "Dolo-650 QID", "Warf 5mg OD"],
                  {"allergies": ["penicillin"]})
    sevs = [SEVERITY_ORDER[f.severity] for f in rep.findings]
    assert sevs == sorted(sevs, reverse=True)
