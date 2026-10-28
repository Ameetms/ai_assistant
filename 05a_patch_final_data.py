"""
STEP 5a - Fix two small gaps in final_data/ (run this BEFORE 05_load_final_data.py).

The checks found two things your build script missed. Both values exist in your own
raw files (tables/Financial_Report_2024_CSV and the FY2024 10-K), so nothing is invented:

  1. sales_by_country.csv has no FY2024 rows.
        U.S. 142,196 + China 66,952 + Other countries 181,887 = 391,035 = FY2024 net sales
     (Source: 10-K FY2024, 'Segment Information and Geographic Data', net sales by country.
      Note: 'China' includes Hong Kong and Taiwan.)

  2. cash_flow.csv has empty cash_beginning / cash_ending for FY2024.
        beginning 30,737 (= FY2023 ending)   ending 29,943
        and 30,737 + (-794 net change) = 29,943
     (Apple renamed that line to 'Cash, cash equivalents, and restricted cash and cash
      equivalents, ...' in the FY2024 report, so the builder did not recognise it.)

Safety:
  - Your original files are copied to final_data/_backup_before_patch/ first.
  - Running it twice does nothing the second time.
  - It refuses to change anything if the numbers on disk are not what it expects.

How to run (from inside your ai_assistant folder):
    python 05a_patch_final_data.py
"""
import shutil
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
FINAL = ROOT / "final_data"
BACKUP = FINAL / "_backup_before_patch"

COUNTRY_ROWS = [
    {"fiscal_year": 2024, "period_end": "2024-09-28", "country_group": "U.S.", "net_sales_usd_m": 142196, "source_report": 2024},
    {"fiscal_year": 2024, "period_end": "2024-09-28", "country_group": "China", "net_sales_usd_m": 66952, "source_report": 2024},
    {"fiscal_year": 2024, "period_end": "2024-09-28", "country_group": "Other countries", "net_sales_usd_m": 181887, "source_report": 2024},
]
CASH_2024 = {"cash_beginning": 30737, "cash_ending": 29943}


def backup(name: str) -> None:
    BACKUP.mkdir(exist_ok=True)
    dst = BACKUP / name
    if not dst.exists():  # never overwrite the first (original) backup
        shutil.copy2(FINAL / name, dst)


def main() -> int:
    country_f, cash_f = FINAL / "sales_by_country.csv", FINAL / "cash_flow.csv"
    for f in (country_f, cash_f):
        if not f.exists():
            print(f"ERROR: {f} not found. Run this from your ai_assistant folder.")
            return 1

    # ------------------------------------------------ 1. country sales FY2024
    cty = pd.read_csv(country_f)
    if (cty.fiscal_year == 2024).any():
        print("[skip] sales_by_country.csv already has FY2024 rows.")
    else:
        total = pd.read_csv(FINAL / "income_statement.csv").set_index("fiscal_year").net_sales[2024]
        if sum(r["net_sales_usd_m"] for r in COUNTRY_ROWS) != total:
            print("ERROR: country rows do not add up to FY2024 net sales in your file. Nothing changed.")
            return 1
        backup("sales_by_country.csv")
        out = pd.concat([cty, pd.DataFrame(COUNTRY_ROWS)], ignore_index=True)
        out.to_csv(country_f, index=False)
        print(f"[done] sales_by_country.csv: added 3 rows for FY2024 (total {total:,}).")

    # ------------------------------------------------ 2. FY2024 cash balances
    cf = pd.read_csv(cash_f)
    row = cf.fiscal_year == 2024
    if not row.any():
        print("[skip] no FY2024 row in cash_flow.csv.")
    elif cf.loc[row, ["cash_beginning", "cash_ending"]].notna().all(axis=None):
        print("[skip] cash_flow.csv FY2024 cash balances already filled.")
    else:
        prev_end = cf.loc[cf.fiscal_year == 2023, "cash_ending"].iloc[0]
        change = cf.loc[row, "net_change_in_cash"].iloc[0]
        if prev_end != CASH_2024["cash_beginning"] or prev_end + change != CASH_2024["cash_ending"]:
            print("ERROR: FY2023 ending cash + FY2024 change does not match the expected values. Nothing changed.")
            return 1
        backup("cash_flow.csv")
        for col, val in CASH_2024.items():
            cf.loc[row, col] = val
        for col in CASH_2024:
            cf[col] = cf[col].astype("Int64")
        cf.to_csv(cash_f, index=False)
        print("[done] cash_flow.csv: FY2024 cash_beginning = 30,737 and cash_ending = 29,943.")

    print(f"\nOriginals (if changed) are in: {BACKUP}")
    print("Next: python 05_load_final_data.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
