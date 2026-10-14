-- rxraksha/backend/sql/oracle.sql
-- Oracle Database Free / 23c / 19c DDL — v1.4
-- Features: 3NF Normalization, Tables, Indexes, CLOB, BLOB, Triggers, Transactions, Users & Privileges

-- ============================================================================
-- 1. USERS, ROLES AND PRIVILEGES
-- ============================================================================
-- Run as DBA (e.g. SYSTEM or SYS):
-- CREATE USER rxraksha IDENTIFIED BY change_me DEFAULT TABLESPACE users QUOTA UNLIMITED ON users;
-- CREATE ROLE rxraksha_app_role;
-- CREATE ROLE rxraksha_read_role;
--
-- GRANT CREATE SESSION, CREATE TABLE, CREATE VIEW, CREATE TRIGGER, CREATE SEQUENCE, CREATE PROCEDURE TO rxraksha;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON drug TO rxraksha_app_role;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON interaction TO rxraksha_app_role;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON dose_rule TO rxraksha_app_role;
-- GRANT SELECT ON drug TO rxraksha_read_role;
-- GRANT SELECT ON interaction TO rxraksha_read_role;
-- GRANT SELECT ON dose_rule TO rxraksha_read_role;

-- ============================================================================
-- 2. NORMALIZED CORE DRUG & BRAND TABLES (3NF)
-- ============================================================================

CREATE TABLE drug (
  drug_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  generic_name VARCHAR2(200) NOT NULL UNIQUE,
  name_norm VARCHAR2(200) NOT NULL UNIQUE,
  atc_code VARCHAR2(200),
  source_ref VARCHAR2(500)
);

CREATE TABLE drug_alias (
  alias_norm VARCHAR2(200) PRIMARY KEY,
  drug_id NUMBER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE
);

CREATE TABLE brand (
  brand_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  brand_name VARCHAR2(200) NOT NULL UNIQUE,
  name_norm VARCHAR2(200) NOT NULL UNIQUE,
  source_ref VARCHAR2(500) NOT NULL
);

CREATE TABLE brand_ingredient (
  brand_id NUMBER NOT NULL REFERENCES brand(brand_id) ON DELETE CASCADE,
  drug_id NUMBER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE,
  PRIMARY KEY (brand_id, drug_id)
);

CREATE TABLE drug_class (
  class_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  class_name VARCHAR2(200) NOT NULL UNIQUE,
  name_norm VARCHAR2(200) NOT NULL UNIQUE,
  aliases VARCHAR2(400),
  description CLOB -- CLOB
);

CREATE TABLE drug_class_member (
  class_id NUMBER NOT NULL REFERENCES drug_class(class_id) ON DELETE CASCADE,
  drug_id NUMBER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE,
  PRIMARY KEY (class_id, drug_id)
);

-- ============================================================================
-- 3. CLINICAL SAFETY & INTERACTION RULES TABLES
-- ============================================================================

CREATE TABLE interaction (
  interaction_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  drug_a_id NUMBER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE,
  drug_b_id NUMBER NOT NULL REFERENCES drug(drug_id) ON DELETE CASCADE,
  source VARCHAR2(200) NOT NULL CHECK (source IN ('DDINTER','HAND_CODED_DEMO')),
  source_ref VARCHAR2(500) NOT NULL,
  source_severity VARCHAR2(200),
  severity VARCHAR2(200) NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MODERATE','LOW','UNKNOWN')),
  mechanism CLOB,    -- CLOB: Pharmacokinetic/dynamic explanation
  management CLOB,   -- CLOB: Clinical advice for dispensing clinician
  side_effects CLOB, -- CLOB: Adverse reactions
  human_verified NUMBER(1) DEFAULT 0 NOT NULL,
  CONSTRAINT pair_order CHECK (drug_a_id < drug_b_id),
  CONSTRAINT pair_uq UNIQUE (drug_a_id, drug_b_id, source)
);

CREATE TABLE allergy_cross_sensitivity (
  cross_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  trigger_class_id NUMBER NOT NULL REFERENCES drug_class(class_id) ON DELETE CASCADE,
  affected_class_id NUMBER NOT NULL REFERENCES drug_class(class_id) ON DELETE CASCADE,
  severity VARCHAR2(200) NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MODERATE','LOW')),
  mechanism CLOB NOT NULL, -- CLOB
  effects CLOB,            -- CLOB
  source_ref VARCHAR2(500) NOT NULL,
  human_verified NUMBER(1) DEFAULT 0 NOT NULL
);

CREATE TABLE disease_rule (
  rule_key VARCHAR2(200) PRIMARY KEY,
  class_id NUMBER REFERENCES drug_class(class_id) ON DELETE CASCADE,
  drug_id NUMBER REFERENCES drug(drug_id) ON DELETE CASCADE,
  factor_type VARCHAR2(200) NOT NULL CHECK (factor_type IN ('KIDNEY','LIVER')),
  min_level VARCHAR2(200) NOT NULL CHECK (min_level IN ('MILD','MODERATE','SEVERE')),
  severity VARCHAR2(200) NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MODERATE','LOW')),
  rationale CLOB NOT NULL, -- CLOB
  effects CLOB,            -- CLOB
  source_ref VARCHAR2(500) NOT NULL,
  human_verified NUMBER(1) DEFAULT 0 NOT NULL,
  CHECK (class_id IS NOT NULL OR drug_id IS NOT NULL)
);

-- Dose Checking Rules (v1.4 DOSE-001)
CREATE TABLE dose_rule (
  rule_key VARCHAR2(200) PRIMARY KEY,
  drug_id NUMBER REFERENCES drug(drug_id) ON DELETE CASCADE,
  class_id NUMBER REFERENCES drug_class(class_id) ON DELETE CASCADE,
  max_daily_dose_mg NUMBER NOT NULL,
  severity VARCHAR2(200) NOT NULL CHECK (severity IN ('CRITICAL','HIGH','MODERATE','LOW')),
  rationale CLOB NOT NULL, -- CLOB
  effects CLOB,            -- CLOB
  source_ref VARCHAR2(500) NOT NULL,
  human_verified NUMBER(1) DEFAULT 0 NOT NULL,
  CHECK (drug_id IS NOT NULL OR class_id IS NOT NULL)
);

-- ============================================================================
-- 4. BLOB & AUDIT TABLES
-- ============================================================================

-- Storing Scanned Prescription Uploads with BLOB
CREATE TABLE prescription_document (
  document_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  filename VARCHAR2(255) NOT NULL,
  mime_type VARCHAR2(100) NOT NULL CHECK (mime_type IN ('image/png', 'image/jpeg', 'application/pdf')),
  file_size_bytes NUMBER NOT NULL,
  image_data BLOB,             -- BLOB: Prescription binary image data
  ocr_extracted_text CLOB,     -- CLOB: OCR text
  uploaded_at TIMESTAMP DEFAULT SYSTIMESTAMP
);

-- Audit Log for Analysis Runs
CREATE TABLE audit_log (
  audit_id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_type VARCHAR2(100) NOT NULL,
  entity_key VARCHAR2(200),
  payload_json CLOB,           -- CLOB: Request and finding details
  created_at TIMESTAMP DEFAULT SYSTIMESTAMP
);

CREATE TABLE kb_version (
  version_tag VARCHAR2(200) NOT NULL,
  loaded_at TIMESTAMP DEFAULT SYSTIMESTAMP,
  notes CLOB -- CLOB
);

-- ============================================================================
-- 5. PERFORMANCE INDEXES
-- ============================================================================

CREATE INDEX idx_ora_drug_norm ON drug(name_norm);
CREATE INDEX idx_ora_brand_norm ON brand(name_norm);
CREATE INDEX idx_ora_alias_drug ON drug_alias(drug_id);
CREATE INDEX idx_ora_brand_ing_drug ON brand_ingredient(drug_id);
CREATE INDEX idx_ora_class_member_drug ON drug_class_member(drug_id);
CREATE INDEX idx_ora_interaction_pair ON interaction(drug_a_id, drug_b_id);
CREATE INDEX idx_ora_interaction_b ON interaction(drug_b_id);
CREATE INDEX idx_ora_interaction_sev ON interaction(severity);
CREATE INDEX idx_ora_allergy_cross ON allergy_cross_sensitivity(trigger_class_id);
CREATE INDEX idx_ora_disease_drug ON disease_rule(drug_id);
CREATE INDEX idx_ora_disease_class ON disease_rule(class_id);
CREATE INDEX idx_ora_dose_drug ON dose_rule(drug_id);
CREATE INDEX idx_ora_dose_class ON dose_rule(class_id);

-- ============================================================================
-- 6. TRIGGERS (AUDIT & VALIDATION)
-- ============================================================================

-- Trigger 1: Auto-audit log when a dose_rule is added
CREATE OR REPLACE TRIGGER trg_audit_dose_rule_ora
AFTER INSERT ON dose_rule
FOR EACH ROW
BEGIN
  INSERT INTO audit_log (event_type, entity_key, payload_json)
  VALUES ('DOSE_RULE_ADDED', :NEW.rule_key, 
          'Rule added: ' || :NEW.rule_key || ' limit=' || TO_CHAR(:NEW.max_daily_dose_mg) || 'mg');
END;
/

-- ============================================================================
-- 7. TRANSACTIONS EXAMPLE (PL/SQL)
-- ============================================================================
-- BEGIN
--   SAVEPOINT before_batch;
--   -- Insert batch
--   COMMIT;
-- EXCEPTION
--   WHEN OTHERS THEN
--     ROLLBACK TO before_batch;
--     RAISE;
-- END;
-- /
