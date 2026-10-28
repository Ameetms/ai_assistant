"""
STEP 6 - Text-to-SQL: ask a question in English, get an exact answer from the database.

Flow (Chapter 8 of the guide):
    your question
      -> prompt = rules + table/column descriptions (from YOUR data_dictionary) + examples
      -> the LLM (Groq) writes ONE SQL query
      -> SAFETY CHECK (single SELECT only) and the database is opened READ-ONLY
      -> query runs on data/apple_final.db
      -> if it fails, the error goes back to the LLM to fix (max 2 retries)
      -> the LLM explains the result using ONLY the rows returned

Numbers always come from SQL. The LLM writes the query and the explanation, nothing else.
Every run is appended to outputs/query_log.jsonl (question, SQL, rows, tokens, seconds).

How to run (from inside your ai_assistant folder, after Step 5):
    python 06_text_to_sql.py                         -> interactive; type 'quit' to stop
    python 06_text_to_sql.py "What was net income in 2023?"
    python 06_text_to_sql.py --demo                  -> runs 8 example questions
    python 06_text_to_sql.py --schema                -> prints exactly what the LLM is told
Add --no-explain to skip the explanation step (saves tokens).
"""
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "apple_final.db"
LOG_PATH = ROOT / "outputs" / "query_log.jsonl"
MODEL = "openai/gpt-oss-120b"
MAX_RETRIES = 2
ROW_LIMIT = 200

DATA_TABLES = [
    "income_statement", "balance_sheet", "cash_flow",
    "sales_by_category", "sales_by_region", "sales_by_country",
]
SKIP_COLUMNS = {"fiscal_year", "period_end", "source_report"}  # explained once in RULES

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)

# --------------------------------------------------------------------------
# RULES and EXAMPLES are where YOUR analyst knowledge improves accuracy.
# Edit them freely. After every change, run --demo and look at the SQL.
# --------------------------------------------------------------------------
RULES = """You write SQLite queries for a database of Apple Inc. annual financial data.
Return ONLY one SQL query: no explanation, no markdown.

GENERAL
- One row per fiscal year in the wide tables (income_statement, balance_sheet, cash_flow).
  The sales_by_* tables have one row per fiscal year per category/region/country_group.
- fiscal_year is Apple's fiscal year (ends late September). FY2024 = year ended 28 Sep 2024.
  Join tables on fiscal_year.
- Money is USD millions. EPS is USD per share. Columns ending _thousands are thousands of shares.
- Only SELECT. Always ORDER BY fiscal_year when returning several years. Use the exact column names below.
- If the question needs data that does not exist (see COVERAGE), or is not about Apple's financials,
  return exactly:  NOT_AVAILABLE: <short reason>

MEANINGS (use these definitions)
- "revenue", "sales" = net_sales.  "profit", "earnings", "bottom line" = net_income.
- gross_margin is a DOLLAR amount. Gross margin % = 100.0 * gross_margin / net_sales.
  Operating margin % = 100.0 * operating_income / net_sales. Net (profit) margin % = 100.0 * net_income / net_sales.
- Growth % between years = 100.0 * (new - old) / old, rounded to 1 decimal. Use a self join on fiscal_year - 1.
- capital_expenditures, dividends_paid and share_repurchases are stored as NEGATIVE numbers (cash out).
  The amount spent = -column. Free cash flow = operating_cash_flow + capital_expenditures.
- Total debt = commercial_paper + term_debt_current + term_debt_non_current.
- "Services" revenue: services_net_sales (income_statement) or category = 'Services' (sales_by_category).
- "China" in a regional question means region = 'Greater China' in sales_by_region (includes Hong Kong and Taiwan).
  Use sales_by_country (country_group 'U.S.', 'China', 'Other countries') only when the question is about
  country splits or the U.S.
- category values: 'iPhone', 'Mac', 'iPad', 'Wearables, Home and Accessories', 'Services'.
- region values: 'Americas', 'Europe', 'Greater China', 'Japan', 'Rest of Asia Pacific'.
- Regional operating_income_usd_m is BEFORE R&D and corporate costs, so it is higher than consolidated operating income."""

EXAMPLES = [
    ("What was total net sales in 2023?",
     "SELECT fiscal_year, net_sales FROM income_statement WHERE fiscal_year = 2023;"),
    ("Show net income and net profit margin for every year.",
     "SELECT fiscal_year, net_income, ROUND(100.0 * net_income / net_sales, 1) AS net_margin_pct "
     "FROM income_statement ORDER BY fiscal_year;"),
    ("How fast did net sales grow each year?",
     "SELECT n.fiscal_year, n.net_sales, ROUND(100.0 * (n.net_sales - p.net_sales) / p.net_sales, 1) AS growth_pct "
     "FROM income_statement n JOIN income_statement p ON p.fiscal_year = n.fiscal_year - 1 ORDER BY n.fiscal_year;"),
    ("Which product category was biggest in 2024?",
     "SELECT category, net_sales_usd_m FROM sales_by_category WHERE fiscal_year = 2024 "
     "ORDER BY net_sales_usd_m DESC LIMIT 1;"),
    ("How did Greater China sales change from 2022 to 2024?",
     "SELECT fiscal_year, net_sales_usd_m FROM sales_by_region WHERE region = 'Greater China' "
     "AND fiscal_year BETWEEN 2022 AND 2024 ORDER BY fiscal_year;"),
    ("What was free cash flow each year?",
     "SELECT fiscal_year, operating_cash_flow + capital_expenditures AS free_cash_flow FROM cash_flow "
     "ORDER BY fiscal_year;"),
    ("How much did Apple spend on buybacks in 2022?",
     "SELECT fiscal_year, -share_repurchases AS buybacks_usd_m FROM cash_flow WHERE fiscal_year = 2022;"),
    ("What was total debt in 2024?",
     "SELECT fiscal_year, commercial_paper + term_debt_current + term_debt_non_current AS total_debt "
     "FROM balance_sheet WHERE fiscal_year = 2024;"),
    ("What was Apple's revenue in Q2 2025?",
     "NOT_AVAILABLE: only annual data for FY2019-FY2024 is in the database, no quarterly or FY2025 data"),
    ("Who is the CEO of Apple?",
     "NOT_AVAILABLE: the database only holds Apple's annual financial figures"),
]

DEMO_QUESTIONS = [
    "What was total net sales in 2022?",
    "Which year had the highest gross margin percentage, and what was it?",
    "How much did services revenue grow from 2021 to 2024, in percent?",
    "Which region had the largest operating income in 2023?",
    "How much did Apple spend on R&D as a percentage of sales in 2024?",
    "What share of 2024 net sales came from the U.S.?",
    "What was the quarterly revenue for Q3 2025?",   # should be refused
    "What is the capital of Peru?",                   # should be refused
]


# ------------------------------------------------------------------ database
def open_readonly() -> sqlite3.Connection:
    # mode=ro: even a bug or a malicious query can never change your data
    return sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)


def schema_text(con) -> str:
    d = pd.read_sql("SELECT table_name, column_name, description, unit FROM data_dictionary", con)
    out = []
    for t in DATA_TABLES:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        lines = [f"TABLE {t}"]
        for c in cols:
            if c in SKIP_COLUMNS:
                continue
            row = d[(d.table_name == t) & (d.column_name == c)]
            desc = row.description.iloc[0] if len(row) else ""
            lines.append(f"  {c}: {desc}")
        lines.insert(1, "  (also has columns fiscal_year, period_end)")
        out.append("\n".join(lines))
    return "\n".join(out)


def coverage_text(con) -> str:
    lines = []
    for t in DATA_TABLES:
        lo, hi = con.execute(f"SELECT MIN(fiscal_year), MAX(fiscal_year) FROM {t}").fetchone()
        lines.append(f"- {t}: FY{lo} to FY{hi}")
    for col in ["cash_beginning", "cash_ending"]:
        yrs = [r[0] for r in con.execute(f"SELECT fiscal_year FROM cash_flow WHERE {col} IS NULL")]
        if yrs:
            lines.append(f"- cash_flow.{col} is empty for FY{', FY'.join(map(str, yrs))}")
    lines.append("- There is NO quarterly data in the database.")
    return "\n".join(lines)


def build_system_prompt(con) -> str:
    ex = "\n\n".join(f"QUESTION: {q}\nSQL: {s}" for q, s in EXAMPLES)
    return (f"{RULES}\n\nCOVERAGE\n{coverage_text(con)}\n\nSCHEMA\n{schema_text(con)}\n\nEXAMPLES\n{ex}")


# -------------------------------------------------------------------- safety
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|truncate|grant|load_extension)\b",
    re.I,
)


def clean_sql(text: str) -> str:
    text = text.strip()
    return re.sub(r"^```(?:sql)?\s*|\s*```$", "", text, flags=re.I).strip()


def safety_check(sql: str):
    """Returns None if the SQL is safe, otherwise the reason it was rejected."""
    body = sql.strip().rstrip(";").strip()
    if ";" in body:
        return "more than one statement"
    if not re.match(r"^(select|with)\b", body, re.I):
        return "must start with SELECT or WITH"
    if FORBIDDEN.search(body):
        return "contains a forbidden keyword"
    return None


def run_sql(con, sql: str) -> pd.DataFrame:
    body = sql.strip().rstrip(";").strip()
    return pd.read_sql(f"SELECT * FROM ({body}) LIMIT {ROW_LIMIT}", con)


# ----------------------------------------------------------------------- LLM
_client = None


def call_llm(messages, max_tokens=900):
    """Returns (text, total_tokens). Tests replace this function."""
    global _client
    from dotenv import load_dotenv
    from groq import Groq

    if _client is None:
        load_dotenv(ROOT / ".env")
        key = os.getenv("GROQ_API_KEY", "").strip()
        if not key:
            raise RuntimeError("GROQ_API_KEY missing. Check your .env file (Step 3).")
        _client = Groq(api_key=key)

    kwargs = dict(model=MODEL, messages=messages, temperature=0, max_tokens=max_tokens)
    use_effort = True
    for attempt in range(5):
        try:
            # low reasoning effort = faster and far fewer tokens; plenty for SQL
            extra = {"extra_body": {"reasoning_effort": "low"}} if use_effort else {}
            r = _client.chat.completions.create(**kwargs, **extra)
            return (r.choices[0].message.content or "").strip(), (r.usage.total_tokens if r.usage else 0)
        except Exception as e:
            msg = str(e).lower()
            if use_effort and "reasoning" in msg:
                use_effort = False  # this model/SDK does not accept the option; retry without it
                continue
            if "429" in msg or "rate limit" in msg or "rate_limit" in msg:
                wait = 20 * (attempt + 1)  # free tier allows ~8K tokens per minute
                print(f"  (rate limit reached, waiting {wait}s then retrying...)")
                time.sleep(wait)
                continue
            raise
    raise RuntimeError("Still rate-limited after 5 tries. Wait a minute or check your Groq usage limits.")


# ------------------------------------------------------------------ pipeline
def ask(question: str, con, system_prompt: str, explain: bool = True) -> dict:
    t0, tokens = time.time(), 0
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question}]
    res = {"question": question, "sql": None, "attempts": 0, "rows": None, "answer": None, "error": None}

    for attempt in range(MAX_RETRIES + 1):
        res["attempts"] = attempt + 1
        text, used = call_llm(messages)
        tokens += used
        sql = clean_sql(text)
        res["sql"] = sql

        if sql.upper().startswith("NOT_AVAILABLE"):
            res["answer"] = "I can't answer that from the available data. " + sql.split(":", 1)[-1].strip()
            res["rows"], res["error"] = pd.DataFrame(), None
            break

        error = safety_check(sql)
        if error:
            error = f"Rejected by safety check: {error}"
        else:
            try:
                df = run_sql(con, sql)
                if df.empty:
                    error = "The query ran but returned no rows. Check the column names, values and years."
                else:
                    res["rows"] = df
            except Exception as e:
                error = f"SQLite error: {e}"

        res["error"] = error
        if error is None:
            break
        messages += [
            {"role": "assistant", "content": sql},
            {"role": "user", "content": f"That failed. {error}\nReturn a corrected single SQL query only."},
        ]
    else:
        res["answer"] = f"I could not produce a working query after {MAX_RETRIES + 1} tries. Last problem: {res['error']}"

    if res["error"] is None and res["rows"] is not None and not res["rows"].empty and explain:
        text, used = call_llm(
            [
                {"role": "system", "content": "You are a careful financial analyst. Answer in 1-3 plain sentences (no markdown) using ONLY "
                 "the numbers in the result table. Always state the key number(s) with units and the fiscal year(s). "
                 "Money is USD millions unless a column says otherwise. Do not add any number that is not in the table."},
                {"role": "user", "content": f"Question: {question}\n\nResult table (CSV):\n{res['rows'].to_csv(index=False)}"},
            ],
            max_tokens=400,
        )
        tokens += used
        res["answer"] = text
    elif res["answer"] is None and res["rows"] is not None and not res["rows"].empty:
        res["answer"] = "(explanation skipped; see the result table)"

    res["tokens"], res["seconds"] = tokens, round(time.time() - t0, 2)

    LOG_PATH.parent.mkdir(exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        rec = {k: (v.to_dict("records") if isinstance(v, pd.DataFrame) else v) for k, v in res.items()}
        f.write(json.dumps(rec, default=str) + "\n")
    return res


def show(res: dict) -> None:
    print(f"\nQ: {res['question']}")
    print(f"SQL ({res['attempts']} attempt{'s' if res['attempts'] != 1 else ''}):\n  {res['sql']}")
    if res["rows"] is not None and not res["rows"].empty:
        print("\nRESULT")
        print(res["rows"].to_string(index=False))
    print(f"\nANSWER: {res['answer']}")
    print(f"[{res['tokens']} tokens, {res['seconds']}s]")


def main(argv) -> int:
    if not DB_PATH.exists():
        print("ERROR: data/apple_final.db not found. Run 05_load_final_data.py first.")
        return 1
    con = open_readonly()
    system_prompt = build_system_prompt(con)

    if "--schema" in argv:
        print(system_prompt)
        print(f"\n[about {len(system_prompt) // 4} tokens]")
        return 0

    explain = "--no-explain" not in argv
    args = [a for a in argv if not a.startswith("--")]
    try:
        if "--demo" in argv:
            for q in DEMO_QUESTIONS:
                show(ask(q, con, system_prompt, explain))
                time.sleep(15)  # free tier: ~8K tokens per minute
        elif args:
            show(ask(" ".join(args), con, system_prompt, explain))
        else:
            print("Ask about Apple's annual financials. Type 'quit' to stop.")
            while True:
                q = input("\nYour question> ").strip()
                if q.lower() in {"quit", "exit", "q"}:
                    break
                if q:
                    show(ask(q, con, system_prompt, explain))
    except Exception as e:
        print(f"\n[FAIL] {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
