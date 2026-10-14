"""
oracle_gate.py — Phase 0 Oracle Database Free gate test.

Runs 4 checks:
  1. Can connect to Oracle via python-oracledb thin mode
  2. Can create/drop a test table (DDL works)
  3. Bulk insert ~1000 rows completes in < 5 s (proxy for 300k load)
  4. Query returns correct data

If ALL 4 pass  → print ORACLE_GATE=PASS
If ANY fails   → print ORACLE_GATE=FAIL with reason, exit 1

Usage:
  python oracle_gate.py
  # reads ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN from env or .env
"""

import os
import sys
import time

def load_dotenv_simple():
    """Load .env file from parent directory if it exists."""
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


def run_gate():
    load_dotenv_simple()

    user = os.environ.get("ORACLE_USER", "rxraksha")
    password = os.environ.get("ORACLE_PASSWORD", "change_me")
    dsn = os.environ.get("ORACLE_DSN", "localhost:1521/FREEPDB1")

    results = {}

    # --- Check 1: Connect ---
    print("CHECK 1: Connecting to Oracle...")
    try:
        import oracledb
        oracledb.defaults.fetch_lobs = False
        conn = oracledb.connect(user=user, password=password, dsn=dsn)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM DUAL")
        row = cursor.fetchone()
        assert row[0] == 1, "SELECT 1 FROM DUAL did not return 1"
        results["connect"] = "PASS"
        print("  PASS: Connected to Oracle, SELECT 1 FROM DUAL = 1")
    except Exception as e:
        results["connect"] = f"FAIL: {e}"
        print(f"  FAIL: {e}")
        _report(results)
        return 1

    # --- Check 2: DDL create/drop ---
    print("CHECK 2: DDL create/drop table...")
    try:
        try:
            cursor.execute("DROP TABLE rxraksha_gate_test PURGE")
        except Exception:
            pass
        cursor.execute("""
            CREATE TABLE rxraksha_gate_test (
                id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                name VARCHAR2(200) NOT NULL,
                val NUMBER(10)
            )
        """)
        conn.commit()
        results["ddl"] = "PASS"
        print("  PASS: Created rxraksha_gate_test table")
    except Exception as e:
        results["ddl"] = f"FAIL: {e}"
        print(f"  FAIL: {e}")
        _report(results)
        return 1

    # --- Check 3: Bulk insert performance ---
    print("CHECK 3: Bulk insert 1000 rows...")
    try:
        data = [(f"drug_{i}", i) for i in range(1000)]
        t0 = time.perf_counter()
        cursor.executemany(
            "INSERT INTO rxraksha_gate_test (name, val) VALUES (:1, :2)",
            data,
        )
        conn.commit()
        elapsed = time.perf_counter() - t0
        if elapsed > 5.0:
            results["bulk"] = f"FAIL: took {elapsed:.2f}s (> 5s limit)"
            print(f"  FAIL: bulk insert took {elapsed:.2f}s")
        else:
            results["bulk"] = f"PASS ({elapsed:.2f}s)"
            print(f"  PASS: 1000 rows in {elapsed:.2f}s")
    except Exception as e:
        results["bulk"] = f"FAIL: {e}"
        print(f"  FAIL: {e}")

    # --- Check 4: Query correctness ---
    print("CHECK 4: Query correctness...")
    try:
        cursor.execute("SELECT COUNT(*) FROM rxraksha_gate_test")
        count = cursor.fetchone()[0]
        assert count == 1000, f"Expected 1000 rows, got {count}"
        cursor.execute(
            "SELECT name, val FROM rxraksha_gate_test WHERE val = :v",
            {"v": 42},
        )
        row = cursor.fetchone()
        assert row is not None and row[0] == "drug_42", f"Unexpected row: {row}"
        results["query"] = "PASS"
        print("  PASS: Query returned correct data")
    except Exception as e:
        results["query"] = f"FAIL: {e}"
        print(f"  FAIL: {e}")

    # --- Cleanup ---
    try:
        cursor.execute("DROP TABLE rxraksha_gate_test PURGE")
        conn.commit()
    except Exception:
        pass

    try:
        cursor.close()
        conn.close()
    except Exception:
        pass

    return _report(results)


def _report(results):
    print("\n--- ORACLE GATE RESULTS ---")
    all_pass = True
    for check, status in results.items():
        tag = "PASS" if status.startswith("PASS") else "FAIL"
        if tag == "FAIL":
            all_pass = False
        print(f"  {check}: {status}")

    if all_pass and len(results) == 4:
        print("\nORACLE_GATE=PASS")
        print("Recommendation: set DB_BACKEND=oracle")
        return 0
    else:
        print("\nORACLE_GATE=FAIL")
        print("Recommendation: set DB_BACKEND=sqlite")
        return 1


if __name__ == "__main__":
    sys.exit(run_gate())
