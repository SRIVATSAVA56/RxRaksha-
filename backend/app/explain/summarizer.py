"""Phase 5b: OPTIONAL AI summary — gated by human verification (decision D10).

Structural guarantee: unverified AI text CANNOT appear in a report.
  - generate() produces a Draft, never part of the report payload.
  - The report accepts AI text ONLY via attach_verified(), which requires
    an explicit verifier identity + a signed verification record.
  - If no verification record exists, the field is omitted entirely.

There is no code path from generate() to the report that skips verification.
"""
from __future__ import annotations
import hashlib, json, time
from dataclasses import dataclass, field


@dataclass
class Draft:
    """AI-generated text. Exists ONLY outside the report until verified."""
    text: str
    model: str
    prompt_hash: str
    created_at: float = field(default_factory=time.time)
    verified: bool = False          # immutable False at creation


@dataclass
class Verification:
    verifier_id: str                # e.g. pharmacist license / staff id
    method: str = "manual_review"   # "manual_review" | "two_person_check"
    at: float = field(default_factory=time.time)
    draft_hash: str = ""

    def sign(self, draft: Draft) -> "Verification":
        self.draft_hash = hashlib.sha256(
            f"{draft.prompt_hash}|{draft.text}|{self.verifier_id}".encode()).hexdigest()
        return self


# -- pluggable AI backend: local template fallback or any LLM client --

def _offline_fallback(report_summary: str) -> str:
    """No-network 'AI': rephrases the templated summary. Clearly labeled."""
    return ("[AI-assisted draft, UNVERIFIED — do not act on this text] "
            + report_summary)


def generate(report_summary: str, llm_client=None, model: str = "offline-fallback") -> Draft:
    """Produce an UNVERIFIED draft. The draft is flagged and cannot be
    attached to a report without a Verification record."""
    if llm_client is not None:
        text = llm_client(report_summary)      # any callable: prompt -> str
        model = getattr(llm_client, "model_name", model)
    else:
        text = _offline_fallback(report_summary)
    prompt_hash = hashlib.sha256(report_summary.encode()).hexdigest()
    return Draft(text=text, model=model, prompt_hash=prompt_hash)   # verified=False


def attach_verified(report_dict: dict, draft: Draft,
                    verification: Verification | None) -> dict:
    """The ONLY way AI text enters a report. Requires:
       1. a Verification record signed against THIS draft, and
       2. hash match (proves the human reviewed this exact text).
       Otherwise the report carries no AI field at all."""
    if verification is None:
        return report_dict                                       # no AI, silently
    expected = hashlib.sha256(
        f"{draft.prompt_hash}|{draft.text}|{verification.verifier_id}".encode()).hexdigest()
    if verification.draft_hash != expected:
        raise ValueError("verification does not match this draft — refusing to attach")
    draft.verified = True
    report_dict["ai_summary"] = {
        "text": draft.text, "model": draft.model, "verified": True,
        "verified_by": verification.verifier_id, "method": verification.method,
        "verified_at": verification.at,
    }
    return report_dict
