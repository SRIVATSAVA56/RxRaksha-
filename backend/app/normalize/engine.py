"""Phase 2: normalization engine.

raw line -> parse (strength, frequency) -> resolve (combo/alias/generic/fuzzy)
-> canonical ingredients with per-day mg. Deterministic: no AI anywhere.
Unmatched lines are flagged, never guessed (fail-safe).
"""
from __future__ import annotations
import json, re, sqlite3
from collections import Counter
from dataclasses import dataclass, field
from difflib import get_close_matches, SequenceMatcher
from pathlib import Path

from app.db.phase1_load import (
    BACKEND, SEED_DIR, T_DRUG, C_DRUG_ID, C_DRUG_NAME,
    T_ALIAS, C_ALIAS_FK, C_ALIAS_NAME, norm, _drug_id,
)

COMBO_SQL = BACKEND / "sql" / "phase2_combo.sql"
COMBO_TABLE = "combo_component"

# ---------- parsing ----------

STRENGTH_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(mg|gm|g|mcg)\b", re.IGNORECASE)
UNIT_TO_MG = {"mg": 1.0, "gm": 1000.0, "g": 1000.0, "mcg": 0.001}
XYZ_RE = re.compile(r"^\d(?:-\d){2,3}$")   # 1-0-1, 1-1-1-1 — match longest first (4-segment before 3)
QH_RE  = re.compile(r"^q(\d+)h$")          # q8h -> 3/day

# Longest-key-first ordering avoids "1-1-1-1" matching "1-1-1" (fix #1 from v1.3)
_FREQ_RAW = {
    "four times daily": 4, "thrice daily": 3, "twice daily": 2, "once daily": 1,
    "qid": 4, "tid": 3, "tds": 3, "bd": 2, "bid": 2, "od": 1, "qd": 1,
    "once": 1, "daily": 1, "hs": 1, "nocte": 1, "at bedtime": 1,
    "sos": None, "stat": 1, "as needed": None,
    "weekly": round(1 / 7, 6),
}
# sort descending by token length so multi-word phrases matched before sub-phrases
FREQ_PHRASES = sorted(_FREQ_RAW.items(), key=lambda kv: len(kv[0]), reverse=True)

FORM_TOKENS = {
    "tab", "tabs", "tablet", "tablets", "cap", "caps", "capsule",
    "capsules", "syp", "syr", "syrup", "inj", "injection", "drops",
}


@dataclass
class ParsedLine:
    raw: str
    name_candidate: str
    strength_mg: float | None
    doses_per_day: float | None


def _parse_frequency(text: str):
    """Return (doses_per_day, remaining_text). None means PRN/unspecified."""
    # 1. XYZ notation — longest pattern first: 4-segment before 3-segment
    for tok in text.split():
        t = tok.strip(".,;")
        if XYZ_RE.fullmatch(t):
            return float(sum(int(d) for d in re.findall(r"\d", t))), text.replace(tok, " ")
    # 2. q<n>h patterns
    for tok in text.split():
        t = tok.strip(".,;")
        qh = QH_RE.fullmatch(t)
        if qh:
            return round(24 / int(qh.group(1)), 4), text.replace(tok, " ")
    # 3. Multi-word then single-word FREQ phrases (longest first)
    lower = text.lower()
    for phrase, val in FREQ_PHRASES:
        if phrase in lower:
            return val, lower.replace(phrase, " ")
    return None, text


def parse_line(raw: str) -> ParsedLine:
    text = norm(raw)
    # Extract strength
    strength = None
    m = STRENGTH_RE.search(text)
    if m:
        strength = float(m.group(1)) * UNIT_TO_MG[m.group(2).lower()]
        text = text[:m.start()] + " " + text[m.end():]

    # Extract frequency
    doses, text = _parse_frequency(text)

    # Strip form tokens from the front
    parts = [p for p in text.split() if p.strip(".,;")]
    if parts and parts[0] in FORM_TOKENS:
        parts = parts[1:]

    return ParsedLine(raw=raw, name_candidate=" ".join(parts),
                      strength_mg=strength, doses_per_day=doses)


# ---------- resolution ----------

@dataclass
class Match:
    kind: str                     # alias | generic | combo
    key: str
    confidence: float
    rows: list                    # [(drug_id, default_strength_mg), ...]
    match_type: str
    inferred_strength_mg: float | None = None
    needs_review: bool = False


@dataclass
class Ingredient:
    drug_id: int
    generic_name: str
    strength_mg: float | None
    daily_mg: float | None
    source_alias: str
    match_type: str
    confidence: float
    needs_review: bool


@dataclass
class NormalizedLine:
    raw: str
    parsed: ParsedLine
    ingredients: list = field(default_factory=list)
    status: str = "ok"   # ok | review | unmatched


@dataclass
class NormalizedPrescription:
    lines: list
    daily_totals: dict   # drug_id -> {generic_name, total_daily_mg, unknown_dose_lines, sources}
    coverage: dict


class NormalizeEngine:
    def __init__(self, conn: sqlite3.Connection, fuzzy_cutoff: float = 0.80):
        self.conn = conn
        self.cutoff = fuzzy_cutoff
        self.alias_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({T_ALIAS})")}
        self.has_combo = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (COMBO_TABLE,)).fetchone() is not None
        self._name_cache: dict[int, str] = {}
        self.vocab = self._build_vocab()
        self.vocab_keys = list(self.vocab.keys())

    # -- lookups --
    def _build_vocab(self):
        v = {}
        for (n,) in self.conn.execute(f"SELECT {C_ALIAS_NAME} FROM {T_ALIAS}"):
            v[n] = "alias"
        for (n,) in self.conn.execute(f"SELECT {C_DRUG_NAME} FROM {T_DRUG}"):
            v.setdefault(n, "generic")
        if self.has_combo:
            for (n,) in self.conn.execute(f"SELECT DISTINCT combo_alias_name FROM {COMBO_TABLE}"):
                v.setdefault(n, "combo")
        return v

    def _name_of(self, drug_id: int) -> str:
        if drug_id not in self._name_cache:
            row = self.conn.execute(
                f"SELECT {C_DRUG_NAME} FROM {T_DRUG} WHERE {C_DRUG_ID}=?", (drug_id,)).fetchone()
            self._name_cache[drug_id] = row[0] if row else f"drug#{drug_id}"
        return self._name_cache[drug_id]

    def _alias(self, key):
        scol = "strength_mg" if "strength_mg" in self.alias_cols else "NULL"
        row = self.conn.execute(
            f"SELECT {C_ALIAS_FK}, {scol} FROM {T_ALIAS} WHERE {C_ALIAS_NAME}=?", (key,)).fetchone()
        return [tuple(row)] if row else None

    def _generic(self, key):
        row = self.conn.execute(
            f"SELECT {C_DRUG_ID}, NULL FROM {T_DRUG} WHERE {C_DRUG_NAME}=?", (key,)).fetchone()
        return [tuple(row)] if row else None

    def _combo(self, key):
        if not self.has_combo:
            return None
        rows = self.conn.execute(
            f"SELECT drug_id, strength_mg FROM {COMBO_TABLE} WHERE combo_alias_name=?",
            (key,)).fetchall()
        return [tuple(r) for r in rows] or None

    @staticmethod
    def _keys(name: str, strength: float | None):
        """Ordered lookup keys. 'Pan 40' -> also tries 'pan-40', 'pan'."""
        ks, seen = [], set()

        def add(k):
            nk = norm(k)
            if nk and nk not in seen:
                seen.add(nk); ks.append(nk)

        add(name)
        if strength is not None:
            s = int(strength) if float(strength).is_integer() else strength
            add(f"{name}-{s}")
            add(f"{name} {s}")

        # strip trailing number from name itself (e.g. "dolo-650" -> base="dolo", num="650")
        m = re.match(r"^(.*?)[\s\-](\d+(?:\.\d+)?)$", name.strip())
        if m:
            base = m.group(1).strip(" -")
            num  = m.group(2)
            if base:
                add(f"{base}-{num}")
                add(f"{base} {num}")
                add(base)

        return ks

    def resolve(self, name: str, strength: float | None) -> Match | None:
        # Try to infer embedded strength from name ("dolo-650" -> 650)
        inferred = None
        m = re.match(r"^(.*?)[\s\-](\d+(?:\.\d+)?)$", name.strip())
        if m and m.group(1).strip(" -"):
            inferred = float(m.group(2))

        for key in self._keys(name, strength):
            if rows := self._combo(key):
                return Match("combo", key, 1.0, rows, "combo")
            if rows := self._alias(key):
                return Match("alias", key, 1.0, rows, "exact_alias",
                             inferred_strength_mg=inferred)
        if rows := self._generic(norm(name)):
            return Match("generic", norm(name), 1.0, rows, "generic")

        hits = get_close_matches(norm(name), self.vocab_keys, n=1, cutoff=self.cutoff)
        if not hits:
            return None
        key = hits[0]
        kind = self.vocab[key]
        conf = SequenceMatcher(None, norm(name), key).ratio()
        rows = {"alias": self._alias, "generic": self._generic, "combo": self._combo}[kind](key)
        if not rows:
            return None
        return Match(kind, key, round(conf, 3), rows, f"fuzzy({kind})",
                     inferred_strength_mg=inferred, needs_review=True)

    # -- public API --
    def normalize_line(self, raw: str) -> NormalizedLine:
        p = parse_line(raw)
        if not p.name_candidate:
            return NormalizedLine(raw=raw, parsed=p, status="unmatched")
        m = self.resolve(p.name_candidate, p.strength_mg)
        if m is None:
            return NormalizedLine(raw=raw, parsed=p, status="unmatched")

        ings = []
        for drug_id, comp_strength in m.rows:
            if m.kind == "combo":
                # Combo: component strengths are authoritative — line strength ignored
                s = comp_strength
            else:
                # Single: prefer explicit parsed strength, then embedded, then alias seed
                s = p.strength_mg
                if s is None:
                    s = m.inferred_strength_mg
                if s is None:
                    s = comp_strength

            daily = round(s * p.doses_per_day, 4) \
                if (s is not None and p.doses_per_day is not None) else None
            needs = m.needs_review or daily is None
            ings.append(Ingredient(
                drug_id=drug_id, generic_name=self._name_of(drug_id),
                strength_mg=s, daily_mg=daily, source_alias=m.key,
                match_type=m.match_type, confidence=m.confidence,
                needs_review=needs))

        status = "review" if any(i.needs_review for i in ings) else "ok"
        return NormalizedLine(raw=raw, parsed=p, ingredients=ings, status=status)

    def normalize_prescription(self, lines: list[str]) -> NormalizedPrescription:
        normed = [self.normalize_line(l) for l in lines if l and l.strip()]
        totals: dict = {}
        for ln in normed:
            for ing in ln.ingredients:
                e = totals.setdefault(ing.drug_id, {
                    "generic_name": ing.generic_name,
                    "total_daily_mg": 0.0,
                    "unknown_dose_lines": 0,
                    "sources": [],
                })
                e["sources"].append(ing.source_alias)
                if ing.daily_mg is None:
                    e["unknown_dose_lines"] += 1
                else:
                    e["total_daily_mg"] += ing.daily_mg
        st = Counter(l.status for l in normed)
        coverage = {
            "lines_total": len(normed),
            "ok":          st.get("ok", 0),
            "review":      st.get("review", 0),
            "unmatched":   st.get("unmatched", 0),
            "doses_known":   sum(1 for l in normed for i in l.ingredients if i.daily_mg is not None),
            "doses_unknown": sum(1 for l in normed for i in l.ingredients if i.daily_mg is None),
        }
        return NormalizedPrescription(lines=normed, daily_totals=totals, coverage=coverage)


# ---------- migration + combo seed loading ----------

def apply_phase2_migration(conn: sqlite3.Connection, path=COMBO_SQL):
    conn.executescript(Path(path).read_text(encoding="utf-8"))
    conn.commit()


def load_combos(conn: sqlite3.Connection, path=SEED_DIR / "combo_products.json") -> int:
    items = json.loads(Path(path).read_text(encoding="utf-8"))
    cache, n = {}, 0
    with conn:
        for item in items:
            key = norm(item["alias"])
            comps = item.get("components") or []
            if len(comps) < 2:
                raise ValueError(f"combo '{key}' needs >=2 components")
            for comp in comps:
                did = _drug_id(conn, norm(comp["generic"]), cache)
                conn.execute(
                    f"INSERT OR IGNORE INTO {COMBO_TABLE} (combo_alias_name, drug_id, strength_mg) "
                    "VALUES (?,?,?)", (key, did, comp.get("strength_mg")))
                n += 1
    return n
