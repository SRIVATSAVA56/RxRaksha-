/* ───────────────────────────────────────────────────────
   types.ts — Shared TypeScript types for RxRaksha frontend
   ─────────────────────────────────────────────────────── */

/* ── Candidate returned from /normalize or /extract ── */
export interface Candidate {
  kind: "GENERIC" | "BRAND";
  label: string;
  match_score: number;
  drug_ids: number[];
  ingredient_names?: string[];
}

/* ── A single medicine row (extraction or typed) ── */
export interface MedicineRow {
  row_id: string;
  source: "EXISTING" | "NEW";
  origin: "OCR" | "TYPED";
  raw_text: string;
  ocr_confidence: number | null;
  dose: string | null;
  frequency: string | null;
  candidates: Candidate[];
  selected_candidate_index: number | null;
  status: "UNCONFIRMED" | "CONFIRMED" | "UNRESOLVED";
}

/* ── Patient profile ── */
export type KidneyLiverStatus = "NORMAL" | "MILD" | "MODERATE" | "SEVERE" | "UNKNOWN";
export type AllergyDiagnosisStatus = "NONE_REPORTED" | "UNKNOWN" | "LISTED";

export interface Patient {
  label: string;
  age_years: number | null;
  weight_kg: number | null;
  gender: "MALE" | "FEMALE" | "OTHER" | null;
  kidney_status: KidneyLiverStatus;
  liver_status: KidneyLiverStatus;
  allergy_status: AllergyDiagnosisStatus;
  allergies: string[];
  diagnosis_status: AllergyDiagnosisStatus;
  diagnoses: string[];
}

/* ── Analysis response ── */
export interface Evidence {
  source_name: string;
  source_ref: string;
  source_severity: string | null;
  mechanism: string | null;
  management: string | null;
  kb_version: string;
  human_verified: boolean;
}

export interface ReasoningStep {
  step: "INPUT" | "NORMALIZATION" | "RULE" | "EVIDENCE" | "ACTION";
  text: string;
}

export interface Explanation {
  headline: string;
  why_it_matters: string;
  possible_effects: string;
  suggested_action: string;
}

export interface Finding {
  finding_id: string;
  rule_id: string;
  risk_type: "DRUG_DRUG_INTERACTION" | "DUPLICATE_THERAPY" | "ALLERGY" | "DRUG_DISEASE";
  severity: "CRITICAL" | "HIGH" | "MODERATE" | "LOW" | "UNKNOWN";
  points: number;
  affected_medicine_ids: number[];
  affected_row_ids: string[];
  affected_names: string[];
  match_type: "EXACT" | "CLASS" | "CROSS_SENSITIVITY" | null;
  evidence: Evidence;
  reasoning_chain: ReasoningStep[];
  possible_effects: string | null;
  explanation: Explanation;
  explanation_mode: "TEMPLATE" | "LLM";
  validation_status: "NOT_APPLICABLE" | "PASSED" | "REJECTED_FALLBACK";
}

export interface ScoreBasis {
  counts: Record<string, number>;
  raw_points: number;
  minimum_applied: boolean;
}

export interface AnalysisResult {
  analysis_id: string;
  input_revision: string;
  coverage_status: "complete" | "partial" | "failed";
  checks_run: string[];
  checks_not_configured: string[];
  skipped_checks: { check: string; reason: string }[];
  unresolved_items: string[];
  checked_medicine_ids: number[];
  score: number | null;
  score_basis: ScoreBasis;
  findings: Finding[];
  before_after: {
    existing: MedicineRow[];
    new: MedicineRow[];
    combined: MedicineRow[];
  };
  explanation_mode: "LLM" | "TEMPLATE";
  knowledge_base_version: string;
}

/* ── API error ── */
export interface ApiError {
  error: { code: string; message: string };
}

/* ── Demo case ── */
export interface DemoCase {
  case_id: string;
  title: string;
  description: string;
  patient: Patient;
  existing_rows: MedicineRow[];
  new_rows: MedicineRow[];
  typed_fallback_rows?: MedicineRow[];
  image_url?: string;
  synthetic: true;
}
