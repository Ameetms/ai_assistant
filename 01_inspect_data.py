"""
STEP 1 - Make an inventory of your data (read-only, changes nothing).

It answers: what files do I have, which period does each one cover,
which years have tables, and are there any odd/empty files?

The report is printed on screen AND saved to  outputs/data_inventory.txt
so you can send it back to me.

How to run (from inside your ai_assistant folder):
    python 01_inspect_data.py
"""
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "outputs"
OUT_DIR.mkdir(exist_ok=True)

lines = []


def say(text: str = "") -> None:
    print(text)
    lines.append(text)


def read_text(path: Path) -> str:
    # utf-8-sig removes the hidden BOM character at the start of some files
    return path.read_text(encoding="utf-8-sig", errors="replace")


# ---------------------------------------------------------------- text reports
def describe_reports(folder: str, kind: str) -> None:
    say(f"\n{kind} files in {folder}/")
    say("-" * 60)
    pattern = (
        r"For the fiscal year ended\s+([A-Za-z]+ \d{1,2}, \d{4})"
        if kind == "10-K"
        else r"For the quarterly period ended\s+([A-Za-z]+ \d{1,2}, \d{4})"
    )
    for f in sorted((ROOT / folder).glob("*.txt")):
        text = read_text(f)
        m = re.search(pattern, text)
        period = m.group(1) if m else "NOT FOUND"
        n_tabs = text.count("\t")
        say(f"{f.name:<24} {len(text)/1000:>6.0f} KB   period ended: {period:<20} tab chars: {n_tabs}")


# ------------------------------------------------------------------ csv tables
def describe_tables() -> None:
    base = ROOT / "tables"
    say("\nCSV table folders in tables/")
    say("-" * 60)
    for folder in sorted(p for p in base.iterdir() if p.is_dir()):
        files = sorted(folder.iterdir())
        csvs = [f for f in files if f.suffix.lower() == ".csv"]
        others = [f for f in files if f.suffix.lower() != ".csv"]
        empty = [f.name for f in csvs if f.stat().st_size < 10]

        # fiscal year-end date comes from the Cover Page table
        year_end = "unknown"
        cover = folder / "Cover Page.csv"
        if cover.exists():
            df = pd.read_csv(cover, encoding="utf-8-sig", header=None, dtype=str)
            hit = df[df[0].str.strip() == "Document Period End Date"]
            if not hit.empty:
                year_end = re.sub(r"\s+", " ", str(hit.iloc[0, 1]))

        say(f"{folder.name}: {len(csvs)} csv files, fiscal year ended {year_end}")
        if others:
            say(f"    non-csv files: {[f.name for f in others]}  (probably junk, safe to ignore)")
        if empty:
            say(f"    empty csv files: {empty}")

        # the four core statements
        for name in [
            "CONSOLIDATED STATEMENTS OF OPER.csv",
            "CONSOLIDATED BALANCE SHEETS.csv",
            "CONSOLIDATED STATEMENTS OF CASH.csv",
        ]:
            f = folder / name
            if f.exists():
                df = pd.read_csv(f, encoding="utf-8-sig", header=None, dtype=str)
                title = str(df.iloc[0, 0])[:95]
                say(f"    {name:<40} {df.shape[0]:>3} rows x {df.shape[1]} cols | {title}")
            else:
                say(f"    {name:<40} MISSING")


def coverage_summary() -> None:
    say("\nCoverage summary")
    say("-" * 60)
    tabs = sorted(p.name for p in (ROOT / "tables").iterdir() if p.is_dir())
    say(f"Annual CSV table sets : {', '.join(tabs)}")
    ks = sorted(f.name for f in (ROOT / "10-K_annual_reports").glob("*.txt"))
    say(f"10-K text reports     : {len(ks)}  ({ks[0][5:15]} to {ks[-1][5:15]})")
    qs = sorted(f.name for f in (ROOT / "10-Q_quarterly_reports").glob("*.txt"))
    say(f"10-Q text reports     : {len(qs)}  ({qs[0][5:15]} to {qs[-1][5:15]})")
    say("\nNOTE: 10-K text files exist for years that have no CSV table folder,")
    say("and no 10-Q has CSV tables. Quarterly numbers exist only inside the 10-Q text.")


def main() -> int:
    say("DATA INVENTORY")
    say("=" * 60)
    for needed in ["10-K_annual_reports", "10-Q_quarterly_reports", "tables"]:
        if not (ROOT / needed).is_dir():
            say(f"ERROR: folder '{needed}' not found. Put this script inside your ai_assistant folder.")
            return 1
    describe_reports("10-K_annual_reports", "10-K")
    describe_reports("10-Q_quarterly_reports", "10-Q")
    describe_tables()
    coverage_summary()
    (OUT_DIR / "data_inventory.txt").write_text("\n".join(lines), encoding="utf-8")
    say(f"\nSaved report to {OUT_DIR / 'data_inventory.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
