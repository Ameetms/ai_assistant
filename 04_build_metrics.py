"""
STEP 4 - Build a CLEAN metrics layer on top of the database.

Why this step exists:
  Apple renamed some line items between years (for example
  'Retained earnings' became 'Accumulated deficit'), and the cash-flow
  table groups lines inconsistently. If we give the LLM those raw names it
  will write wrong SQL. So we create small, clean tables with ONE standard
  name per metric, plus a plain-English dictionary of what each means.
  (This is the "data dictionary + metric definitions" idea from the guide.)

New tables added to data/apple_financials.db:
  financial_metrics  - metric, fiscal_year, period_end, value, unit
  metric_dictionary  - metric, statement, unit, description
  product_sales      - net sales by product category (iPhone, Mac, ...)
  segment_results    - net sales and operating income by region

It only adds tables. It never changes the raw data tables.
Run it as many times as you like.

How to run (from inside your ai_assistant folder, after Step 2):
    python 04_build_metrics.py
"""
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "apple_financials.db"

RSQ = "’"  # the curly apostrophe that appears in 'shareholders’ equity'

USD = "USD millions"
PS = "USD per share"
SH = "thousands of shares"

# (metric, statement view, section or None, regex for line item, unit, description, flip_sign)
# section None = ignore section (it is unreliable in some tables).
SPEC = [
    # ---------------- income statement (period = fiscal year) ----------------
    ("net_sales", "income_statement", "", r"^Net sales$", USD, "Total net sales (revenue) for the fiscal year", False),
    ("products_net_sales", "income_statement", "Products", r"^Net sales$", USD, "Net sales of products (iPhone, Mac, iPad, Wearables)", False),
    ("services_net_sales", "income_statement", "Services", r"^Net sales$", USD, "Net sales of services", False),
    ("cost_of_sales", "income_statement", "", r"^Cost of sales$", USD, "Total cost of sales", False),
    ("products_cost_of_sales", "income_statement", "Products", r"^Cost of sales$", USD, "Cost of sales for products", False),
    ("services_cost_of_sales", "income_statement", "Services", r"^Cost of sales$", USD, "Cost of sales for services", False),
    ("gross_margin", "income_statement", "", r"^Gross margin$", USD, "Net sales minus cost of sales, in dollars (NOT a percentage)", False),
    ("research_and_development", "income_statement", "Operating expenses:", r"^Research and development$", USD, "R&D expense", False),
    ("selling_general_admin", "income_statement", "Operating expenses:", r"^Selling, general and administrative$", USD, "SG&A expense", False),
    ("total_operating_expenses", "income_statement", "Operating expenses:", r"^Total operating expenses$", USD, "R&D plus SG&A", False),
    ("operating_income", "income_statement", "", r"^Operating income$", USD, "Gross margin minus operating expenses", False),
    ("other_income_expense_net", "income_statement", "", r"^Other income/\(expense\), net$", USD, "Other income or expense, net", False),
    ("income_before_taxes", "income_statement", "", r"^Income before provision for income taxes$", USD, "Pre-tax income", False),
    ("income_tax_provision", "income_statement", "", r"^Provision for income taxes$", USD, "Income tax expense", False),
    ("net_income", "income_statement", "", r"^Net income$", USD, "Profit after tax", False),
    ("eps_basic", "income_statement", None, r"^Basic \(in dollars per share\)$", PS, "Basic earnings per share", False),
    ("eps_diluted", "income_statement", None, r"^Diluted \(in dollars per share\)$", PS, "Diluted earnings per share", False),
    ("shares_used_basic", "income_statement", None, r"^Basic \(in shares\)$", SH, "Weighted average shares used for basic EPS", False),
    ("shares_used_diluted", "income_statement", None, r"^Diluted \(in shares\)$", SH, "Weighted average shares used for diluted EPS", False),
    # ---------------- balance sheet (period = fiscal year end date) ----------
    ("cash_and_equivalents", "balance_sheet", "Current assets:", r"^Cash and cash equivalents$", USD, "Cash and cash equivalents at year end", False),
    ("marketable_securities_current", "balance_sheet", "Current assets:", r"^Marketable securities$", USD, "Current marketable securities", False),
    ("marketable_securities_noncurrent", "balance_sheet", "Non-current assets:", r"^Marketable securities$", USD, "Non-current marketable securities", False),
    ("accounts_receivable", "balance_sheet", "Current assets:", r"^Accounts receivable, net$", USD, "Accounts receivable, net", False),
    ("inventories", "balance_sheet", "Current assets:", r"^Inventories$", USD, "Inventories", False),
    ("total_current_assets", "balance_sheet", "Current assets:", r"^Total current assets$", USD, "Total current assets", False),
    ("property_plant_equipment_net", "balance_sheet", "Non-current assets:", r"^Property, plant and equipment, net$", USD, "PP&E, net", False),
    ("total_assets", "balance_sheet", "", r"^Total assets$", USD, "Total assets", False),
    ("accounts_payable", "balance_sheet", "Current liabilities:", r"^Accounts payable$", USD, "Accounts payable", False),
    ("deferred_revenue_current", "balance_sheet", "Current liabilities:", r"^Deferred revenue$", USD, "Current deferred revenue", False),
    ("commercial_paper", "balance_sheet", "Current liabilities:", r"^Commercial paper$", USD, "Commercial paper (short-term borrowing)", False),
    ("term_debt_current", "balance_sheet", "Current liabilities:", r"^Term debt$", USD, "Term debt due within one year", False),
    ("term_debt_noncurrent", "balance_sheet", "Non-current liabilities:", r"^Term debt$", USD, "Term debt due after one year", False),
    ("total_current_liabilities", "balance_sheet", "Current liabilities:", r"^Total current liabilities$", USD, "Total current liabilities", False),
    ("total_liabilities", "balance_sheet", "", r"^Total liabilities$", USD, "Total liabilities", False),
    ("total_shareholders_equity", "balance_sheet", None, r"^Total shareholders" + RSQ + r" equity$", USD, "Total shareholders' equity", False),
    ("shares_outstanding", "balance_sheet", None, r"^Common stock, shares outstanding", SH, "Shares outstanding at year end", False),
    # ---------------- cash flow (section is unreliable, so ignore it) --------
    ("operating_cash_flow", "cash_flow", None, r"^Cash generated by operating activities$", USD, "Cash generated by operating activities", False),
    ("investing_cash_flow", "cash_flow", None, r"^Cash (generated by/\(used in\)|used in) investing activities$", USD, "Net cash from investing (negative = cash spent)", False),
    ("financing_cash_flow", "cash_flow", None, r"^Cash (used in|generated by/\(used in\)) financing activities$", USD, "Net cash from financing (negative = cash spent)", False),
    ("depreciation_amortization", "cash_flow", None, r"^Depreciation and amortization$", USD, "Depreciation and amortization", False),
    ("share_based_compensation", "cash_flow", None, r"^Share-based compensation expense$", USD, "Share-based compensation expense", False),
    ("capital_expenditures", "cash_flow", None, r"^Payments for acquisition of property, plant and equipment$", USD, "Capital expenditure, shown as a POSITIVE amount spent", True),
    ("dividends_paid", "cash_flow", None, r"^Payments for dividends and dividend equivalents$", USD, "Dividends paid, shown as a POSITIVE amount", True),
    ("share_repurchases", "cash_flow", None, r"^Repurchases of common stock$", USD, "Buybacks of common stock, shown as a POSITIVE amount", True),
]

STATEMENT_NAME = {"income_statement": "Income statement", "balance_sheet": "Balance sheet", "cash_flow": "Cash flow"}


def extract_metrics(con) -> pd.DataFrame:
    out = []
    for metric, view, section, pattern, unit, desc, flip in SPEC:
        df = pd.read_sql(f"SELECT section, line_item, period_end, value FROM {view}", con)
        rx = re.compile(pattern)
        hit = df[df["line_item"].apply(lambda s: bool(rx.search(s)))]
        if section is not None:
            hit = hit[hit["section"] == section]
        # Two different labels for the same thing never appear in the same year,
        # but if they ever do with different values we must stop, not guess.
        grouped = hit.groupby("period_end")["value"].nunique()
        if (grouped > 1).any():
            bad = grouped[grouped > 1].index.tolist()
            raise RuntimeError(f"Metric '{metric}' matched different values for {bad}. Tell me; do not continue.")
        hit = hit.drop_duplicates("period_end")
        for _, r in hit.iterrows():
            v = -r["value"] if flip else r["value"]
            out.append(
                {
                    "metric": metric,
                    "fiscal_year": int(r["period_end"][:4]),
                    "period_end": r["period_end"],
                    "value": float(v),
                    "unit": unit,
                    "statement": STATEMENT_NAME[view],
                    "description": desc,
                }
            )
    return pd.DataFrame(out)


def add_derived(m: pd.DataFrame) -> pd.DataFrame:
    wide = m.pivot(index=["fiscal_year", "period_end"], columns="metric", values="value").reset_index()
    rows = []

    def add(metric, series, unit, statement, desc):
        for (_, r), v in zip(wide.iterrows(), series):
            if pd.notna(v):
                rows.append(
                    {"metric": metric, "fiscal_year": int(r["fiscal_year"]), "period_end": r["period_end"],
                     "value": float(v), "unit": unit, "statement": statement, "description": desc}
                )

    add("gross_margin_pct", wide["gross_margin"] / wide["net_sales"] * 100, "percent", "Derived",
        "Gross margin divided by net sales, times 100")
    add("operating_margin_pct", wide["operating_income"] / wide["net_sales"] * 100, "percent", "Derived",
        "Operating income divided by net sales, times 100")
    add("net_margin_pct", wide["net_income"] / wide["net_sales"] * 100, "percent", "Derived",
        "Net income divided by net sales, times 100")
    add("free_cash_flow", wide["operating_cash_flow"] - wide["capital_expenditures"], USD, "Derived",
        "Operating cash flow minus capital expenditures")
    add("total_debt", wide["commercial_paper"] + wide["term_debt_current"] + wide["term_debt_noncurrent"], USD, "Derived",
        "Commercial paper + current term debt + non-current term debt")
    return pd.concat([m, pd.DataFrame(rows)], ignore_index=True)


def build_segments(con):
    q = """SELECT section AS segment, line_item, period_end, value
           FROM facts
           WHERE table_title LIKE '%Information by Reportable Segment%'
             AND section <> '' AND line_item IN ('Net sales','Operating income')"""
    df = pd.read_sql(q, con).drop_duplicates(["segment", "line_item", "period_end"])
    wide = df.pivot(index=["segment", "period_end"], columns="line_item", values="value").reset_index()
    wide["fiscal_year"] = wide["period_end"].str[:4].astype(int)
    wide = wide.rename(columns={"Net sales": "net_sales", "Operating income": "operating_income"})
    return wide[["segment", "fiscal_year", "period_end", "net_sales", "operating_income"]]


def main() -> int:
    if not DB_PATH.exists():
        print("ERROR: database not found. Run 02_build_database.py first.")
        return 1
    con = sqlite3.connect(DB_PATH)

    m = add_derived(extract_metrics(con))
    fm = m[["metric", "fiscal_year", "period_end", "value", "unit"]].sort_values(["metric", "period_end"])
    md = (
        m[["metric", "statement", "unit", "description"]].drop_duplicates("metric")
        .sort_values(["statement", "metric"])
    )

    ps = pd.read_sql("SELECT category, period_end, net_sales_usd_millions AS net_sales FROM net_sales_by_category", con)
    ps["fiscal_year"] = ps["period_end"].str[:4].astype(int)
    ps = ps[["category", "fiscal_year", "period_end", "net_sales"]].sort_values(["category", "period_end"])
    seg = build_segments(con)

    fm.to_sql("financial_metrics", con, index=False, if_exists="replace")
    md.to_sql("metric_dictionary", con, index=False, if_exists="replace")
    ps.to_sql("product_sales", con, index=False, if_exists="replace")
    seg.to_sql("segment_results", con, index=False, if_exists="replace")
    con.commit()

    # ---------------------------------------------------------------- report
    print(f"financial_metrics : {len(fm)} rows, {fm['metric'].nunique()} metrics, "
          f"fiscal years {fm['fiscal_year'].min()}-{fm['fiscal_year'].max()}")
    print(f"product_sales     : {len(ps)} rows ({ps['category'].nunique()} categories)")
    print(f"segment_results   : {len(seg)} rows ({seg['segment'].nunique()} regions)")

    print("\nCoverage (years available per metric):")
    cov = fm.groupby("metric")["fiscal_year"].apply(lambda s: f"{s.min()}-{s.max()} ({s.nunique()} yrs)")
    short = cov[~cov.str.endswith("(6 yrs)")]
    print("  6 years (2019-2024):", int(cov.str.endswith("(6 yrs)").sum()), "metrics")
    print("  Fewer than 6 years:")
    for k, v in short.items():
        print(f"    {k:<36} {v}")

    # ---------------------------------------------------------------- checks
    wide = fm.pivot(index="fiscal_year", columns="metric", values="value")
    fails = 0

    def chk(name, a, b, tol=1.0):
        nonlocal fails
        d = (a - b).abs().dropna()
        ok = len(d) > 0 and (d <= tol).all()
        fails += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({len(d)} years checked)")

    print("\nChecks:")
    chk("gross margin = net sales - cost of sales", wide["gross_margin"], wide["net_sales"] - wide["cost_of_sales"])
    chk("products + services net sales = net sales", wide["products_net_sales"] + wide["services_net_sales"], wide["net_sales"])
    chk("operating income = gross margin - operating expenses",
        wide["operating_income"], wide["gross_margin"] - wide["total_operating_expenses"])
    chk("net income = pre-tax income - tax", wide["net_income"], wide["income_before_taxes"] - wide["income_tax_provision"])
    chk("total assets = liabilities + equity",
        wide["total_assets"], wide["total_liabilities"] + wide["total_shareholders_equity"])
    chk("product categories add up to net sales",
        ps.groupby("fiscal_year")["net_sales"].sum(), wide["net_sales"])
    chk("regions add up to net sales", seg.groupby("fiscal_year")["net_sales"].sum(), wide["net_sales"])
    # Regional operating income is BEFORE unallocated R&D and corporate costs,
    # so the regions add up to MORE than consolidated operating income. That is normal.
    reg = seg.groupby("fiscal_year")["operating_income"].sum()
    ok = bool(((reg - wide["operating_income"]).dropna() > 0).all())
    fails += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] regional operating income is higher than consolidated "
          f"(expected: regions exclude R&D and corporate costs)")

    print("\nSnapshot (USD millions unless stated):")
    cols = ["net_sales", "gross_margin", "gross_margin_pct", "operating_income", "net_income",
            "operating_cash_flow", "capital_expenditures", "free_cash_flow", "total_debt"]
    print(wide[cols].round(1).to_string())

    con.close()
    if fails:
        print(f"\n{fails} check(s) FAILED. Send me this whole output and do not continue yet.")
        return 1
    print("\nAll checks passed. Next: send me this output, and I will give you Step 5 (text-to-SQL).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
