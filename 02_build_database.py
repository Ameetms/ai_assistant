"""
STEP 2 - Turn all the CSV tables into ONE SQLite database.

Why? Your CSV files are 280+ small tables with different layouts. A database
lets the assistant (and you) ask questions with SQL.

What it does:
  1. Reads every CSV in tables/Financial_Report_YYYY_CSV/
  2. Converts each one into the same simple "long" shape, one row per number:
        fiscal_year | table_title | section | line_item | period_end | value ...
  3. Saves everything in  data/apple_financials.db
  4. Creates easy views: income_statement, balance_sheet, cash_flow, net_sales_by_category
  5. Runs a few sanity checks (does assets = liabilities + equity? etc.)

It only READS your CSVs. It never edits them. You can run it again any time;
it rebuilds the database from scratch.

How to run (from inside your ai_assistant folder):
    python 02_build_database.py
"""
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
TABLES_DIR = ROOT / "tables"
DB_PATH = ROOT / "data" / "apple_financials.db"

DATE_RE = re.compile(r"([A-Z][a-z]{2})\.?\s+(\d{1,2}),\s*(\d{4})")


# ------------------------------------------------------------------ helpers
def parse_date(text: str):
    """'Sep. 28,  2024' -> '2024-09-28'. Returns None if it is not a date."""
    m = DATE_RE.search(re.sub(r"\s+", " ", str(text)))
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_number(text: str):
    """'391035' -> 391035.0 ; '-565' -> -565.0 ; '$ 6.11' -> 6.11 ; text -> None"""
    t = str(text).strip().replace("$", "").replace(",", "").strip()
    if t.startswith("(") and t.endswith(")"):
        t = "-" + t[1:-1]
    try:
        return float(t)
    except ValueError:
        return None


def split_title(raw_title: str):
    """
    'CONSOLIDATED BALANCE SHEETS - USD ($) $ in Millions'
        -> ('CONSOLIDATED BALANCE SHEETS', 'USD ($) $ in Millions')
    """
    raw_title = re.sub(r"\s+", " ", raw_title.strip())
    parts = raw_title.rsplit(" - USD", 1)
    if len(parts) == 2:
        return parts[0].strip(), "USD" + parts[1]
    return raw_title, ""


def is_blank(x) -> bool:
    return str(x).strip() == ""


# ------------------------------------------------------------- one CSV file
def parse_csv(path: Path, fiscal_year: int):
    """
    Returns (rows, note). rows = list of dicts. note = None if OK, else why skipped.
    Two header layouts exist in your files:
      A) row 0 = period length ('12 Months Ended'), row 1 = dates
      B) row 0 = dates only (balance-sheet style, a single point in time)
    """
    df = pd.read_csv(path, encoding="utf-8-sig", header=None, dtype=str, keep_default_na=False)
    if df.shape[0] < 2 or df.shape[1] < 2:
        return [], "too small / empty"

    title, unit_note = split_title(str(df.iloc[0, 0]))
    row0 = [str(c).strip() for c in df.iloc[0, 1:]]
    row1 = [str(c).strip() for c in df.iloc[1, 1:]]

    if any("Months Ended" in c for c in row0) and any(parse_date(c) for c in row1):
        layout, first_data_row = "A", 2
        period_type, types = "", []
        for c in row0:  # blanks mean "same as the cell to the left"
            if c:
                period_type = c
            types.append(period_type)
        dates = [parse_date(c) for c in row1]
    elif any(parse_date(c) for c in row0):
        layout, first_data_row = "B", 1
        types = ["Point in time"] * len(row0)
        dates = [parse_date(c) for c in row0]
    else:
        return [], "layout not supported yet (no date header)"

    rows, section, order = [], "", 0
    for i in range(first_data_row, df.shape[0]):
        label = str(df.iloc[i, 0]).strip()
        cells = [str(c) for c in df.iloc[i, 1:]]
        if not label:
            continue
        if all(is_blank(c) for c in cells):
            # A line with no numbers is a heading (e.g. 'Products', 'Current assets:').
            # Lines ending in ']' are technical XBRL labels; ignore them.
            if not label.endswith("]"):
                section = label
            continue
        for c, ptype, pdate in zip(cells, types, dates):
            if is_blank(c) or pdate is None:
                continue
            num = parse_number(c)
            order += 1
            rows.append(
                {
                    "fiscal_year": fiscal_year,
                    "source_file": path.name,
                    "table_title": title,
                    "unit_note": unit_note,
                    "section": section,
                    "line_item": label,
                    "period_type": ptype,
                    "period_end": pdate,
                    "value": num,
                    "value_text": None if num is not None else c.strip(),
                    "row_order": order,
                }
            )
        # After a 'Total ...' line the group is finished, so later lines
        # (e.g. 'Operating income', 'Net income') are top-level again.
        if label.lower().startswith("total"):
            section = ""
    return rows, None


# ---------------------------------------------------------------- whole job
VIEWS = {
    "income_statement": "CONSOLIDATED STATEMENTS OF OPERATIONS",
    "balance_sheet": "CONSOLIDATED BALANCE SHEETS",
    "cash_flow": "CONSOLIDATED STATEMENTS OF CASH FLOWS",
}


def build_database() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    all_rows, log = [], []
    for folder in sorted(p for p in TABLES_DIR.iterdir() if p.is_dir()):
        m = re.search(r"(\d{4})", folder.name)
        if not m:
            continue
        fy = int(m.group(1))
        for f in sorted(folder.glob("*.csv")):
            try:
                rows, note = parse_csv(f, fy)
            except Exception as e:  # keep going, but record what failed
                rows, note = [], f"error: {e}"
            all_rows.extend(rows)
            log.append({"fiscal_year": fy, "file": f.name, "rows_loaded": len(rows), "note": note or "ok"})

    facts = pd.DataFrame(all_rows)
    # Some detail tables repeat the same label inside one file (e.g. several lease
    # maturities). Number them so we never throw away a genuine, different number.
    facts["occurrence"] = (
        facts.groupby(["fiscal_year", "source_file", "section", "line_item", "period_type", "period_end"]).cumcount() + 1
    )
    log_df = pd.DataFrame(log)

    con = sqlite3.connect(DB_PATH)
    facts.to_sql("facts_all_filings", con, index=False)
    log_df.to_sql("load_log", con, index=False)

    # Same number can appear in two filings (FY2024 10-K repeats FY2023).
    # Keep ONE copy per number: the one from the most recent filing.
    con.executescript(
        """
        CREATE TABLE facts AS
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY table_title, section, line_item, period_type, period_end, occurrence
                ORDER BY fiscal_year DESC, row_order
            ) AS rn
            FROM facts_all_filings
        ) WHERE rn = 1;
        ALTER TABLE facts DROP COLUMN rn;
        CREATE INDEX idx_facts_item ON facts(line_item, period_end);
        CREATE INDEX idx_facts_title ON facts(table_title);
        """
    )
    for view, title in VIEWS.items():
        con.execute(
            f"""CREATE VIEW {view} AS
                SELECT section, line_item, period_type, period_end, value, unit_note
                FROM facts WHERE table_title = '{title}' AND value IS NOT NULL"""
        )
    # Apple renamed this table between filings ('Revenue Recognition - ...' became
    # 'Revenue - ...'), so the same number can appear twice under two titles.
    # Keep the copy from the most recent filing.
    con.execute(
        """CREATE VIEW net_sales_by_category AS
           SELECT category, period_end, net_sales_usd_millions FROM (
               SELECT section AS category, period_end, value AS net_sales_usd_millions,
                      ROW_NUMBER() OVER (PARTITION BY section, period_end
                                         ORDER BY fiscal_year DESC, row_order) AS rn
               FROM facts
               WHERE table_title LIKE '%Net Sales Disaggregated by Significant Products and Services%'
                 AND line_item = 'Net sales' AND section <> ''
           ) WHERE rn = 1"""
    )
    con.commit()
    return con


# ------------------------------------------------------------ sanity checks
def check(con, name: str, sql: str, expected) -> bool:
    got = con.execute(sql).fetchone()
    got = got[0] if got else None
    ok = got is not None and abs(got - expected) < 0.5
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: got {got}, expected {expected}")
    return ok


def run_checks(con) -> int:
    print("\nSanity checks")
    print("-" * 60)
    fails = 0
    q = "SELECT value FROM income_statement WHERE line_item='{}' AND section='{}' AND period_end='{}'"
    # numbers I can see in the FY2024 10-K text
    fails += not check(con, "FY2024 net sales (USD millions)", q.format("Net sales", "", "2024-09-28"), 391035)
    fails += not check(con, "FY2024 net income", q.format("Net income", "", "2024-09-28"), 93736)
    fails += not check(con, "FY2022 net sales", q.format("Net sales", "", "2022-09-24"), 394328)

    # internal consistency, every year that has a balance sheet
    for (d,) in con.execute("SELECT DISTINCT period_end FROM balance_sheet ORDER BY 1").fetchall():
        a = con.execute(
            "SELECT value FROM balance_sheet WHERE line_item='Total assets' AND period_end=?", (d,)
        ).fetchone()
        b = con.execute(
            "SELECT value FROM balance_sheet WHERE line_item='Total liabilities and shareholders’ equity' AND period_end=?",
            (d,),
        ).fetchone()
        if a and b:
            ok = abs(a[0] - b[0]) < 0.5
            print(f"  [{'PASS' if ok else 'FAIL'}] {d}: total assets {a[0]:,.0f} = liabilities + equity {b[0]:,.0f}")
            fails += not ok

    # the four product categories + services should add up to total net sales
    for (d,) in con.execute("SELECT DISTINCT period_end FROM net_sales_by_category ORDER BY 1").fetchall():
        s = con.execute(
            "SELECT SUM(net_sales_usd_millions) FROM net_sales_by_category WHERE period_end=?", (d,)
        ).fetchone()[0]
        t = con.execute(
            "SELECT value FROM income_statement WHERE line_item='Net sales' AND section='' AND period_end=?", (d,)
        ).fetchone()
        if t:
            ok = abs(s - t[0]) < 1.5
            print(f"  [{'PASS' if ok else 'FAIL'}] {d}: category sales add up to {s:,.0f} vs total {t[0]:,.0f}")
            fails += not ok
    return fails


def main() -> int:
    if not TABLES_DIR.is_dir():
        print("ERROR: 'tables' folder not found. Put this script inside your ai_assistant folder.")
        return 1
    print("Building database (takes a few seconds)...")
    con = build_database()

    n_all = con.execute("SELECT COUNT(*) FROM facts_all_filings").fetchone()[0]
    n = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
    n_tab = con.execute("SELECT COUNT(DISTINCT table_title) FROM facts").fetchone()[0]
    print(f"\nLoaded {n_all:,} numbers from all filings -> {n:,} after removing repeats")
    print(f"Distinct tables: {n_tab}")

    skipped = con.execute("SELECT fiscal_year, file, note FROM load_log WHERE note <> 'ok'").fetchall()
    print(f"\nCSV files skipped: {len(skipped)}")
    for fy, f, note in skipped:
        print(f"  FY{fy}  {f}  ->  {note}")

    rng = con.execute("SELECT MIN(period_end), MAX(period_end) FROM income_statement").fetchone()
    print(f"\nIncome statement covers period ends: {rng[0]} to {rng[1]}")

    fails = run_checks(con)
    con.close()
    print(f"\nDatabase saved to: {DB_PATH}")
    if fails:
        print(f"\n{fails} check(s) FAILED. Send me this whole output and do not continue yet.")
        return 1
    print("\nAll checks passed. Next: send me the output above (copy and paste), and I will give you Step 3.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
