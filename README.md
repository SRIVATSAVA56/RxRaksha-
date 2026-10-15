# RxRaksha — Prescription Safety Decision Support

**Problem:** Adverse drug events from interactions, duplicate therapy, and missed allergies are a leading cause of preventable harm. RxRaksha screens a prescription in <1s with a fully deterministic clinical rule engine.

## Quick start
```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open [http://127.0.0.1:8000](http://127.0.0.1:8000) — click **"Analyze prescription"**.

## Architecture
```
[OCR optional] -> Normalize (fuzzy match, 1,942 drugs)
-> Rules (DDI/DUP/ALLERGY/ORGAN/DOSE, 160K DDInter pairs)
-> Score (0-100, capped + floored, full ledger)
-> Report JSON + UI
```

## Design Invariants
- **Deterministic first:** same input -> same output. AI summaries require human verification (D10 gate) before entering any report.
- **Honest missing-data accounting:** unknown items lower coverage and score — never silently ignored.
- **Fail-safe:** unparseable lines are flagged for human review, never guessed.

## Verification
```bash
cd backend
python -m pytest   # 75 tests passing
```

## Demo Cases
1. **Warf 5mg OD + Crocin 500mg + Dolo-650** -> `25/Caution` (Moderate DDI + duplicate therapy)
2. **Augmentin 625 TDS + Penicillin Allergy** -> `100/Critical` (Contraindicated allergy clash)
3. **Thyronorm 100mcg OD** -> `0/Safe` (Clean baseline, zero findings)
4. **Warfarin + Aspirin + Metoprolol + Omeprazole** -> `75/High Risk` (Major bleeding hazard stack)

