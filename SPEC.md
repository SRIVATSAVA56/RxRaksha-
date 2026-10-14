# RxRaksha Build Spec v1.4 — FINAL

**System Name:** RxRaksha (रक्ष / Raksha = Protection)  
**Version:** v1.4 (Includes DOSE-001 Dosage Checking Patch & Database Standards)  
**Backend:** Python 3.11+ / FastAPI / SQLite (portable to Oracle via `sql/oracle.sql`)  
**Frontend:** React 19 + TypeScript + Tailwind CSS + Lucide Icons  

---

## 1. Locked Architecture Decisions

| ID | Decision | Rationale |
|---|---|---|
| **D1** | Single-page multi-step flow: `/review/prescription` → `/review/confirm` → `/review/patient` → `/review/results` | Zero state loss across steps, frictionless clinical review |
| **D2** | Pure deterministic rule engine runs FIRST. LLM runs ONLY for natural language explanation generation | Guarantees 0% clinical hallucination; rules fire deterministically |
| **D3** | Invariant: Every medicine row must have an explicit match or be flagged UNRESOLVED | Safety-critical honesty; no silent drop of unparsed medicines |
| **D4** | Dual database support: Primary SQLite, 100% portable DDL to Oracle Free/23c | Fast local development + enterprise SQL portability |
| **D5** | Database Architecture: 3NF Normalization, Indexes, Triggers, Transactions, Users/Roles, CLOB (clinical texts), BLOB (prescription images) | Enterprise relational integrity and compliance |
| **D6** | Pinned DDInter dataset + hand-coded clinical seed datasets | Reproducible clinical knowledge base |
| **D7** | Severity levels: `CRITICAL` > `HIGH` > `MODERATE` > `LOW` > `UNKNOWN` | Standardized clinical risk hierarchy |
| **D8** | Missing clinical data: If dose/frequency or organ profile missing, flag in `skipped_checks` and mark `coverage_status = "partial"` | Transparent audit trail for clinicians |
| **D9** | Stateless backend design: analysis runs return self-contained payloads, client retains state | No silent server drift; reproducible deterministic analysis |
| **D10** | Diagnosis-based rules are NOT built. They appear in `checks_not_configured`. Dosage rules ARE built (one hand-coded rule family, DOSE-001). | Spec Patch v1.4 locked decision |

---

## 2. Database Schema & Architecture (`sql/sqlite.sql` & `sql/oracle.sql`)

### 2.1 Normalization (3NF)
- **1NF**: Atomic scalar values, unique primary keys on all tables.
- **2NF**: No partial dependencies; composite keys on join tables (`brand_ingredient`, `drug_class_member`).
- **3NF**: No transitive dependencies. Generic drugs, aliases, brands, ingredients, classes, interactions, and rules are separate relations linked via Foreign Keys with `ON DELETE CASCADE`.

### 2.2 Relational Tables
1. **`drug`**: `drug_id` (PK), `generic_name` (UNIQUE), `name_norm` (UNIQUE), `atc_code`, `source_ref`.
2. **`drug_alias`**: `alias_norm` (PK), `drug_id` (FK → `drug`).
3. **`brand`**: `brand_id` (PK), `brand_name` (UNIQUE), `name_norm` (UNIQUE), `source_ref`.
4. **`brand_ingredient`**: `(brand_id, drug_id)` (Composite PK, FKs).
5. **`drug_class`**: `class_id` (PK), `class_name` (UNIQUE), `name_norm` (UNIQUE), `aliases`, `description` (CLOB).
6. **`drug_class_member`**: `(class_id, drug_id)` (Composite PK, FKs).
7. **`interaction`**: `interaction_id` (PK), `drug_a_id` (FK), `drug_b_id` (FK), `source`, `source_ref`, `source_severity`, `severity`, `mechanism` (CLOB), `management` (CLOB), `side_effects` (CLOB), `human_verified` (BOOLEAN/NUMBER(1)). Constraint: `drug_a_id < drug_b_id`.
8. **`allergy_cross_sensitivity`**: `cross_id` (PK), `trigger_class_id` (FK), `affected_class_id` (FK), `severity`, `mechanism` (CLOB), `effects` (CLOB), `source_ref`, `human_verified`.
9. **`disease_rule`**: `rule_key` (PK), `class_id` (FK), `drug_id` (FK), `factor_type` (`KIDNEY` or `LIVER`), `min_level` (`MILD`, `MODERATE`, `SEVERE`), `severity`, `rationale` (CLOB), `effects` (CLOB), `source_ref`, `human_verified`.
10. **`dose_rule`** *(v1.4)*: `rule_key` (PK), `drug_id` (FK), `class_id` (FK), `max_daily_dose_mg` (NUMBER/REAL), `severity`, `rationale` (CLOB), `effects` (CLOB), `source_ref`, `human_verified`.
11. **`prescription_document`**: `document_id` (PK), `filename`, `mime_type`, `file_size_bytes`, `image_data` (BLOB), `ocr_extracted_text` (CLOB), `uploaded_at`.
12. **`audit_log`**: `audit_id` (PK), `event_type`, `entity_key`, `payload_json` (CLOB), `created_at`.
13. **`kb_version`**: `version_tag`, `loaded_at`, `notes` (CLOB).

### 2.3 Performance Indexes
- Indexes on `name_norm` across `drug`, `brand`, and `drug_alias`.
- Pairwise index on `interaction(drug_a_id, drug_b_id)` and single index on `interaction(drug_b_id)`.
- Foreign key lookup indexes on `dose_rule(drug_id)`, `dose_rule(class_id)`, `disease_rule(drug_id)`, `drug_class_member(drug_id)`.

### 2.4 Triggers & Transactions
- Normalization enforcement triggers.
- Audit logging triggers on dose rule changes.
- Explicit transaction control (`BEGIN TRANSACTION`, `COMMIT`, `ROLLBACK`) for atomic KB seeding.

### 2.5 Security, Users & Privileges
- Oracle DDL defines roles (`rxraksha_app_role`, `rxraksha_read_role`) and least-privilege `GRANT` statements.
- SQLite leverages connection isolation (`PRAGMA query_only`).

---

## 3. Seed Datasets (`backend/data/seed/`)

1. **`dose_rules.json`** *(v1.4)*:
   - `DOSE-PARACETAMOL-MAX`: Max daily dose 4000 mg, Paracetamol (resolves to Acetaminophen), severity HIGH.
2. **`disease_rules.json`**:
   - Organ contraindication rules (NSAIDs in renal failure, Paracetamol in hepatic impairment, Metformin in severe renal impairment).
3. **`allergy_cross.json`**:
   - Penicillin ↔ Cephalosporin cross-reactivity.
4. **`brands.json`**:
   - Indian pharma brands (Dolo 650, Crocin 500, Augmentin, etc.) mapped to generic ingredients.
5. **Raw DDInter Datasets**:
   - Preserved in `backend/data/raw/ddinter/` (`ddinter_downloads_code_A.csv` through `V.csv`).

---

## 4. Clinical Rule Engine & Logic

### 4.1 Rules Evaluated
1. **DDI-001 (Drug-Drug Interaction):** Evaluates all pairwise combinations of active generic ingredients against `interaction` table.
2. **DUP-001 (Duplicate Therapy):** Flags multiple medicines containing the exact same generic active ingredient.
3. **ALLERGY-001 (Allergy & Cross-Sensitivity):** Checks patient allergies against prescribed drugs and drug class cross-sensitivities.
4. **ORGAN-001 (Organ Function Contraindications):** Checks patient kidney (eGFR/stage) and liver status against `disease_rule`.
5. **DOSE-001 (Daily Dosage Limits - v1.4):**
   - Frequency mapping: "once daily"=1, "twice daily"=2, "three times daily"=3, "four times daily"=4, "at bedtime"=1, "as needed"=null, null/"Not specified"=null.
   - Converts doses to mg (`mcg / 1000`, `g * 1000`).
   - Daily total per drug = sum over all contributing rows of `(dose_mg * times_per_day)`.
   - Single-ingredient brands and generics participate. Combination products skip with notice.
   - Fires when daily total > `max_daily_dose_mg`.
   - If dose or frequency is missing on any contributing row, rule does not fire; adds skipped check entry.

### 4.2 Coverage Accounting
- `checks_run`: ["Drug-drug interactions", "Duplicate therapy", "Allergies", "Organ function", "Dosage"].
- `checks_not_configured`: ["Diagnosis-based rules", "Same-class overlap"].
- `coverage_status`: `"full"` if all data present; `"partial"` if any skipped checks occur.

---

## 5. Explainability & LLM Validation

- **Deterministic Fallback (100% reliable):** Rule engine generates structured headline, why_it_matters, and suggested_action from loaded templates.
- **DOSE-001 Template:**
  - Headline: `"{Drug}: daily dose above the loaded limit"`
  - Why it matters: `"The medicines on this list add up to {total} mg of {drug} per day. The loaded limit is {limit} mg. {rationale}"`
  - Suggested Action: `"Clinician review is required before dispensing."`
- **LLM Refiner & Strict Validator:**
  - LLM can rephrase explanation ONLY if verified: no invented drug names, no modified numbers, matches clinical severity.

---

## 6. Demo Cases

- **Case 1 (Allergy):** Amoxicillin prescribed to penicillin-allergic patient → `ALLERGY-001` (CRITICAL).
- **Case 2 (Duplicate + Overdose - v1.4):**
  - Line 1: Tab Crocin 500 mg QID (2000 mg/day)
  - Line 2: Tab Dolo 650 mg QID (2600 mg/day)
  - Result: `DUP-001` (Duplicate Paracetamol) AND `DOSE-001` (4600 mg/day > 4000 mg/day limit, HIGH).
- **Case 3 (Drug Interaction):** Warfarin + Aspirin → `DDI-001` (CRITICAL bleeding risk).
- **Case 4 (Organ Impairment):** Metformin prescribed with severe kidney impairment → `ORGAN-001` (HIGH/CRITICAL).

---

## 7. Verification Tests (T1 – T31)

- **T1–T28:** Normalization, alias resolution, DDI, duplicate therapy, allergy cross-reactivity, organ rules, OCR fallback, UI states.
- **T29 (v1.4):** Dose rule fires on Case 2 with computed total 4600 mg in reasoning chain.
- **T30 (v1.4):** Dose rule silence when Dolo is taken once daily (2650 mg <= 4000 mg limit).
- **T31 (v1.4):** Missing dose honesty: Paracetamol row with "Not specified" dose skips DOSE-001, records skipped check, coverage_status = "partial".
- **Health Endpoint:** `/api/health/kb` checks that `dose_rule` is present and populated.
