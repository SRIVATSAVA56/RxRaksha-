"""Phase 1: schema init + seed loading (DDInter CSVs, dose rules, brand aliases).

Idempotent: safe to re-run. Single transaction per loader: all-or-nothing.
Dedup: interaction pairs canonicalized (A,B) == (B,A) before insert (FR-24).
"""
from __future__ import annotations
import csv, json, sqlite3
from collections import Counter
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
DEFAULT_DB = BACKEND / "data" / "rxraksha.db"
SCHEMA_SQL = BACKEND / "sql" / "sqlite.sql"
RAW_DIR = BACKEND / "data" / "raw" / "ddinter"
SEED_DIR = BACKEND / "data" / "seed"

KB_VERSION = "ddinter-2.0"
EVIDENCE_SOURCE = "DDInter 2.0 bulk download"

# ---- CONFIG: edit HERE if sqlite.sql used different names ----
T_DRUG, C_DRUG_ID, C_DRUG_NAME = "drug", "drug_id", "generic_name"
T_ALIAS, C_ALIAS_FK, C_ALIAS_NAME = "drug_alias", "drug_id", "alias_name"
T_INTER, C_IA_A, C_IA_B, C_IA_SEV = "interaction", "drug_a_id", "drug_b_id", "severity"
C_IA_SRC, C_IA_KB = "evidence_source", "kb_version"
T_DOSE, C_DOSE_ID, C_DOSE_FK, C_DOSE_MAX = "dose_rule", "rule_id", "drug_id", "max_daily_mg"

EXPECTED_COLS = {
    T_DRUG: {C_DRUG_ID, C_DRUG_NAME},
    T_ALIAS: {C_ALIAS_FK, C_ALIAS_NAME},
    T_INTER: {C_IA_A, C_IA_B, C_IA_SEV, C_IA_SRC, C_IA_KB},
    T_DOSE: {C_DOSE_ID, C_DOSE_MAX},
}

LEVEL_MAP = {  # DDInter Level -> internal severity
    "major": "major", "moderate": "moderate", "minor": "minor",
    "unknown": "unknown", "contraindicated": "contraindicated",
    "not recommended": "contraindicated",
}

HEADER_ALIASES = {
    "drug_a": {"drug_a", "druga", "drug_1", "drug1"},
    "drug_b": {"drug_b", "drugb", "drug_2", "drug2"},
    "level": {"level", "severity", "interaction_level"},
}


def init_db(db_path=DEFAULT_DB, fresh=False, check_same_thread=False):
    db_path = Path(db_path)
    if fresh and db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()
    return conn


def self_check(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = set(EXPECTED_COLS) - tables
    if missing:
        raise SystemExit(f"SCHEMA MISMATCH: missing tables {missing}. Found: {sorted(tables)}. Fix CONFIG block.")
    for t, needed in EXPECTED_COLS.items():
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        if miss := (needed - cols):
            raise SystemExit(f"SCHEMA MISMATCH: {t} missing columns {miss}; has {sorted(cols)}. Fix CONFIG block.")


def norm(s: str) -> str:
    return " ".join((s or "").split()).lower()


def _canon(h: str) -> str:
    return "_".join((h or "").strip().lower().split())


def _resolve_headers(fieldnames, fname):
    canon = {_canon(h): h for h in (fieldnames or [])}
    out = {}
    for key, alts in HEADER_ALIASES.items():
        for a in alts:
            if a in canon:
                out[key] = canon[a]; break
    if miss := set(HEADER_ALIASES) - set(out):
        raise ValueError(f"{fname.name}: cannot find columns {miss}; header was {fieldnames}")
    return out


def _drug_id(conn, name, cache):
    if name in cache:
        return cache[name]
    row = conn.execute(f"SELECT {C_DRUG_ID} FROM {T_DRUG} WHERE {C_DRUG_NAME}=?", (name,)).fetchone()
    if row:
        cache[name] = row[0]; return row[0]
    cur = conn.execute(f"INSERT INTO {T_DRUG} ({C_DRUG_NAME}) VALUES (?)", (name,))
    cache[name] = cur.lastrowid
    return cur.lastrowid


def _flush(conn, batch, cache):
    rows = []
    for lo, hi, sev in batch:
        a_id = _drug_id(conn, lo, cache)
        b_id = _drug_id(conn, hi, cache)
        # Re-sort by numeric ID so CHECK (drug_a_id < drug_b_id) is always satisfied
        if a_id > b_id:
            a_id, b_id = b_id, a_id
        rows.append((a_id, b_id, sev, EVIDENCE_SOURCE, KB_VERSION))
    conn.executemany(
        f"INSERT OR IGNORE INTO {T_INTER} ({C_IA_A},{C_IA_B},{C_IA_SEV},{C_IA_SRC},{C_IA_KB}) VALUES (?,?,?,?,?)",
        rows)


def load_ddinter(conn, raw_dir=RAW_DIR, batch_size=5000):
    files = sorted(Path(raw_dir).glob("ddinter_downloads_code_*.csv"))
    if not files:
        raise FileNotFoundError(f"No DDInter CSVs found in {raw_dir}")
    stats, unknown_levels = Counter(), set()
    cache, seen = {}, set()
    with conn:  # single transaction
        for f in files:
            with f.open(newline="", encoding="utf-8-sig") as fh:
                reader = csv.DictReader(fh)
                km = _resolve_headers(reader.fieldnames, f)
                batch = []
                for row in reader:
                    stats["rows_read"] += 1
                    a, b = norm(row.get(km["drug_a"], "")), norm(row.get(km["drug_b"], ""))
                    if not a or not b:
                        stats["skipped_empty"] += 1; continue
                    lo, hi = (a, b) if a <= b else (b, a)
                    if lo == hi:
                        stats["skipped_self_pair"] += 1; continue
                    if (lo, hi) in seen:
                        stats["duplicates_skipped"] += 1; continue
                    seen.add((lo, hi))
                    raw = (row.get(km["level"], "") or "").strip().lower()
                    sev = LEVEL_MAP.get(raw)
                    if sev is None:
                        unknown_levels.add(raw or "<blank>"); sev = "unknown"
                    batch.append((lo, hi, sev))
                    stats["pairs_accepted"] += 1
                    if len(batch) >= batch_size:
                        _flush(conn, batch, cache); batch.clear()
                if batch:
                    _flush(conn, batch, cache)
            stats["files_loaded"] += 1
    stats["drugs_total"] = conn.execute(f"SELECT COUNT(*) FROM {T_DRUG}").fetchone()[0]
    stats["interactions_total"] = conn.execute(f"SELECT COUNT(*) FROM {T_INTER}").fetchone()[0]
    return stats, sorted(unknown_levels)


def load_dose_rules(conn, path=SEED_DIR / "dose_rules.json"):
    rules = json.loads(Path(path).read_text(encoding="utf-8"))
    dose_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({T_DOSE})")}
    use_fk = C_DOSE_FK in dose_cols
    cache, n = {}, 0
    with conn:
        for r in rules:
            if miss := ({"rule_id", "ingredient", "max_daily_mg"} - set(r)):
                raise ValueError(f"dose rule missing keys {miss}: {r}")
            target = _drug_id(conn, norm(r["ingredient"]), cache) if use_fk else norm(r["ingredient"])
            values = {C_DOSE_ID: r["rule_id"], (C_DOSE_FK if use_fk else "ingredient"): target,
                      C_DOSE_MAX: r["max_daily_mg"], "population": r.get("population", "adult"),
                      "note": r.get("note", ""), "source": r.get("source", "")}
            values = {k: v for k, v in values.items() if k in dose_cols}
            conn.execute(f"INSERT OR IGNORE INTO {T_DOSE} ({','.join(values)}) VALUES ({','.join('?' * len(values))})",
                         tuple(values.values()))
            n += 1
    return n


def load_aliases(conn, path=SEED_DIR / "drug_aliases.json"):
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({T_ALIAS})")}
    cache, n = {}, 0
    with conn:
        for it in items:
            values = {C_ALIAS_FK: _drug_id(conn, norm(it["generic"]), cache),
                      C_ALIAS_NAME: norm(it["alias"]),
                      "strength_mg": it.get("strength_mg"), "source": it.get("source", "seed")}
            values = {k: v for k, v in values.items() if k in cols and v is not None}
            conn.execute(f"INSERT OR IGNORE INTO {T_ALIAS} ({','.join(values)}) VALUES ({','.join('?' * len(values))})",
                         tuple(values.values()))
            n += 1
    return n
