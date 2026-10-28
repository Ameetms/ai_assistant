"""
STEP 5 - Load your curated  final_data/  CSV files into a SQLite database and verify them.

final_data/ is the OFFICIAL structured data of this project. This script:
  1. Loads every CSV in final_data/ as a table into  data/apple_final.db
        income_statement, balance_sheet, cash_flow, sales_by_category,
        sales_by_region, sales_by_country   (the tables the assistant will query)
        data_dictionary, data_checks, all_facts_long   (documentation / traceability)
  2. Checks that every column is described in data_dictionary.
  3. Re-calculates the accounting checks itself (it does not just trust data_checks.csv).
  4. If data/apple_financials.db from Step 2/4 exists, compares final_data against it.
     Those two were built independently, so agreement is strong evidence both are right.
  5. Prints the coverage (which years each table has) and known gaps.

It only READS final_data/. Safe to run many times.

How to run (from inside your ai_assistant folder):
    python 05_load_final_data.py
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
FINAL_DIR = ROOT / "final_data"
DB_PATH = ROOT / "data" / "apple_final.db"
OLD_DB = ROOT / "data" / "apple_financials.db"

DATA_TABLES = [
    "income_statement", "balance_sheet", "cash_flow",
    "sales_by_category", "sales_by_region", "sales_by_country",
]
DOC_TABLES = ["data_dictionary", "data_checks", "all_facts_long"]

fails = 0


def result(ok: bool, text: str) -> None:
    global fails
    fails += 0 if ok else 1
    print(f"  [{'PASS' if ok else 'FAIL'}] {text}")


def load() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    for name in DATA_TABLES + DOC_TABLES:
        f = FINAL_DIR / f"{name}.csv"
        if not f.exists():
            print(f"  [FAIL] missing file: {f}")
            sys.exit(1)
        df = pd.read_csv(f, encoding="utf-8-sig")
        df.to_sql(name, con, index=False)
        print(f"  loaded {name:<20} {len(df):>4} rows x {df.shape[1]:>2} columns")
    return con


def check_dictionary(con) -> None:
    print("\nDictionary coverage")
    d = pd.read_sql("SELECT table_name, column_name FROM data_dictionary", con)
    for t in DATA_TABLES:
        cols = set(pd.read_sql(f"SELECT * FROM {t} LIMIT 1", con).columns)
        doc = set(d[d.table_name == t].column_name)
        result(not (cols - doc), f"{t}: every column documented"
               + (f"  (missing: {sorted(cols - doc)})" if cols - doc else ""))


def check_identities(con) -> None:
    print("\nAccounting checks (recalculated here)")
    inc = pd.read_sql("SELECT * FROM income_statement", con).set_index("fiscal_year")
    bal = pd.read_sql("SELECT * FROM balance_sheet", con).set_index("fiscal_year")
    cf = pd.read_sql("SELECT * FROM cash_flow", con).set_index("fiscal_year")
    cat = pd.read_sql("SELECT * FROM sales_by_category", con)
    reg = pd.read_sql("SELECT * FROM sales_by_region", con)
    cty = pd.read_sql("SELECT * FROM sales_by_country", con)

    def same(name, a, b, tol=0.5):
        d = (a - b).abs().dropna()
        result(len(d) > 0 and bool((d <= tol).all()), f"{name}  ({len(d)} years)")

    same("gross margin = net sales - cost of sales", inc.gross_margin, inc.net_sales - inc.cost_of_sales)
    same("operating income = gross margin - operating expenses",
         inc.operating_income, inc.gross_margin - inc.total_operating_expenses)
    same("net income = pre-tax income - tax", inc.net_income, inc.income_before_tax - inc.income_tax_provision)
    same("products + services = net sales", inc.products_net_sales + inc.services_net_sales, inc.net_sales)
    same("total assets = total liabilities + equity",
         bal.total_assets, bal.total_liabilities + bal.total_shareholders_equity)
    same("current + non-current assets = total assets",
         bal.total_current_assets + bal.total_non_current_assets, bal.total_assets)
    same("operating + investing + financing = net change in cash",
         cf.operating_cash_flow + cf.investing_cash_flow + cf.financing_cash_flow, cf.net_change_in_cash)
    same("5 product categories = net sales", cat.groupby("fiscal_year").net_sales_usd_m.sum(), inc.net_sales)
    same("5 regions = net sales", reg.groupby("fiscal_year").net_sales_usd_m.sum(), inc.net_sales)
    same("U.S. + China + other = net sales", cty.groupby("fiscal_year").net_sales_usd_m.sum(), inc.net_sales)

    ck = pd.read_sql("SELECT status FROM data_checks", con).status
    bad_status = ck[(ck != "OK") & ~ck.str.startswith("CHANGED")]
    result(bad_status.empty, f"data_checks.csv: {int((ck == 'OK').sum())} OK, "
           f"{int(ck.str.startswith('CHANGED').sum())} restated-line notes, {len(bad_status)} problems")


def cross_check(con) -> None:
    print("\nCross-check against Step 2/4 database (independent build)")
    if not OLD_DB.exists():
        print("  skipped (data/apple_financials.db not found; this is optional)")
        return
    old = sqlite3.connect(OLD_DB)
    try:
        m = pd.read_sql("SELECT metric, fiscal_year, value FROM financial_metrics", old).pivot(
            index="fiscal_year", columns="metric", values="value")
    except Exception:
        print("  skipped (run 04_build_metrics.py to enable this check)")
        return
    rename = {
        "income_before_tax": "income_before_taxes", "shares_basic_thousands": "shares_used_basic",
        "shares_diluted_thousands": "shares_used_diluted", "accounts_receivable_net": "accounts_receivable",
        "marketable_securities_non_current": "marketable_securities_noncurrent",
        "term_debt_non_current": "term_debt_noncurrent", "deferred_revenue": "deferred_revenue_current",
    }
    flip = {"capital_expenditures", "dividends_paid", "share_repurchases"}  # old build stored these as positive
    n = diff = 0
    for t in ["income_statement", "balance_sheet", "cash_flow"]:
        df = pd.read_sql(f"SELECT * FROM {t}", con).set_index("fiscal_year")
        for col in df.columns:
            mc = rename.get(col, col)
            if mc not in m.columns:
                continue
            for fy, v in df[col].items():
                o = m.loc[fy, mc] if fy in m.index else None
                if pd.isna(v) or o is None or pd.isna(o):
                    continue
                o = -o if mc in flip else o
                n += 1
                diff += abs(v - o) > 0.005
    result(diff == 0, f"{n} statement values compared, {diff} differences")


def coverage(con) -> None:
    print("\nCoverage (fiscal years per table)")
    last_year = con.execute("SELECT MAX(fiscal_year) FROM income_statement").fetchone()[0]
    first_year = con.execute("SELECT MIN(fiscal_year) FROM income_statement").fetchone()[0]
    gaps = ["No quarterly numbers in these tables (quarterly figures exist only in the 10-Q text files)."]
    gaps.append(f"No numbers after FY{last_year} yet.")
    for t in DATA_TABLES:
        lo, hi, k = con.execute(f"SELECT MIN(fiscal_year), MAX(fiscal_year), COUNT(DISTINCT fiscal_year) FROM {t}").fetchone()
        print(f"  {t:<18} FY{lo}-FY{hi}  ({k} years)")
        if lo > first_year:
            gaps.append(f"{t} starts in FY{lo}, not FY{first_year}.")
        if hi < last_year:
            gaps.append(f"{t} stops at FY{hi}, not FY{last_year}.")
    for col in ["cash_beginning", "cash_ending"]:
        yrs = [r[0] for r in con.execute(f"SELECT fiscal_year FROM cash_flow WHERE {col} IS NULL")]
        if yrs:
            gaps.append(f"cash_flow.{col} is empty for FY{', FY'.join(map(str, yrs))}.")
    print("\nKnown gaps to be aware of:")
    for g in gaps:
        print(f"  - {g}")


def main() -> int:
    if not FINAL_DIR.is_dir():
        print("ERROR: folder 'final_data' not found next to this script.")
        return 1
    print("Loading final_data into data/apple_final.db")
    con = load()
    check_dictionary(con)
    check_identities(con)
    cross_check(con)
    coverage(con)
    con.commit()
    con.close()
    print(f"\nDatabase saved to: {DB_PATH}")
    if fails:
        print(f"\n{fails} check(s) FAILED. Send me this whole output and do not continue yet.")
        return 1
    print("\nAll checks passed. Next: python 06_text_to_sql.py --demo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
