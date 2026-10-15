"""Phase 5a: template-based explanations.

Deterministic, clinician-facing text built from findings. Always available,
no AI, no network. This is the DEFAULT explanation path; the optional AI
summary (summarizer.py) can never replace it — only annotate alongside it,
behind the human-verification gate (decision D10).
"""
from __future__ import annotations
from dataclasses import dataclass

from app.rules.engine import Finding, RulesReport
from app.scoring.engine import ScoreResult

CATEGORY_LABEL = {
    "ddi": "Drug–drug interaction",
    "duplicate": "Duplicate therapy",
    "allergy": "Allergy conflict",
    "organ": "Organ-risk conflict",
    "dose": "Dosage limit",
}

SEVERITY_PHRASE = {
    "contraindicated": "is contraindicated",
    "major": "poses a major risk",
    "moderate": "requires caution",
    "minor": "is a minor concern",
    "unknown": "could not be fully assessed",
}


@dataclass
class ExplainedFinding:
    rule_id: str
    headline: str                 # one-line plain-language summary
    detail: str                   # paragraph built from the reasoning chain
    severity: str
    category: str


def _headline(f: Finding) -> str:
    cat = CATEGORY_LABEL.get(f.category, f.category)
    sev = SEVERITY_PHRASE.get(f.severity, f.severity)
    ings = " + ".join(f.ingredients) if f.ingredients else "prescription"
    return f"{cat}: {ings} — {sev}."


def _detail(f: Finding) -> str:
    steps = " ".join(f"({i+1}) {s}" for i, s in enumerate(f.reasoning_chain))
    return (f"{f.mechanism} Reasoning: {steps} "
            f"[Source: {f.evidence_source}, KB {f.kb_version}]")


def explain_findings(report: RulesReport) -> list[ExplainedFinding]:
    return [ExplainedFinding(rule_id=f.rule_id, headline=_headline(f),
                             detail=_detail(f), severity=f.severity, category=f.category)
            for f in report.findings]


def explain_summary(report: RulesReport, result: ScoreResult,
                    norm_coverage: dict) -> str:
    """One-paragraph clinician summary, fully templated."""
    n = len(report.findings)
    ran = [c["check"] for c in report.coverage.get("checks", []) if c.get("ran")]
    skipped = [c["check"] for c in report.coverage.get("checks", []) if not c.get("ran")]
    parts = [
        f"Risk score {result.score}/100 ({result.band}).",
        f"{n} finding(s) across {len(ran)} completed checks ({', '.join(ran) or 'none'}).",
    ]
    if n:
        top = report.findings[0]
        parts.append(f"Most severe: {top.title} [{top.severity}].")
    if skipped:
        parts.append(f"Not assessed: {', '.join(skipped)} — "
                     f"patient profile incomplete; provide it to enable these checks.")
    if norm_coverage.get("unmatched") or norm_coverage.get("review"):
        parts.append(f"{norm_coverage.get('unmatched', 0)} line(s) unrecognized and "
                     f"{norm_coverage.get('review', 0)} need review; "
                     f"resolve these before relying on the score.")
    parts.append(result.action)
    return " ".join(parts)
