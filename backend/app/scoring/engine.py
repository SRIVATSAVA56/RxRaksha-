"""Phase 4a: scoring engine.

Findings -> 0-100 risk score. Higher = more risk.
Accumulation-based with a risk floor for the worst finding:
  - start at 0 (safe)
  - add per-finding weight by severity
  - floor the risk score at the worst finding's threshold (e.g. moderate >= 25, major >= 50)
  - major severity findings cap at 75 (High Risk), reserving Critical (90-100) for contraindicated
Bands:
  0-24:   Safe
  25-49:  Caution
  50-74:  Warning
  75-89:  High Risk
  90-100: Critical
Deterministic: same findings -> same score.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from app.rules.engine import Finding, RulesReport, SEVERITY_ORDER

# per-finding risk addition weight by severity
WEIGHTS = {"contraindicated": 40, "major": 25, "moderate": 12, "minor": 5, "unknown": 4}
# backward-compatibility caps reference
CAPS    = {"contraindicated": 25, "major": 50, "moderate": 75, "minor": 90, "unknown": 95}
# minimum risk floor based on worst severity present
MIN_RISK = {"contraindicated": 75, "major": 50, "moderate": 25, "minor": 10, "unknown": 5}

# named bands: (min_inclusive, label, action) in descending threshold order
BANDS = [
    (90, "Critical",  "Contraindicated combination or equivalent; hard stop."),
    (75, "High Risk", "Major issues present; do not dispense without clinician override."),
    (50, "Warning",   "Significant interaction or risk; pharmacist review advised."),
    (25, "Caution",   "Minor or moderate issues; review flagged items."),
    (0,  "Safe",      "No significant issues detected."),
]

UNKNOWN_DEDUCTION = 3   # per unmatched / review line: incomplete data adds risk


@dataclass
class ScoreResult:
    score: int
    band: str
    action: str
    ledger: list = field(default_factory=list)   # [{source, detail, delta}]
    cap_applied: str | None = None


def _band_for(score: int):
    for minimum, label, action in BANDS:
        if score >= minimum:
            return label, action
    return BANDS[-1][1], BANDS[-1][2]


def score(report: RulesReport, normalization_coverage: dict) -> ScoreResult:
    ledger, risk_points, worst = [], 0, 0   # `worst` = harshest severity rank seen

    for f in report.findings:
        w = WEIGHTS.get(f.severity, WEIGHTS["unknown"])
        risk_points += w
        worst = max(worst, SEVERITY_ORDER.get(f.severity, 1))
        ledger.append({"source": f.rule_id, "detail": f.title, "delta": w})

    # incomplete-data penalties from normalization coverage
    n_pen = normalization_coverage.get("unmatched", 0) + normalization_coverage.get("review", 0)
    if n_pen:
        d = n_pen * UNKNOWN_DEDUCTION
        risk_points += d
        ledger.append({"source": "coverage",
                       "detail": f"{n_pen} line(s) unmatched or needing review", "delta": d})

    # checks that could not run -> recorded honestly with 0 delta
    for c in report.coverage.get("checks", []):
        if not c.get("ran"):
            ledger.append({"source": f"check:{c['check']}",
                           "detail": c.get("reason", "check did not run"), "delta": 0})

    raw = min(100, max(0, risk_points))

    # minimum risk floor from worst finding
    floor_score, floor_reason = 0, None
    for sev, rank in SEVERITY_ORDER.items():
        if rank == worst and worst >= SEVERITY_ORDER["moderate"]:
            floor_score = MIN_RISK.get(sev, 100 - CAPS[sev])
            floor_reason = f"risk floored at {floor_score} due to worst severity '{sev}'"
            break

    final = max(raw, floor_score)
    if floor_reason and final == floor_score and raw < floor_score:
        ledger.append({"source": "floor", "detail": floor_reason, "delta": floor_score - raw})

    # ceiling for major: a 'major' worst finding stays in High Risk (<= 75), not Critical
    # Critical (90-100) is reserved for contraindicated combinations or catastrophic multi-hazard stacks
    worst_sev = next((s for s, r in SEVERITY_ORDER.items() if r == worst), None)
    if worst_sev == "major" and final > 75:
        ledger.append({"source": "cap",
                       "detail": "risk capped at 75: major severity ceiling",
                       "delta": 75 - final})
        final = 75

    band, action = _band_for(final)
    applied_note = floor_reason if (floor_reason and final == floor_score and raw < floor_score) else None

    return ScoreResult(score=final, band=band, action=action, ledger=ledger,
                       cap_applied=applied_note)
