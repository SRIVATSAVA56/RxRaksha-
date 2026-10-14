# RxRaksha TODO

## Phase 0 decisions (2026-10-14)

### Oracle gate: FAIL
- **Reason:** `python-oracledb` has no build for Python 3.14. `import oracledb` fails.
- **Decision:** `DB_BACKEND=sqlite`. Schema remains portable to Oracle via `sql/oracle.sql`.
- **D11 impact:** Pitch deck slide 8 says "SQLite, schema portable to Oracle".

### OCR test: Tesseract unavailable
- **Reason:** Tesseract binary is not installed on this machine.
- **Decision:** `OCR_ENGINE=tesseract` kept in config (the code handles the `OcrUnavailable` gracefully).
  - Typed entry always works (INV8).
  - If Sai installs Tesseract before demo, OCR will work automatically.
  - 503 `OCR_UNAVAILABLE` on upload → UI switches to Type tab per spec 11.3.
  - The confirmation screen (Confirm page) works regardless.
- **5-image synthetic test:** Not run (no Tesseract binary). Recorded here per spec.
  - If Tesseract is installed later, re-run: `python backend/scripts/ocr_test.py`

### Summary
| Check | Result | Action |
|---|---|---|
| Oracle connect | FAIL (no oracledb for Python 3.14) | Use SQLite |
| Oracle DDL | Schema written (`sql/oracle.sql`) | Portable Oracle DDL preserved |
| Tesseract OCR | Binary not in PATH | Fallback to typed prescription entry |

---

## Phase Progress

- [x] **Phase 0:** Feasibility gates, project skeleton, environment, Screen 1 (`/review/prescription`).
- [x] **Spec Patch v1.4:** Applied `DOSE-001` dosage checking, updated `SPEC.md`, `sqlite.sql`, `oracle.sql`, created `dose_rules.json`.
- [x] **Database Requirements:** Added 3NF Normalization, Indexes, Triggers, Transactions, Users & Privileges, CLOB, BLOB.
- [x] **DDInter Raw Datasets:** Verified present in `backend/data/raw/ddinter/` (Codes A, B, D, H, L, P, R, V). **STRICT: DO NOT REMOVE.**
- [x] **Phase 1:** Database initialization, schema creation, DDInter (160K pairs) loader, and seed rules (`rxraksha.db`). (5 tests)
- [x] **Phase 2:** Normalization engine (`NormalizeEngine`), alias/brand/generic resolution, dose/freq parsing (`/api/normalize`). (19 tests)
- [x] **Phase 3:** Clinical rules engine (`RulesEngine`) evaluating `DDI-001`, `DUP-001`, `ALLERGY-001`, `ORGAN-001`, `DOSE-001`. (10 tests)
- [x] **Phase 4:** Scoring engine (`score`) with deduction ledger, worst-severity hard caps, and floor logic. (12 tests)
- [x] **Phase 5:** Explanation templates (deterministic headline/why/action) and human-verified AI summarizer. (6 tests)
- [x] **Phase 6:** Frontend UI: Static decision-support report UI at `/` and React 19 router skeleton (`/review/prescription`).
- [x] **Phase 7:** Calibrated major-severity floor, judge one-pager `README.md`, rehearsed demo script, and complete test suite (52/52 green).
