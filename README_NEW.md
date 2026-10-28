# AI Business Analyst Assistant (Apple Inc. financials)

A learning/portfolio project: ask business questions in plain English and get answers from
**exact numbers (SQL database)** and, in later steps, **management commentary (document search)**.

> Principle: *the LLM speaks, but evidence decides.* Numbers always come from SQL, never from the model's memory.

## Status

| Step | Script | What it does | Status |
|---|---|---|---|
| 0 | `00_check_setup.py` | Checks Python, packages and folders | Done |
| 1 | `01_inspect_data.py` | Inventory of every data file | Done |
| 2 | `02_build_database.py` | CSV tables to one SQLite database | Done |
| 3 | `03_first_llm_call.py` | First call to the LLM (Groq) | Done |
| 4 | `04_build_metrics.py` | Clean metrics layer + data dictionary | Done |
| 5 | `05_text_to_sql.py` | English question to SQL to answer | Built, being tested |
| 6+ | (planned) | Parse FY2025 and quarterly numbers, document search (RAG), agent, evaluation, Streamlit app | Planned |

## Data

All data is Apple Inc. (fiscal year ends in late September), from public SEC filings.

| Folder | Contents |
|---|---|
| `10-K_annual_reports/` | 5 annual reports as text, FY2021 to FY2025. File name = filing date, e.g. `10-K_2024-11-01.txt` |
| `10-Q_quarterly_reports/` | 18 quarterly reports as text, Dec 2020 quarter to Jun 2026 quarter. Q4 is not filed separately; it is inside the 10-K |
| `tables/Financial_Report_YYYY_CSV/` | The financial tables of each 10-K as CSV, one file per table. Only FY2021 to FY2024 exist |

Notes about the raw files:
- Each CSV has a title in the first cell with the unit (for example `$ in Millions`).
  Two header layouts exist: *period length + dates* (income statement style) and *dates only* (balance sheet style).
- The same number appears in two consecutive filings; the newest filing wins.
- Apple renamed some line items between years (e.g. "Retained earnings" became "Accumulated deficit"),
  which is why Step 4 creates standard metric names.
- A tiny file called `temp` in some CSV folders is junk and is ignored.
- Text reports start with a few lines of website menu text ("Menu", "Search Facts"...); they are not part of the filing.

## Database (`data/apple_financials.db`, created by the scripts)

Clean tables, used by the assistant:

| Table | Content |
|---|---|
| `financial_metrics` | 49 metrics x fiscal year (net sales, margins, cash flow, debt...). Columns: metric, fiscal_year, period_end, value, unit |
| `metric_dictionary` | Plain-English definition and unit of every metric |
| `product_sales` | Net sales by iPhone, Mac, iPad, Wearables/Home/Accessories, Services |
| `segment_results` | Net sales and operating income by Americas, Europe, Greater China, Japan, Rest of Asia Pacific |

Raw tables, kept for traceability: `facts` (every number from every CSV, newest filing only),
`facts_all_filings` (before removing repeats), `load_log` (what was loaded or skipped), and views
`income_statement`, `balance_sheet`, `cash_flow`, `net_sales_by_category`.

Units: money in USD millions, EPS in USD per share, share counts in thousands.
Capex, dividends and buybacks are stored as positive amounts spent.

## Known limits

- Structured numbers cover FY2019 to FY2024 only. Balance sheet items start in FY2020; `shares_outstanding` starts in FY2022.
- No FY2025 or quarterly numbers in the database yet (they exist in the text reports; parsing is planned).
- The statements of shareholders' equity CSVs use a different layout and are not loaded yet.
- Regional operating income excludes unallocated R&D and corporate costs, so regions sum to more than consolidated operating income.

## How to run

Requires Python 3.10+ (developed on 3.14).

```
python -m venv .venv
.venv\Scripts\Activate.ps1            # Windows PowerShell
pip install -r requirements.txt
pip install groq python-dotenv        # needed from Step 3 onwards

python 00_check_setup.py
python 01_inspect_data.py
python 02_build_database.py
python 04_build_metrics.py

# Steps 3 and 5 need a free Groq API key in a file named .env :
#   GROQ_API_KEY=your_key_here
python 03_first_llm_call.py
python 05_text_to_sql.py "What was net income in 2023?"
python 05_text_to_sql.py --demo
```

## Safety

- The API key lives only in `.env`, which is listed in `.gitignore`. Never commit it.
- The database is opened **read-only** by the assistant, and only a single `SELECT` is accepted.
- `data/` and `outputs/` are generated locally and are not committed.

## Outputs

- `outputs/data_inventory.txt`: report from Step 1.
- `outputs/query_log.jsonl`: every question, SQL, result, token count and time (used later for evaluation).

## Tools

Python, pandas, SQLite, Groq API (`openai/gpt-oss-120b`). Planned: LangGraph, ChromaDB, rank_bm25,
sentence-transformers, RAGAS, LangSmith, Streamlit.
