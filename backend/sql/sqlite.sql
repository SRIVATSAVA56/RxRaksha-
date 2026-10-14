-- rxraksha/backend/sql/sqlite.sql
-- RxRaksha SQLite Database DDL — v1.4
-- Features: 3NF Normalization, Tables, Indexes, CLOB (TEXT), BLOB, Triggers, Transactions, Privileges

PRAGMA foreign_keys = ON;

-- ============================================================================
-- 1. NORMALIZED CORE DRUG & BRAND TABLES (3NF)
-- ============================================================================

-- Generic Drugs
CREATE TABLE IF NOT EXISTS drug (
  drug_id INTEGER PRIMARY KEY AUTOINCREMENT,
  generic_name TEXT NOT NULL UNIQUE,
  name_norm TEXT,
  atc_code TEXT,
  source_ref TEXT
);

-- Drug Aliases / Synonyms (1:N from drug)
CREATE TABLE IF NOT EXISTS drug_alias (
  alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
  drug_id INTEGER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE,
  alias_name TEXT NOT NULL,
  alias_norm TEXT,
  strength_mg REAL,
  source TEXT DEFAULT 'seed',
  UNIQUE (drug_id, alias_name)
);

-- Commercial Brands (1:N or M:N with generic ingredients)
CREATE TABLE IF NOT EXISTS brand (
  brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
  brand_name TEXT NOT NULL UNIQUE,
  name_norm TEXT NOT NULL UNIQUE,
  source_ref TEXT NOT NULL
);

-- Associative Table: Brand to Ingredients (Normalized M:N)
CREATE TABLE IF NOT EXISTS brand_ingredient (
  brand_id INTEGER NOT NULL REFERENCES brand(brand_id) ON DELETE CASCADE,
  drug_id INTEGER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE,
  PRIMARY KEY (brand_id, drug_id)
);

-- Drug Classes (e.g. NSAID, ACE Inhibitor, Beta Blocker)
CREATE TABLE IF NOT EXISTS drug_class (
  class_id INTEGER PRIMARY KEY AUTOINCREMENT,
  class_name TEXT NOT NULL UNIQUE,
  name_norm TEXT NOT NULL UNIQUE,
  aliases TEXT,
  description TEXT -- CLOB
);

-- Associative Table: Drug Class Membership (Normalized M:N)
CREATE TABLE IF NOT EXISTS drug_class_member (
  class_id INTEGER NOT NULL REFERENCES drug_class(class_id) ON DELETE CASCADE,
  drug_id INTEGER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE,
  PRIMARY KEY (class_id, drug_id)
);

-- ============================================================================
-- 2. CLINICAL SAFETY & INTERACTION RULES TABLES
-- ============================================================================

-- Drug-Drug Interactions (Canonical pair: drug_a_id < drug_b_id)
CREATE TABLE IF NOT EXISTS interaction (
  interaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
  drug_a_id INTEGER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE,
  drug_b_id INTEGER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE,
  severity TEXT NOT NULL,
  evidence_source TEXT DEFAULT 'DDInter 2.0 bulk download',
  kb_version TEXT DEFAULT 'ddinter-2.0',
  source TEXT DEFAULT 'DDINTER',
  source_ref TEXT DEFAULT 'DDInter 2.0',
  source_severity TEXT,
  mechanism TEXT,    -- CLOB: detailed pharmacokinetic/dynamic mechanism
  management TEXT,   -- CLOB: clinical recommendation / mitigation
  side_effects TEXT, -- CLOB: reported side effects
  human_verified INTEGER NOT NULL DEFAULT 0,
  CHECK (drug_a_id < drug_b_id),
  UNIQUE (drug_a_id, drug_b_id, evidence_source)
);

-- Allergy Cross-Sensitivity Rules (Class to Class)
CREATE TABLE IF NOT EXISTS allergy_cross_sensitivity (
  cross_id INTEGER PRIMARY KEY AUTOINCREMENT,
  trigger_class_id INTEGER NOT NULL REFERENCES drug_class(class_id) ON DELETE CASCADE,
  affected_class_id INTEGER NOT NULL REFERENCES drug_class(class_id) ON DELETE CASCADE,
  severity TEXT NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MODERATE','LOW','critical','high','moderate','low')),
  mechanism TEXT NOT NULL, -- CLOB
  effects TEXT,            -- CLOB
  source_ref TEXT NOT NULL,
  human_verified INTEGER NOT NULL DEFAULT 0
);

-- Disease / Organ Impairment Rules (Kidney / Liver)
CREATE TABLE IF NOT EXISTS disease_rule (
  rule_key TEXT PRIMARY KEY,
  class_id INTEGER REFERENCES drug_class(class_id) ON DELETE CASCADE,
  drug_id INTEGER REFERENCES drug(drug_id) ON DELETE CASCADE,
  factor_type TEXT NOT NULL CHECK (factor_type IN ('KIDNEY','LIVER')),
  min_level TEXT NOT NULL CHECK (min_level IN ('MILD','MODERATE','SEVERE')),
  severity TEXT NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MODERATE','LOW','critical','high','moderate','low')),
  rationale TEXT NOT NULL, -- CLOB
  effects TEXT,            -- CLOB
  source_ref TEXT NOT NULL,
  human_verified INTEGER NOT NULL DEFAULT 0,
  CHECK (class_id IS NOT NULL OR drug_id IS NOT NULL)
);

-- Dosage Limits Rule Table (DOSE-001 from Spec Patch v1.4)
CREATE TABLE IF NOT EXISTS dose_rule (
  rule_id TEXT PRIMARY KEY,
  rule_key TEXT,
  drug_id INTEGER REFERENCES drug(drug_id) ON DELETE CASCADE,
  class_id INTEGER REFERENCES drug_class(class_id) ON DELETE CASCADE,
  max_daily_mg REAL NOT NULL,
  max_daily_dose_mg REAL,
  severity TEXT DEFAULT 'HIGH',
  rationale TEXT,          -- CLOB
  effects TEXT,            -- CLOB
  population TEXT DEFAULT 'adult',
  note TEXT,
  source TEXT,
  source_ref TEXT DEFAULT 'HAND_CODED',
  human_verified INTEGER NOT NULL DEFAULT 0
);

-- ============================================================================
-- 3. BLOB & AUDIT TABLES
-- ============================================================================

-- Storing Scanned Prescription Uploads with BLOB
CREATE TABLE IF NOT EXISTS prescription_document (
  document_id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT NOT NULL,
  mime_type TEXT NOT NULL CHECK (mime_type IN ('image/png', 'image/jpeg', 'application/pdf')),
  file_size_bytes INTEGER NOT NULL,
  image_data BLOB,              -- BLOB: Binary storage of prescription image
  ocr_extracted_text TEXT,      -- CLOB: Extracted text from OCR engine
  uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Audit Log for Analysis Runs & Clinical Overrides
CREATE TABLE IF NOT EXISTS audit_log (
  audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,     -- 'ANALYSIS_EXECUTION', 'MANUAL_OVERRIDE', 'SEED_UPDATE'
  entity_key TEXT,
  payload_json TEXT,            -- CLOB: Input & findings payload snapshot
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Knowledge Base Version Tag
CREATE TABLE IF NOT EXISTS kb_version (
  version_tag TEXT NOT NULL,
  loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  notes TEXT -- CLOB
);

-- ============================================================================
-- 4. PERFORMANCE INDEXES
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_drug_name ON drug(generic_name);
CREATE INDEX IF NOT EXISTS idx_drug_name_norm ON drug(name_norm);
CREATE INDEX IF NOT EXISTS idx_drug_alias_name ON drug_alias(alias_name);
CREATE INDEX IF NOT EXISTS idx_drug_alias_norm ON drug_alias(alias_norm);
CREATE INDEX IF NOT EXISTS idx_drug_alias_drug_id ON drug_alias(drug_id);
CREATE INDEX IF NOT EXISTS idx_brand_name_norm ON brand(name_norm);
CREATE INDEX IF NOT EXISTS idx_brand_ing_drug ON brand_ingredient(drug_id);
CREATE INDEX IF NOT EXISTS idx_class_member_drug ON drug_class_member(drug_id);
CREATE INDEX IF NOT EXISTS idx_interaction_pair ON interaction(drug_a_id, drug_b_id);
CREATE INDEX IF NOT EXISTS idx_interaction_drug_b ON interaction(drug_b_id);
CREATE INDEX IF NOT EXISTS idx_interaction_severity ON interaction(severity);
CREATE INDEX IF NOT EXISTS idx_allergy_cross_trigger ON allergy_cross_sensitivity(trigger_class_id);
CREATE INDEX IF NOT EXISTS idx_disease_rule_drug ON disease_rule(drug_id);
CREATE INDEX IF NOT EXISTS idx_disease_rule_class ON disease_rule(class_id);
CREATE INDEX IF NOT EXISTS idx_disease_rule_factor ON disease_rule(factor_type, min_level);
CREATE INDEX IF NOT EXISTS idx_dose_rule_drug ON dose_rule(drug_id);
CREATE INDEX IF NOT EXISTS idx_dose_rule_class ON dose_rule(class_id);

-- ============================================================================
-- 5. DATABASE TRIGGERS
-- ============================================================================

-- Trigger 1: Ensure name_norm is always populated on drug insertion
CREATE TRIGGER IF NOT EXISTS trg_drug_normalize_insert
AFTER INSERT ON drug
FOR EACH ROW
WHEN NEW.name_norm IS NULL
BEGIN
  UPDATE drug 
  SET name_norm = LOWER(TRIM(NEW.generic_name)) 
  WHERE drug_id = NEW.drug_id;
END;

-- Trigger 2: Ensure alias_norm is populated on alias insertion
CREATE TRIGGER IF NOT EXISTS trg_alias_normalize_insert
AFTER INSERT ON drug_alias
FOR EACH ROW
WHEN NEW.alias_norm IS NULL
BEGIN
  UPDATE drug_alias 
  SET alias_norm = LOWER(TRIM(NEW.alias_name)) 
  WHERE alias_id = NEW.alias_id;
END;

-- Trigger 3: Sync rule_key and max_daily_dose_mg on dose_rule
CREATE TRIGGER IF NOT EXISTS trg_dose_rule_sync_insert
AFTER INSERT ON dose_rule
FOR EACH ROW
BEGIN
  UPDATE dose_rule 
  SET rule_key = COALESCE(NEW.rule_key, NEW.rule_id),
      max_daily_dose_mg = COALESCE(NEW.max_daily_dose_mg, NEW.max_daily_mg)
  WHERE rule_id = NEW.rule_id;
END;

-- Trigger 4: Auto-audit log when a dose_rule is modified or inserted
CREATE TRIGGER IF NOT EXISTS trg_audit_dose_rule_insert
AFTER INSERT ON dose_rule
FOR EACH ROW
BEGIN
  INSERT INTO audit_log (event_type, entity_key, payload_json)
  VALUES ('DOSE_RULE_ADDED', NEW.rule_id, json_object(
    'rule_id', NEW.rule_id,
    'max_daily_mg', NEW.max_daily_mg,
    'severity', NEW.severity
  ));
END;
