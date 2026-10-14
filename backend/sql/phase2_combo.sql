-- Phase 2: multi-ingredient (combo) products. Additive, idempotent.
-- A combo alias cannot live in drug_alias (which maps to ONE drug_id),
-- so components get their own table. Keyed by normalized alias text.
CREATE TABLE IF NOT EXISTS combo_component (
  combo_alias_name TEXT NOT NULL,
  drug_id          INTEGER NOT NULL REFERENCES drug(drug_id),
  strength_mg      REAL,
  PRIMARY KEY (combo_alias_name, drug_id)
);
CREATE INDEX IF NOT EXISTS idx_combo_drug ON combo_component(drug_id);
