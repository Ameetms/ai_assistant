"""
STEP 5 - Text-to-SQL: ask a question in English, get an exact answer from the database.

How it works (the flow from the guide, Chapter 8):
    your question
      -> we add the schema, the metric dictionary and example queries to a prompt
      -> the LLM (Groq) writes a SQL query
      -> SAFETY CHECK: only one SELECT is allowed, database opened read-only
      -> the query runs on data/apple_financials.db
      -> if it fails, the error goes back to the LLM to fix (max 2 retries)
      -> the LLM explains the result using ONLY the returned rows

The numbers always come from SQL. The LLM only writes the query and the explanation.
Every run is saved to outputs/query_log.jsonl (question, SQL, rows, tokens, time).

How to run (from inside your ai_assistant folder, after Steps 2-4):
    python 05_text_to_sql.py                       -> interactive chat, type 'quit' to stop
    python 05_text_to_sql.py "What was net income in 2023?"
    python 05_text_to_sql.py --demo                -> runs 6 example questions
Add --no-explain to skip the explanation step and save tokens.
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
DB_PATH = ROOT / "data" / "apple_financials.db"
LOG_PATH = ROOT / "outputs" / "query_log.jsonl"
MODEL = "openai/gpt-oss-120b"
MAX_RETRIES = 2
ROW_LIMIT = 200

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 30)

# --------------------------------------------------------------------------
# What the LLM is told about your data. Edit the RULES and EXAMPLES freely:
# this is where YOUR analyst knowledge improves accuracy.
# --------------------------------------------------------------------------
RULES = """You write SQLite queries for a database of Apple Inc. annual financial data.
Return ONLY one SQL query, no explanation, no markdown.

TABLES
financial_metrics(metric, fiscal_year, period_end, value, unit)
    One row per metric per fiscal year. Use metric names from the METRIC LIST only.
metric_dictionary(metric, statement, unit, description)
product_sales(category, fiscal_year, period_end, net_sales)
    category is one of: iPhone, Mac, iPad, Wearables, Home and Accessories, Services
    (exact text: 'iPhone','Mac','iPad','Wearables, Home and Accessories','Services')
segment_results(segment, fiscal_year, period_end, net_sales, operating_income)
    segment is one of: Americas, Europe, Greater China, Japan, Rest of Asia Pacific

RULES
- Apple's fiscal year ends in late September. fiscal_year 2024 = year ended 28 Sep 2024.
- Money values are USD millions. EPS is USD per share. Share counts are in thousands.
- Data exists for fiscal years 2019-2024 only (balance sheet items 2020-2024; shares_outstanding 2022-2024).
  There is NO quarterly data and NO fiscal 2025 data in this database.
  If the question needs data that does not exist, return exactly:  NOT_AVAILABLE: <short reason>
  If the question is not about Apple's financials, return exactly:  NOT_AVAILABLE: out of scope
- gross_margin is a dollar amount. For a percentage use the metric gross_margin_pct.
- For growth or change between years use a self join on fiscal_year (or LAG), and compute
  percentages as 100.0 * (new - old) / old. Round to 1 decimal.
- Always filter metric = '...' ; never use SELECT * on financial_metrics.
- Only SELECT. Always ORDER BY fiscal_year when returning several years."""

EXAMPLES = [
    ("What was total net sales in 2023?",
     "SELECT fiscal_year, value AS net_sales_usd_m FROM financial_metrics "
     "WHERE metric = 'net_sales' AND fiscal_year = 2023;"),
    ("Show net income and net margin for every year.",
     "SELECT a.fiscal_year, a.value AS net_income_usd_m, ROUND(b.value, 1) AS net_margin_pct "
     "FROM financial_metrics a JOIN financial_metrics b ON a.fiscal_year = b.fiscal_year "
     "WHERE a.metric = 'net_income' AND b.metric = 'net_margin_pct' ORDER BY a.fiscal_year;"),
    ("How fast did net sales grow each year?",
     "SELECT n.fiscal_year, n.value AS net_sales_usd_m, "
     "ROUND(100.0 * (n.value - p.value) / p.value, 1) AS growth_pct "
     "FROM financial_metrics n JOIN financial_metrics p "
     "ON p.fiscal_year = n.fiscal_year - 1 AND p.metric = n.metric "
     "WHERE n.metric = 'net_sales' ORDER BY n.fiscal_year;"),
    ("Which product category was biggest in 2024?",
     "SELECT category, net_sales AS net_sales_usd_m FROM product_sales "
     "WHERE fiscal_year = 2024 ORDER BY net_sales DESC LIMIT 1;"),
    ("How did Greater China sales change from 2022 to 2024?",
     "SELECT fiscal_year, net_sales AS net_sales_usd_m FROM segment_results "
     "WHERE segment = 'Greater China' AND fiscal_year IN (2022, 2023, 2024) ORDER BY fiscal_year;"),
    ("What was free cash flow in 2024?",
     "SELECT fiscal_year, value AS free_cash_flow_usd_m FROM financial_metrics "
     "WHERE metric = 'free_cash_flow' AND fiscal_year = 2024;"),
]

DEMO_QUESTIONS = [
    "What was total net sales in 2022?",
    "Which year had the highest gross margin percentage?",
    "How much did services revenue grow from 2021 to 2024, in percent?",
    "Which region had the largest operating income in 2023?",
    "What was the quarterly revenue for Q3 2025?",   # should be NOT_AVAILABLE
    "What is the capital of Peru?",                   # should be NOT_AVAILABLE
]


# ------------------------------------------------------------------ database
def open_readonly() -> sqlite3.Connection:
    # mode=ro means even a bug or a bad query can never change your data
    return sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)


def metric_list(con) -> str:
    rows = con.execute("SELECT metric, unit, description FROM metric_dictionary ORDER BY statement, metric").fetchall()
    return "\n".join(f"- {m} [{u}]: {d}" for m, u, d in rows)


def build_system_prompt(con) -> str:
    ex = "\n\n".join(f"QUESTION: {q}\nSQL: {s}" for q, s in EXAMPLES)
    return f"{RULES}\n\nMETRIC LIST\n{metric_list(con)}\n\nEXAMPLES\n{ex}"


# -------------------------------------------------------------------- safety
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|truncate|grant|load_extension)\b",
    re.I,
)


def clean_sql(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:sql)?\s*|\s*```$", "", text, flags=re.I).strip()
    return text


def safety_check(sql: str) -> str | None:
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


def call_llm(messages, max_tokens=700):
    """Returns (text, total_tokens). Replace this function in tests."""
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
    try:  # low reasoning effort = faster and far fewer tokens, plenty for SQL
        r = _client.chat.completions.create(**kwargs, extra_body={"reasoning_effort": "low"})
    except Exception as e:
        if "reasoning" in str(e).lower():
            r = _client.chat.completions.create(**kwargs)
        else:
            raise
    return (r.choices[0].message.content or "").strip(), (r.usage.total_tokens if r.usage else 0)


# ------------------------------------------------------------------ pipeline
def ask(question: str, con, system_prompt: str, explain: bool = True) -> dict:
    t0 = time.time()
    tokens = 0
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question}]
    result = {"question": question, "sql": None, "attempts": 0, "rows": None, "answer": None, "error": None}

    for attempt in range(MAX_RETRIES + 1):
        result["attempts"] = attempt + 1
        text, used = call_llm(messages)
        tokens += used
        sql = clean_sql(text)
        result["sql"] = sql

        if sql.upper().startswith("NOT_AVAILABLE"):
            result["answer"] = "I can't answer that from the available data. " + sql.split(":", 1)[-1].strip()
            result["rows"] = pd.DataFrame()
            break

        problem = safety_check(sql)
        if problem:
            error = f"Rejected by safety check: {problem}"
        else:
            try:
                df = run_sql(con, sql)
                if df.empty:
                    error = "The query ran but returned no rows. Check metric names and years."
                else:
                    result["rows"] = df
                    error = None
            except Exception as e:
                error = f"SQLite error: {e}"

        if error is None:
            break
        result["error"] = error
        messages += [
            {"role": "assistant", "content": sql},
            {"role": "user", "content": f"That failed. {error}\nReturn a corrected single SQL query only."},
        ]
    else:
        result["answer"] = f"I could not produce a working query after {MAX_RETRIES + 1} tries. Last problem: {result['error']}"

    if result["rows"] is not None and not result["rows"].empty:
        result["error"] = None
        if explain:
            table = result["rows"].to_csv(index=False)
            text, used = call_llm(
                [
                    {"role": "system", "content": "You are a careful financial analyst. Answer in 1-3 sentences using ONLY "
                     "the numbers in the result table. Money is USD millions unless a column says otherwise. "
                     "Do not add any number that is not in the table."},
                    {"role": "user", "content": f"Question: {question}\n\nResult table (CSV):\n{table}"},
                ],
                max_tokens=300,
            )
            tokens += used
            result["answer"] = text
    result["tokens"] = tokens
    result["seconds"] = round(time.time() - t0, 2)

    LOG_PATH.parent.mkdir(exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        rec = {k: (v.to_dict("records") if isinstance(v, pd.DataFrame) else v) for k, v in result.items()}
        f.write(json.dumps(rec, default=str) + "\n")
    return result


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
        print("ERROR: database not found. Run Steps 2 and 4 first.")
        return 1
    explain = "--no-explain" not in argv
    args = [a for a in argv if not a.startswith("--")]

    con = open_readonly()
    if con.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='financial_metrics'").fetchone()[0] == 0:
        print("ERROR: financial_metrics table missing. Run 04_build_metrics.py first.")
        return 1
    system_prompt = build_system_prompt(con)

    try:
        if "--demo" in argv:
            for q in DEMO_QUESTIONS:
                show(ask(q, con, system_prompt, explain))
                time.sleep(2)  # be gentle with the free-tier rate limit
        elif args:
            show(ask(" ".join(args), con, system_prompt, explain))
        else:
            print("Ask about Apple's financials (FY2019-FY2024). Type 'quit' to stop.")
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
