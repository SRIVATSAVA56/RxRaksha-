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
import hashlib, io, re, sqlite3
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator

from app.db.phase1_load import DEFAULT_DB, T_DRUG, T_INTER
from app.normalize.engine import NormalizeEngine, NormalizedPrescription
from app.rules.engine import RulesEngine, RulesReport, Finding
from app.scoring.engine import score as score_report
from app.explain.templates import explain_findings, explain_summary
from app.explain.summarizer import generate, attach_verified, Verification
from app.explain.llm_client import enabled as llm_enabled, LLMClient, _clean_patient_text
from app.auth import router as auth_router, get_current_user
from app.vault import router as vault_router

try:
    import pytesseract
    if Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe").exists():
        pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
except ImportError:
    pytesseract = None

MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10MB

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
app.include_router(auth_router)
app.include_router(vault_router)

STATIC = Path(__file__).resolve().parent / "static"

@app.get("/", include_in_schema=False)
@app.get("/static/index.html", include_in_schema=False)
def ui():
    resp = FileResponse(STATIC / "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

try:
    from fastapi.staticfiles import StaticFiles
    if STATIC.exists():
        app.mount("/static", StaticFiles(directory=STATIC), name="static")
except Exception:
    pass
# ---------- schemas ----------

class PatientProfile(BaseModel):
    age: int | None = 58
    weight_kg: float | None = 72.0
    gender: str | None = "unknown"
    allergies: list[str] = Field(default_factory=list)
    allergy_state: str = "unknown"
    kidney_impairment: bool = False
    liver_impairment: bool = False

class AnalyzeRequest(BaseModel):
    lines: list[str]
    patient: PatientProfile = Field(default_factory=PatientProfile)

class SummaryRequest(BaseModel):
    report: dict                      # a prior /analyze response body
    verifier_id: str | None = None    # if absent -> draft returned, NOT attached
    method: str = "manual_review"

VALID_SEVERITIES = {"contraindicated", "major", "moderate", "minor", "unknown"}
SAFE_STR_REGEX = re.compile(r"^[a-zA-Z0-9\s\-_./()]+$")
ALLOWED_DATA_KEYS = {
    "pair", "severity", "sources", "total_daily_mg", "allergies",
    "conditions", "matched", "limit_mg", "unknown_lines", "overage_mg"
}

class ExplainRequest(BaseModel):
    category: str
    severity: str
    ingredients: list[str]
    evidence_source: str
    kb_version: str
    data: dict | None = None
    age: int | None = None
    audience: str = "clinician"
    gender: str | None = None

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        if v.lower() not in VALID_SEVERITIES:
            raise ValueError(f"Invalid severity: {v}")
        return v.lower()

    @field_validator("ingredients")
    @classmethod
    def validate_ingredients(cls, v: list[str]) -> list[str]:
        if not (1 <= len(v) <= 6):
            raise ValueError("ingredients must have between 1 and 6 items")
        for item in v:
            if not (1 <= len(item) <= 80):
                raise ValueError("ingredient must be 1 to 80 characters")
            if not SAFE_STR_REGEX.match(item):
                raise ValueError("ingredient contains invalid characters")
        return v

    @field_validator("evidence_source", "kb_version")
    @classmethod
    def validate_safe_str(cls, v: str) -> str:
        if not (1 <= len(v) <= 80):
            raise ValueError("Must be 1 to 80 characters")
        if not SAFE_STR_REGEX.match(v):
            raise ValueError("Contains invalid characters")
        return v

    @field_validator("age")
    @classmethod
    def validate_age(cls, v: int | None) -> int | None:
        if v is not None and not (0 <= v <= 120):
            raise ValueError("age must be between 0 and 120")
        return v

    @field_validator("audience")
    @classmethod
    def validate_audience(cls, v: str) -> str:
        v_norm = (v or "").strip().lower()
        if v_norm not in {"clinician", "patient"}:
            raise ValueError("audience must be 'clinician' or 'patient'")
        return v_norm

    @field_validator("gender")
    @classmethod
    def validate_gender(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v_str = v.strip()
        if not (1 <= len(v_str) <= 30) or not SAFE_STR_REGEX.match(v_str):
            raise ValueError("gender contains invalid characters")
        return v_str

EXPLAIN_CACHE: OrderedDict[str, dict] = OrderedDict()

from app.normalize.engine import Ingredient, NormalizedLine
from app.explain.llm_resolver import resolve_unknowns
import logging

logger = logging.getLogger("rxraksha")

# ---------- helpers ----------

def _run_pipeline(lines: list[str], patient: dict) -> dict:
    norm_rx: NormalizedPrescription = state["normalizer"].normalize_prescription(lines)

    # LLM Fallback Resolver step for any unmatched lines
    llm_findings = []
    unmatched_lines = [ln.raw for ln in norm_rx.lines if ln.status == "unmatched"]
    if unmatched_lines:
        resolved_ingredients = [
            i.generic_name for ln in norm_rx.lines if ln.status != "unmatched"
            for i in ln.ingredients if i.generic_name
        ]
        try:
            llm_res = resolve_unknowns(unmatched_lines, resolved_ingredients)
            needs_rerun = False
            for item in llm_res.get("resolved", []):
                ent_line = item.get("entered_line", "").strip()
                norm_ing = item.get("ingredient")
                str_mg = item.get("strength_mg")
                daily_mg = item.get("daily_mg")

                matched = False
                if norm_ing:
                    # RETRY deterministic KB lookup with normalized ingredient
                    m = state["normalizer"].resolve(norm_ing, str_mg)
                    if m:
                        for idx, ln in enumerate(norm_rx.lines):
                            if ln.raw.strip() == ent_line and ln.status == "unmatched":
                                ings = []
                                for drug_id, comp_str in m.rows:
                                    s = str_mg or comp_str or (ings and ings[0].strength_mg) or None
                                    d = daily_mg or s
                                    ings.append(Ingredient(
                                        drug_id=drug_id,
                                        generic_name=state["normalizer"]._name_of(drug_id),
                                        strength_mg=s,
                                        daily_mg=d,
                                        source_alias=f"{norm_ing} (LLM-normalized)",
                                        match_type="generic (LLM-normalized)",
                                        confidence=m.confidence,
                                        needs_review=False
                                    ))
                                norm_rx.lines[idx] = NormalizedLine(
                                    raw=ln.raw,
                                    parsed=ln.parsed,
                                    ingredients=ings,
                                    status="ok"
                                )
                                needs_rerun = True
                                matched = True
                                break

                # Only if STILL unresolved after retry: collect LLM findings
                if not matched and item.get("interactions"):
                    llm_findings.append({
                        "entered_line": ent_line,
                        "ingredient": norm_ing,
                        "strength_mg": str_mg,
                        "daily_mg": daily_mg,
                        "confidence": item.get("confidence", "low"),
                        "sources": item.get("sources", []),
                        "interactions": item.get("interactions", []),
                        "status": "AI-RESOLVED — UNVERIFIED",
                        "source": "llm-web-search"
                    })

            if needs_rerun:
                from collections import Counter
                totals = {}
                for ln in norm_rx.lines:
                    for ing in ln.ingredients:
                        e = totals.setdefault(ing.drug_id, {
                            "generic_name": ing.generic_name,
                            "total_daily_mg": 0.0,
                            "unknown_dose_lines": 0,
                            "sources": []
                        })
                        e["sources"].append(ing.source_alias)
                        if ing.daily_mg is None:
                            e["unknown_dose_lines"] += 1
                        else:
                            e["total_daily_mg"] += ing.daily_mg
                st = Counter(l.status for l in norm_rx.lines)
                norm_rx.coverage = {
                    "lines_total": len(norm_rx.lines),
                    "ok": st.get("ok", 0),
                    "review": st.get("review", 0),
                    "unmatched": st.get("unmatched", 0),
                    "doses_known": sum(1 for l in norm_rx.lines for i in l.ingredients if i.daily_mg is not None),
                    "doses_unknown": sum(1 for l in norm_rx.lines for i in l.ingredients if i.daily_mg is None),
                }
                norm_rx.daily_totals = totals
        except Exception as e:
            logger.warning("LLM fallback resolver failed: %s", e)

    report: RulesReport = state["rules"].analyze(norm_rx, patient)
    result = score_report(report, norm_rx.coverage)
    out = _serialize(norm_rx, report, result, ocr_meta=None)
    out["patient_context"] = patient
    out["llm_findings"] = llm_findings
    return out

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
            "interactions": conn.execute(f"SELECT COUNT(*) FROM {T_INTER}").fetchone()[0],
            "ai_explain": llm_enabled()}

@app.post("/analyze/explain")
def analyze_explain(req: ExplainRequest, current_user: dict = Depends(get_current_user)):
    if req.severity == "unknown":
        unknown_text = (
            "There is no known risk rating for this medicine combination in the safety database. Please speak with your doctor or pharmacist."
            if req.audience == "patient"
            else "The knowledge base lists this pair with no severity rating. No further information is available."
        )
        return {
            "ai_explanation": {
                "text": unknown_text,
                "model": None,
                "verified": False,
                "source": "template",
                "audience": req.audience,
                "label": "No AI used."
            }
        }

    if not llm_enabled():
        raise HTTPException(status_code=404, detail="AI explanation is not enabled")

    clean_data = {}
    if req.data and isinstance(req.data, dict):
        for k, v in req.data.items():
            if k in ALLOWED_DATA_KEYS:
                if isinstance(v, (int, float)):
                    clean_data[k] = v
                elif isinstance(v, str) and len(v) <= 60:
                    clean_data[k] = v

    if req.audience == "patient":
        age_str = str(req.age) if req.age is not None else "unspecified"
        gender_str = req.gender if req.gender else "unspecified"
        drugs_str = " and ".join(req.ingredients)
        facts = (
            f"Patient: age {age_str}, {gender_str}\n"
            f"Finding: {drugs_str}, severity: {req.severity}\n"
            f"Evidence: {req.evidence_source} (KB {req.kb_version})"
        )
    else:
        facts_lines = [
            f"Finding type: {req.category}",
            f"Drugs: {', '.join(req.ingredients)}",
            f"Severity per {req.evidence_source}: {req.severity}",
            f"Knowledge base: {req.kb_version}",
        ]
        if clean_data:
            details_str = ", ".join(f"{k}={v}" for k, v in sorted(clean_data.items()))
            facts_lines.append(f"Details: {details_str}")
        else:
            facts_lines.append("Details: none")

        if req.age is not None:
            facts_lines.append(f"Patient age: {req.age}")

        facts = "\n".join(facts_lines)

    prompt_hash = hashlib.sha256(f"{req.audience}:{facts}".encode()).hexdigest()
    if prompt_hash in EXPLAIN_CACHE:
        return EXPLAIN_CACHE[prompt_hash]

    try:
        draft = generate(facts, llm_client=LLMClient(audience=req.audience))
    except Exception:
        raise HTTPException(status_code=503, detail="AI explanation unavailable")

    text = draft.text
    if req.audience == "patient":
        text = _clean_patient_text(text)

    res_payload = {
        "ai_explanation": {
            "text": text,
            "model": draft.model,
            "verified": False,
            "source": "llm",
            "audience": req.audience,
            "label": "AI-generated from model knowledge. Not from DDInter. Unverified."
        }
    }
    if len(EXPLAIN_CACHE) >= 200:
        EXPLAIN_CACHE.popitem(last=False)
    EXPLAIN_CACHE[prompt_hash] = res_payload
    return res_payload

@app.post("/analyze")
def analyze(req: AnalyzeRequest, current_user: dict = Depends(get_current_user)):
    if not any(l.strip() for l in req.lines):
        raise HTTPException(400, "at least one non-empty prescription line is required")
    return _run_pipeline(req.lines, req.patient.model_dump())

@app.post("/analyze/summary")
def analyze_summary(req: SummaryRequest, current_user: dict = Depends(get_current_user)):
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
    try:
        from PIL import Image
        import pytesseract
    except ImportError:
        raise HTTPException(503, "OCR unavailable: install pytesseract + Pillow")

    import shutil
    cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", "tesseract")
    if not Path(cmd).exists() and not shutil.which(cmd):
        for p in [Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
                  Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe")]:
            if p.exists():
                pytesseract.pytesseract.tesseract_cmd = str(p)
                cmd = str(p)
                break
    if not Path(cmd).exists() and not shutil.which(cmd):
        raise HTTPException(503, "OCR unavailable: tesseract binary not installed or path not found")
    return pytesseract, Image

@app.post("/ocr/extract")
async def ocr_extract(image: UploadFile = File(...)):
    """Extract plain text lines from an uploaded prescription image."""
    pytesseract, Image = _init_tesseract()

    raw = await image.read()
    if len(raw) > MAX_IMAGE_SIZE:
        raise HTTPException(413, "Image file too large: maximum allowed size is 10MB")

    try:
        text = pytesseract.image_to_string(Image.open(io.BytesIO(raw)))
    except Exception as e:
        err_msg = str(e)
        if "not installed" in err_msg.lower() or "not found" in err_msg.lower():
            raise HTTPException(503, f"OCR unavailable: {e}")
        raise HTTPException(422, f"OCR extraction failed: {e}")

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return {"text": text, "lines": lines}

@app.post("/analyze/image")
async def analyze_image(image: UploadFile = File(...),
                        patient: str = Form(default="{}"),
                        current_user: dict = Depends(get_current_user)):
    """OCR path. pytesseract is optional: if unavailable, 503 with a clear reason."""
    pytesseract, Image = _init_tesseract()

    raw = await image.read()
    if len(raw) > MAX_IMAGE_SIZE:
        raise HTTPException(413, "Image file too large: maximum allowed size is 10MB")

    import json as _json
    try:
        patient_dict = _json.loads(patient)
    except _json.JSONDecodeError:
        raise HTTPException(400, "patient form field must be valid JSON")

    try:
        text = pytesseract.image_to_string(Image.open(io.BytesIO(raw)))
    except Exception as e:
        err_msg = str(e)
        if "not installed" in err_msg.lower() or "not found" in err_msg.lower():
            raise HTTPException(503, f"OCR unavailable: {e}")
        raise HTTPException(422, f"OCR failed: {e}")

    lines = [l.strip() for l in text.splitlines() if l.strip()]
    out = _run_pipeline(lines, patient_dict)
    out["ocr"] = {"engine": "tesseract", "lines_extracted": len(lines), "raw_text": text}
    return out

