"""Phase 1 tests — run WITHOUT the real DDInter files (synthetic fixtures).
Run from backend/:  python -m pytest tests/test_phase1_seed.py -v
"""
import json
import pytest
from app.db import phase1_load as p1

CSV_ROWS = [
    "DDInterID_A,Drug_A,DDInterID_B,Drug_B,Level",
    "D1,Paracetamol,D2,Warfarin,Moderate",
    "D2,Warfarin,D1,Paracetamol,Moderate",   # reversed duplicate -> skipped
    "D3,Ibuprofen,D2,Warfarin,Major",
    "D4,Aspirin,D5,Clopidogrel,Major",
    "D4,Aspirin,D4,Aspirin,Minor",           # self-pair -> skipped
    ",,D2,Warfarin,Major",                   # empty name -> skipped
    "D6,Metformin,D7,Iohexol,Caution",       # unrecognized level -> 'unknown'
]

@pytest.fixture()
def conn(tmp_path):
    c = p1.init_db(db_path=tmp_path / "t.db", fresh=True)
    p1.self_check(c)
    yield c
    c.close()

@pytest.fixture()
def raw_dir(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    (d / "ddinter_downloads_code_T.csv").write_text("\n".join(CSV_ROWS), encoding="utf-8")
    return d

def test_load_counts_and_dedup(conn, raw_dir):
    stats, unknown = p1.load_ddinter(conn, raw_dir=raw_dir)
    assert stats["pairs_accepted"] == 4
    assert stats["duplicates_skipped"] == 1
    assert stats["skipped_self_pair"] == 1
    assert stats["skipped_empty"] == 1
    assert stats["drugs_total"] == 7
    assert unknown == ["caution"]

def test_pairs_canonicalized(conn, raw_dir):
    p1.load_ddinter(conn, raw_dir=raw_dir)
    # FR-24: each pair stored exactly once (no duplicates)
    # Canonical order is by numeric drug_id (a_id < b_id), not alphabetical name
    id_rows = conn.execute(
        f"SELECT {p1.C_IA_A}, {p1.C_IA_B} FROM {p1.T_INTER}").fetchall()
    assert all(a < b for a, b in id_rows), "drug_a_id must always be < drug_b_id (canonical order)"
    assert len(id_rows) == len(set(id_rows)), "No duplicate pairs allowed"
    # paracetamol-warfarin pair must exist (as either name order in the join)
    name_pairs = conn.execute(
        f"SELECT a.{p1.C_DRUG_NAME}, b.{p1.C_DRUG_NAME} FROM {p1.T_INTER} i "
        f"JOIN {p1.T_DRUG} a ON a.{p1.C_DRUG_ID}=i.{p1.C_IA_A} "
        f"JOIN {p1.T_DRUG} b ON b.{p1.C_DRUG_ID}=i.{p1.C_IA_B}").fetchall()
    flat = {n for pair in name_pairs for n in pair}
    assert "paracetamol" in flat and "warfarin" in flat

def test_idempotent_rerun(conn, raw_dir):
    p1.load_ddinter(conn, raw_dir=raw_dir)
    p1.load_ddinter(conn, raw_dir=raw_dir)
    assert conn.execute(f"SELECT COUNT(*) FROM {p1.T_INTER}").fetchone()[0] == 4
    assert conn.execute(f"SELECT COUNT(*) FROM {p1.T_DRUG}").fetchone()[0] == 7

def test_dose_rules(conn, tmp_path):
    f = tmp_path / "dose.json"
    f.write_text(json.dumps([{"rule_id": "DOSE-001", "ingredient": "Paracetamol",
                              "max_daily_mg": 4000, "population": "adult",
                              "note": "hepatotoxicity risk", "source": "curated"}]))
    assert p1.load_dose_rules(conn, path=f) == 1
    assert conn.execute(f"SELECT COUNT(*) FROM {p1.T_DOSE} WHERE {p1.C_DOSE_ID}='DOSE-001'").fetchone()[0] == 1

def test_aliases(conn, tmp_path):
    f = tmp_path / "aliases.json"
    f.write_text(json.dumps([{"alias": "Crocin", "generic": "Paracetamol", "strength_mg": 500}]))
    p1.load_aliases(conn, path=f)
    row = conn.execute(
        f"SELECT d.{p1.C_DRUG_NAME} FROM {p1.T_ALIAS} al "
        f"JOIN {p1.T_DRUG} d ON d.{p1.C_DRUG_ID}=al.{p1.C_ALIAS_FK} "
        f"WHERE al.{p1.C_ALIAS_NAME}='crocin'").fetchone()
    assert row and row[0] == "paracetamol"
