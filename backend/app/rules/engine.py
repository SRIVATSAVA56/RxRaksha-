"""Phase 3: clinical rules engine.

Five deterministic checkers over a NormalizedPrescription:
  DDI      - drug-drug interactions from the DDInter-seeded table (FR-22..24)
  DUP      - duplicate therapy: same ingredient from multiple brands
  ALLERGY  - patient allergy vs ingredient class rules
  ORGAN    - kidney/liver contraindication rules
  DOSE     - total daily mg vs dose_rule limits (DOSE-001 math, D10)

Every finding carries evidence + kb_version + a step-by-step reasoning
chain. No AI. Deterministic: same input -> same findings.
"""
from __future__ import annotations
import json, sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from app.db.phase1_load import (
    SEED_DIR, KB_VERSION,
    T_DRUG, C_DRUG_ID, C_DRUG_NAME,
    T_INTER, C_IA_A, C_IA_B, C_IA_SEV, C_IA_SRC, C_IA_KB,
    T_DOSE, C_DOSE_FK, C_DOSE_MAX,
)
from app.normalize.engine import NormalizedPrescription

SEVERITY_ORDER = {"contraindicated": 5, "major": 4, "moderate": 3, "minor": 2, "unknown": 1}

ALLERGY_SEED = SEED_DIR / "allergy_rules.json"
ORGAN_SEED = SEED_DIR / "organ_rules.json"


@dataclass
class Finding:
    rule_id: str
    category: str                 # ddi | duplicate | allergy | organ | dose
    severity: str
    title: str
    mechanism: str
    evidence_source: str
    kb_version: str
    reasoning_chain: list[str]
    ingredients: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)   # rule-specific numbers


@dataclass
class RulesReport:
    findings: list[Finding]
    coverage: dict                # which checks ran / skipped and why


# ---------------- helpers ----------------

def _load_seed(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _pair_lookup(conn, id_a: int, id_b: int):
    lo, hi = min(id_a, id_b), max(id_a, id_b)   # canonical (FR-24)
    return conn.execute(
        f"SELECT {C_IA_SEV}, {C_IA_SRC}, {C_IA_KB} FROM {T_INTER} "
        f"WHERE {C_IA_A}=? AND {C_IA_B}=?", (lo, hi)).fetchone()


# ---------------- checkers ----------------

def check_ddi(conn, norm_rx: NormalizedPrescription) -> tuple[list[Finding], dict]:
    ids = sorted(norm_rx.daily_totals.keys())
    findings, checked = [], 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            checked += 1
            a, b = ids[i], ids[j]
            ta, tb = norm_rx.daily_totals[a], norm_rx.daily_totals[b]
            na, nb = ta["generic_name"], tb["generic_name"]
            row = _pair_lookup(conn, a, b)
            if row is None:
                continue
            sev, src, kb = row
            findings.append(Finding(
                rule_id="DDI-001", category="ddi", severity=sev,
                title=f"Interaction: {na} + {nb}",
                mechanism=f"DDInter 2.0 reports a '{sev}' interaction between {na} and {nb}.",
                evidence_source=src or "DDInter 2.0", kb_version=kb or KB_VERSION,
                reasoning_chain=[
                    f"Prescription contains '{na}' (from {ta['sources']}).",
                    f"Prescription contains '{nb}' (from {tb['sources']}).",
                    f"Interaction table lookup ({na}, {nb}) -> severity '{sev}'.",
                    f"Recorded as {sev} finding with source '{src or 'DDInter 2.0'}' v{kb or KB_VERSION}.",
                ],
                ingredients=[na, nb],
                data={"pair": [na, nb], "severity": sev},
            ))
    return findings, {"check": "ddi", "ran": True, "pairs_checked": checked,
                      "pairs_flagged": len(findings)}


def check_duplicate(norm_rx: NormalizedPrescription) -> tuple[list[Finding], dict]:
    findings = []
    for t in norm_rx.daily_totals.values():
        if len(set(t["sources"])) > 1:
            findings.append(Finding(
                rule_id="DUP-001", category="duplicate", severity="moderate",
                title=f"Duplicate therapy: {t['generic_name']} from {len(set(t['sources']))} brands",
                mechanism=(f"{t['generic_name']} is prescribed under multiple brand names "
                           f"({', '.join(sorted(set(t['sources'])))}). Combined intake may "
                           f"unintentionally exceed the intended dose."),
                evidence_source="RxRaksha normalization engine", kb_version=KB_VERSION,
                reasoning_chain=[
                    f"Brands {sorted(set(t['sources']))} all normalize to '{t['generic_name']}'.",
                    f"Daily total across brands: {t['total_daily_mg']} mg.",
                    "Same ingredient from >=2 sources -> duplicate therapy finding.",
                ],
                ingredients=[t["generic_name"]],
                data={"sources": sorted(set(t["sources"])),
                      "total_daily_mg": t["total_daily_mg"]},
            ))
    return findings, {"check": "duplicate", "ran": True, "flagged": len(findings)}


def _class_rules_check(norm_rx, rules, category, rule_prefix, patient_key, patient_vals, condition_label):
    findings = []
    if not patient_vals:
        return findings, {"check": category, "ran": False,
                          "reason": f"patient {patient_key} not provided — check skipped (reported, not hidden)"}
    names = {t["generic_name"]: t for t in norm_rx.daily_totals.values()}
    for rule in rules:
        hit_ings = [i for i in rule["ingredients"] if i in names]
        if not hit_ings:
            continue
        label = rule.get("allergen") or rule.get("condition")
        findings.append(Finding(
            rule_id=rule["rule_id"], category=category, severity=rule["severity"],
            title=f"{condition_label}: {label} vs {', '.join(hit_ings)}",
            mechanism=rule["mechanism"],
            evidence_source=rule["source"], kb_version=KB_VERSION,
            reasoning_chain=[
                f"Patient profile lists {patient_key}: {patient_vals}.",
                f"Rule {rule['rule_id']} covers {label}: {rule['ingredients']}.",
                f"Prescription contains: {', '.join(hit_ings)} -> {rule['severity']} finding.",
            ],
            ingredients=hit_ings,
            data={patient_key: patient_vals, "matched": hit_ings},
        ))
    return findings, {"check": category, "ran": True, "rules_loaded": len(rules),
                      "flagged": len(findings)}


def check_allergy(norm_rx, patient: dict, rules):
    return _class_rules_check(norm_rx, rules, "allergy", "ALLERGY", "allergies",
                              patient.get("allergies") or [], "Allergy")


def check_organ(norm_rx, patient: dict, rules):
    conds = [c for c in ("kidney", "liver") if patient.get(f"{c}_impairment")]
    matched = [r for r in rules if r["condition"] in conds]
    return _class_rules_check(norm_rx, matched, "organ", "ORGAN", "conditions",
                              conds, "Organ risk")


def check_dose(conn, norm_rx: NormalizedPrescription) -> tuple[list[Finding], dict]:
    dose_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({T_DOSE})")}
    use_fk = C_DOSE_FK in dose_cols
    findings, checked = [], 0
    for drug_id, t in norm_rx.daily_totals.items():
        if use_fk:
            row = conn.execute(
                f"SELECT {C_DOSE_MAX} FROM {T_DOSE} WHERE {C_DOSE_FK}=?", (drug_id,)).fetchone()
        else:
            row = conn.execute(
                f"SELECT {C_DOSE_MAX} FROM {T_DOSE} WHERE ingredient=?",
                (t["generic_name"],)).fetchone()
        if row is None:
            continue
        checked += 1
        limit_mg = float(row[0])
        total = t["total_daily_mg"]
        if t["unknown_dose_lines"] > 0 and total <= limit_mg:
            # under limit BUT some lines unquantified -> flag for review, not a violation
            findings.append(Finding(
                rule_id="DOSE-REVIEW", category="dose", severity="unknown",
                title=f"Dose partially unknown: {t['generic_name']}",
                mechanism=(f"Counted {total} mg/day of {t['generic_name']} (limit {limit_mg} mg), "
                           f"but {t['unknown_dose_lines']} line(s) had no determinable frequency. "
                           f"True daily dose may be higher."),
                evidence_source="dose_rule seed", kb_version=KB_VERSION,
                reasoning_chain=[
                    f"Quantified daily total: {total} mg from {t['sources']}.",
                    f"{t['unknown_dose_lines']} line(s) could not be quantified (e.g. SOS).",
                    "Cannot certify dose is under limit -> review finding.",
                ],
                ingredients=[t["generic_name"]],
                data={"total_daily_mg": total, "limit_mg": limit_mg,
                      "unknown_lines": t["unknown_dose_lines"]},
            ))
            continue
        if total > limit_mg:
            over = round(total - limit_mg, 4)
            findings.append(Finding(
                rule_id="DOSE-001", category="dose", severity="major",
                title=f"Excessive daily dose: {t['generic_name']} {total} mg > {limit_mg} mg",
                mechanism=(f"Total daily {t['generic_name']} ({total} mg from "
                           f"{t['sources']}) exceeds the maximum recommended {limit_mg} mg/day. "
                           f"Overage: {over} mg/day."),
                evidence_source="dose_rule seed", kb_version=KB_VERSION,
                reasoning_chain=[
                    f"Lines contributing: {t['sources']} -> total {total} mg/day.",
                    f"dose_rule limit for {t['generic_name']}: {limit_mg} mg/day.",
                    f"{total} > {limit_mg} -> major overdose finding (overage {over} mg/day).",
                ],
                ingredients=[t["generic_name"]],
                data={"total_daily_mg": total, "limit_mg": limit_mg, "overage_mg": over},
            ))
    return findings, {"check": "dose", "ran": True, "rules_checked": checked,
                      "flagged": len(findings)}


# ---------------- orchestrator ----------------

class RulesEngine:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.allergy_rules = _load_seed(ALLERGY_SEED)
        self.organ_rules = _load_seed(ORGAN_SEED)

    def analyze(self, norm_rx: NormalizedPrescription, patient: dict | None = None) -> RulesReport:
        patient = patient or {}
        findings: list[Finding] = []
        coverage = []

        f, c = check_ddi(self.conn, norm_rx); findings += f; coverage.append(c)
        f, c = check_duplicate(norm_rx); findings += f; coverage.append(c)
        f, c = check_allergy(norm_rx, patient, self.allergy_rules); findings += f; coverage.append(c)
        f, c = check_organ(norm_rx, patient, self.organ_rules); findings += f; coverage.append(c)
        f, c = check_dose(self.conn, norm_rx); findings += f; coverage.append(c)

        # deterministic ordering: highest severity first, then rule_id
        findings.sort(key=lambda x: (-SEVERITY_ORDER.get(x.severity, 0), x.rule_id, x.title))
        return RulesReport(findings=findings, coverage={"checks": coverage,
                                                        "normalization": norm_rx.coverage})
