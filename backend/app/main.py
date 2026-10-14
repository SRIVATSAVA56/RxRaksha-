"""RxRaksha API — Phase 5.

Endpoints:
  GET  /health          -> liveness + DB stats
  GET  /docs            -> auto Swagger UI (FastAPI built-in)
  POST /analyze         -> text lines + patient profile -> full report
  POST /analyze/summary -> optional AI summary gated by human verification (D10)
  POST /analyze/image   -> prescription image (OCR) + patient -> full report

Pipeline: [OCR] -> normalize -> rules -> score -> report JSON.
"""
from __future__ import annotations
import io, sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.db.phase1_load import DEFAULT_DB, T_DRUG, T_INTER
from app.normalize.engine import NormalizeEngine, NormalizedPrescription
from app.rules.engine import RulesEngine, RulesReport, Finding
from app.scoring.engine import score as score_report
from app.explain.templates import explain_findings, explain_summary
from app.explain.summarizer import generate, attach_verified, Verification

# ---------- app state ----------

state: dict = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not DEFAULT_DB.exists():
        raise RuntimeError(f"DB not found at {DEFAULT_DB} — run Phase 1 first.")
    conn = sqlite3.connect(DEFAULT_DB, check_same_thread=False)
    state.update(conn=conn,
                 normalizer=NormalizeEngine(conn),
                 rules=RulesEngine(conn))
    yield
    conn.close()

app = FastAPI(title="RxRaksha API", version="0.5.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC = Path(__file__).resolve().parent / "static"

@app.get("/", include_in_schema=False)
def ui():
    return FileResponse(STATIC / "index.html")
# ---------- schemas ----------

class PatientProfile(BaseModel):
    allergies: list[str] = Field(default_factory=list)
    kidney_impairment: bool = False
    liver_impairment: bool = False

class AnalyzeRequest(BaseModel):
    lines: list[str]
    patient: PatientProfile = Field(default_factory=PatientProfile)

class SummaryRequest(BaseModel):
    report: dict                      # a prior /analyze response body
    verifier_id: str | None = None    # if absent -> draft returned, NOT attached
    method: str = "manual_review"

# ---------- helpers ----------

def _run_pipeline(lines: list[str], patient: dict) -> dict:
    norm_rx: NormalizedPrescription = state["normalizer"].normalize_prescription(lines)
    report: RulesReport = state["rules"].analyze(norm_rx, patient)
    result = score_report(report, norm_rx.coverage)
    return _serialize(norm_rx, report, result, ocr_meta=None)

def _finding_json(f: Finding) -> dict:
    return {"rule_id": f.rule_id, "category": f.category, "severity": f.severity,
            "title": f.title, "mechanism": f.mechanism,
            "evidence_source": f.evidence_source, "kb_version": f.kb_version,
            "reasoning_chain": f.reasoning_chain, "ingredients": f.ingredients,
            "data": f.data}

def _serialize(norm_rx, report, result, ocr_meta) -> dict:
    templated_summary = explain_summary(report, result, norm_rx.coverage)
    return {
        "score": result.score, "band": result.band, "action": result.action,
        "cap_applied": result.cap_applied,
        "score_ledger": result.ledger,
        "findings": [_finding_json(f) for f in report.findings],
        "explanations": [                          # NEW — templated, always present
            {"rule_id": e.rule_id, "headline": e.headline, "detail": e.detail,
             "severity": e.severity, "category": e.category}
            for e in explain_findings(report)],
        "summary": templated_summary,              # NEW — templated clinician summary
        "ai_summary": None,                        # NEW — populated ONLY via attach_verified
        "normalized": {
            "lines": [
                {"raw": ln.raw, "status": ln.status,
                 "ingredients": [
                     {"generic": i.generic_name, "strength_mg": i.strength_mg,
                      "daily_mg": i.daily_mg, "source_alias": i.source_alias,
                      "match_type": i.match_type, "needs_review": i.needs_review}
                     for i in ln.ingredients]}
                for ln in norm_rx.lines],
            "daily_totals": {str(k): v for k, v in norm_rx.daily_totals.items()},
        },
        "coverage": {**report.coverage, "normalization": norm_rx.coverage},
        "ocr": ocr_meta,
        "disclaimer": ("Decision-support only. Verify against clinical judgment. "
                       "Not a substitute for pharmacist review."),
    }

# ---------- endpoints ----------

@app.get("/health")
def health():
    conn = state["conn"]
    return {"status": "ok",
            "drugs": conn.execute(f"SELECT COUNT(*) FROM {T_DRUG}").fetchone()[0],
            "interactions": conn.execute(f"SELECT COUNT(*) FROM {T_INTER}").fetchone()[0]}

@app.post("/analyze")
def analyze(req: AnalyzeRequest):
    if not any(l.strip() for l in req.lines):
        raise HTTPException(400, "at least one non-empty prescription line is required")
    return _run_pipeline(req.lines, req.patient.model_dump())

@app.post("/analyze/summary")
def analyze_summary(req: SummaryRequest):
    """Demonstrates D10: generates an AI draft from the templated summary.
    If verifier_id is provided, attaches it as verified; otherwise returns
    the draft OUTSIDE the report, unverified and clearly labeled."""
    templated = req.report.get("summary") or ""
    if not templated:
        raise HTTPException(400, "report.summary missing — run /analyze first")
    draft = generate(templated)                    # offline fallback; plug an LLM client here
    if req.verifier_id is None:
        # unverified path: report untouched, draft returned separately
        return {"report": req.report, "ai_draft_unverified": {
            "text": draft.text, "model": draft.model, "verified": False,
            "note": "Not attached. A human must verify before this enters a report."}}
    v = Verification(verifier_id=req.verifier_id, method=req.method).sign(draft)
    return {"report": attach_verified(dict(req.report), draft, v)}

def _init_tesseract():
    import pytesseract
    import shutil
    if not shutil.which("tesseract"):
        for p in [Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
                 Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")]:
            if p.exists():
                pytesseract.pytesseract.tesseract_cmd = str(p)
                break
    return pytesseract

@app.post("/ocr/extract")
async def ocr_extract(image: UploadFile = File(...)):
    """Extract plain text lines from an uploaded prescription image."""
    try:
        from PIL import Image
        pytesseract = _init_tesseract()
    except ImportError:
        raise HTTPException(503, "OCR unavailable: install pytesseract + Pillow")

    raw = await image.read()
    try:
        text = pytesseract.image_to_string(Image.open(io.BytesIO(raw)))
    except Exception as e:
        raise HTTPException(422, f"OCR extraction failed: {e}")

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return {"text": text, "lines": lines}

@app.post("/analyze/image")
async def analyze_image(image: UploadFile = File(...),
                        patient: str = Form(default="{}")):
    """OCR path. pytesseract is optional: if unavailable, 503 with a clear reason."""
    try:
        from PIL import Image
        pytesseract = _init_tesseract()
    except ImportError:
        raise HTTPException(503, "OCR unavailable: install pytesseract + Pillow + tesseract binary")

    import json as _json
    try:
        patient_dict = _json.loads(patient)
    except _json.JSONDecodeError:
        raise HTTPException(400, "patient form field must be valid JSON")

    raw = await image.read()
    try:
        text = pytesseract.image_to_string(Image.open(io.BytesIO(raw)))
    except Exception as e:                              # tesseract missing / bad image
        raise HTTPException(422, f"OCR failed: {e}")

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = _run_pipeline(lines, patient_dict)
    out["ocr"] = {"engine": "tesseract", "lines_extracted": len(lines), "raw_text": text}
    return out

