"""Phase 4a: scoring engine.

Findings -> 0-100 safety score. Higher = safer.
Deduction-based with a hard cap for the worst finding:
  - start at 100
  - deduct per-finding weight by severity
  - cap the score at the worst finding's ceiling (one 'contraindicated'
    finding can never score above 25, no matter what else is true)
The ledger records every deduction so the UI can answer "why this score?"
Deterministic: same findings -> same score.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from app.rules.engine import Finding, RulesReport, SEVERITY_ORDER

# per-finding deduction weight by severity
WEIGHTS = {"contraindicated": 40, "major": 25, "moderate": 12, "minor": 5, "unknown": 4}
# hard ceiling: worst finding present caps the final score
CAPS    = {"contraindicated": 25, "major": 50, "moderate": 75, "minor": 90, "unknown": 95}
# named bands (v1.4): (min_inclusive, label, action)
BANDS = [
    (90, "Safe",      "No significant issues detected."),
    (75, "Caution",   "Minor or moderate issues; review flagged items."),
    (50, "Warning",   "Significant interaction or risk; pharmacist review advised."),
    (25, "High Risk", "Major issues present; do not dispense without clinician override."),
    (0,  "Critical",  "Contraindicated combination or equivalent; hard stop."),
]

UNKNOWN_DEDUCTION = 3   # per unmatched / review line: incomplete data costs score


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
    ledger, deductions, worst = [], 0, 0   # `worst` = harshest severity rank seen

    for f in report.findings:
        w = WEIGHTS.get(f.severity, WEIGHTS["unknown"])
        deductions += w
        worst = max(worst, SEVERITY_ORDER.get(f.severity, 1))
        ledger.append({"source": f.rule_id, "detail": f.title, "delta": -w})

    # incomplete-data penalties from normalization coverage
    n_pen = normalization_coverage.get("unmatched", 0) + normalization_coverage.get("review", 0)
    if n_pen:
        d = n_pen * UNKNOWN_DEDUCTION
        deductions += d
        ledger.append({"source": "coverage",
                       "detail": f"{n_pen} line(s) unmatched or needing review", "delta": -d})

    # checks that could not run -> no deduction, but recorded (fail-safe honesty)
    for c in report.coverage.get("checks", []):
        if not c.get("ran"):
            ledger.append({"source": f"check:{c['check']}",
                           "detail": c.get("reason", "check did not run"), "delta": 0})

    raw = max(0, 100 - deductions)

    # hard cap from worst finding
    cap_score, cap_reason = 100, None
    for sev, rank in SEVERITY_ORDER.items():
        if rank == worst and worst >= SEVERITY_ORDER["moderate"]:
            cap_score = CAPS[sev]
            cap_reason = f"score capped at {cap_score} due to worst severity '{sev}'"
            break
    final = min(raw, cap_score)
    if cap_reason and final == cap_score and raw > cap_score:
        ledger.append({"source": "cap", "detail": cap_reason, "delta": cap_score - raw})

    # floor: a 'major' worst finding is High Risk (25), not Critical —
    # Critical (0-24) is reserved for multi-hazard stacks or contraindicated.
    worst_sev = next((s for s, r in SEVERITY_ORDER.items() if r == worst), None)
    if worst_sev == "major" and final < 25:
        ledger.append({"source": "floor",
                       "detail": "score floored at 25: major severity floor",
                       "delta": 25 - final})
        final = 25

    band, action = _band_for(final)
    return ScoreResult(score=final, band=band, action=action, ledger=ledger,
                       cap_applied=cap_reason if final == cap_score and raw > cap_score else None)
