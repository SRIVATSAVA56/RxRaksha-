import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import phase1_load as p1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="delete existing DB first")
    args = ap.parse_args()

    conn = p1.init_db(fresh=args.fresh)
    p1.self_check(conn)
    d_stats, unknown = p1.load_ddinter(conn)
    n_dose = p1.load_dose_rules(conn)
    n_alias = p1.load_aliases(conn)

    print("\n===== PHASE 1 LOAD SUMMARY =====")
    for k in ["files_loaded", "rows_read", "pairs_accepted", "duplicates_skipped",
              "skipped_empty", "skipped_self_pair", "drugs_total", "interactions_total"]:
        print(f"  {k:22}: {d_stats.get(k, 0)}")
    print(f"  dose_rules loaded     : {n_dose}")
    print(f"  aliases loaded        : {n_alias}")
    print(f"  unrecognized levels   : {unknown or 'none'}")
    print("\n  severity breakdown:")
    for sev, n in conn.execute(
        f"SELECT {p1.C_IA_SEV}, COUNT(*) FROM {p1.T_INTER} GROUP BY 1 ORDER BY 2 DESC"):
        print(f"    {sev:15}: {n}")
    row = conn.execute(
        f"SELECT r.{p1.C_DOSE_ID}, d.{p1.C_DRUG_NAME}, r.{p1.C_DOSE_MAX} "
        f"FROM {p1.T_DOSE} r LEFT JOIN {p1.T_DRUG} d ON d.{p1.C_DRUG_ID}=r.{p1.C_DOSE_FK} "
        f"WHERE r.{p1.C_DOSE_ID}='DOSE-PARACETAMOL-MAX'").fetchone()
    print(f"\n  DOSE-001 check        : {row}")
    print("================================\n")

if __name__ == "__main__":
    main()
