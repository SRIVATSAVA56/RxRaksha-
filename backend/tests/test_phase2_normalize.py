"""Phase 2 tests — temp DB, real seed files, no DDInter CSVs needed."""
import pytest
from app.db import phase1_load as p1
from app.normalize.engine import (
    NormalizeEngine, apply_phase2_migration, load_combos, parse_line,
)

@pytest.fixture()
def eng(tmp_path):
    conn = p1.init_db(db_path=tmp_path / "t.db", fresh=True)
    p1.load_aliases(conn)                       # real alias seed
    apply_phase2_migration(conn)
    load_combos(conn)                           # real combo seed
    yield NormalizeEngine(conn)
    conn.close()

def one(eng, line):
    return eng.normalize_prescription([line])

def test_exact_alias_daily_math(eng):
    ln = one(eng, "Tab Crocin 500mg 1-0-1").lines[0]
    assert ln.status == "ok"
    i = ln.ingredients[0]
    assert i.generic_name == "paracetamol" and i.strength_mg == 500 and i.daily_mg == 1000

def test_brand_with_embedded_strength(eng):
    ln = one(eng, "Dolo-650 BD").lines[0]
    i = ln.ingredients[0]
    assert i.generic_name == "paracetamol" and i.strength_mg == 650 and i.daily_mg == 1300

def test_space_variant_of_brand_strength(eng):
    ln = one(eng, "Dolo 650mg BD").lines[0]     # unit form
    assert ln.ingredients[0].generic_name == "paracetamol"
    ln2 = one(eng, "Pan 40 OD").lines[0]        # bare number form -> inferred
    i = ln2.ingredients[0]
    assert i.generic_name == "pantoprazole" and i.strength_mg == 40

def test_generic_direct(eng):
    ln = one(eng, "Warfarin 5mg OD").lines[0]
    assert ln.ingredients[0].generic_name == "warfarin" and ln.status == "ok"

def test_combo_expansion(eng):
    ln = one(eng, "Augmentin 625 TDS").lines[0]
    by_name = {i.generic_name: i for i in ln.ingredients}
    assert set(by_name) == {"amoxicillin", "clavulanic acid"}
    assert by_name["amoxicillin"].daily_mg == 1500
    assert by_name["clavulanic acid"].daily_mg == 375

def test_combo_no_strength_uses_seed(eng):
    ln = one(eng, "Combiflam BD").lines[0]
    by_name = {i.generic_name: i for i in ln.ingredients}
    assert by_name["ibuprofen"].daily_mg == 800
    assert by_name["paracetamol"].daily_mg == 650

def test_unit_conversions(eng):
    assert one(eng, "Thyronorm 100mcg OD").lines[0].ingredients[0].daily_mg == pytest.approx(0.1)
    assert one(eng, "Metformin 1g BD").lines[0].ingredients[0].daily_mg == 2000

@pytest.mark.parametrize("tok,expected", [
    ("OD", 1), ("BD", 2), ("TDS", 3), ("QID", 4),
    ("q8h", 3), ("q6h", 4), ("1-0-1", 2), ("1-1-1", 3),
])
def test_frequency_parsing(tok, expected):
    assert parse_line(f"Crocin 500mg {tok}").doses_per_day == expected

def test_fuzzy_ocr_typo_flagged(eng):
    ln = one(eng, "Krocin 500mg OD").lines[0]
    assert ln.status == "review"
    assert ln.ingredients[0].generic_name == "paracetamol"
    assert ln.ingredients[0].match_type.startswith("fuzzy")

def test_unmatched_line_flagged_not_guessed(eng):
    ln = one(eng, "Blorfazep 10mg OD").lines[0]
    assert ln.status == "unmatched" and ln.ingredients == []

def test_missing_frequency_flags_review(eng):
    ln = one(eng, "Crocin 500mg").lines[0]
    assert ln.status == "review" and ln.ingredients[0].daily_mg is None

def test_duplicate_brand_aggregation(eng):
    res = eng.normalize_prescription(["Crocin 500mg 1-0-1", "Dolo-650 1-0-1"])
    para = next(t for t in res.daily_totals.values() if t["generic_name"] == "paracetamol")
    assert para["total_daily_mg"] == 2300 and len(para["sources"]) == 2
    assert res.coverage["lines_total"] == 2 and res.coverage["ok"] == 2
