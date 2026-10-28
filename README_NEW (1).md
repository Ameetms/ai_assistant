# AI Business Analyst Assistant (Apple Inc. financials)

A learning/portfolio project: ask business questions in plain English and get answers from
**exact numbers (SQL database)** and, in later steps, **management commentary (document search)**.

> Principle: *the LLM speaks, but evidence decides.* Numbers always come from SQL, never from the model's memory.

## Status

| Step | Script | What it does | Status |
|---|---|---|---|
| 0 | `00_check_setup.py` | Checks Python, packages and folders | Done |
| 1 | `01_inspect_data.py` | Inventory of every raw data file | Done |
| 2 | `02_build_database.py` | Raw CSV tables to one SQLite database (independent cross-check of `final_data`) | Done |
| 3 | `03_first_llm_call.py` | First call to the LLM (Groq) | Done |
| 4 | `04_build_metrics.py` | Standard-named metrics from the raw tables (used only for the cross-check) | Done |
| 5a | `05a_patch_final_data.py` | Fills two gaps found in `final_data` (FY2024 country sales, FY2024 cash balances) | Done |
| 5 | `05_load_final_data.py` | Loads `final_data/` into `data/apple_final.db` and verifies it | Done |
| 6 | `06_text_to_sql.py` | English question to SQL to answer | Built, being tested |
| 7+ | (planned) | FY2025 and quarterly numbers from the text reports, document search (RAG), agent, evaluation, Streamlit app | Planned |

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

## Official structured data: `final_data/`

`final_data/` holds the curated tables the assistant queries. They were built from the raw CSV tables
and are loaded into `data/apple_final.db` by `05_load_final_data.py`.

| File | Content |
|---|---|
| `income_statement.csv` | One row per fiscal year: net sales, cost of sales, margins, expenses, net income, EPS, shares |
| `balance_sheet.csv` | One row per fiscal year (FY2020 onward): assets, liabilities, equity, debt |
| `cash_flow.csv` | One row per fiscal year: operating, investing, financing cash flow, capex, dividends, buybacks |
| `sales_by_category.csv` | Net sales by iPhone, Mac, iPad, Wearables/Home/Accessories, Services |
| `sales_by_region.csv` | Net sales and segment operating income by Americas, Europe, Greater China, Japan, Rest of Asia Pacific |
| `sales_by_country.csv` | Net sales for U.S., China (includes Hong Kong and Taiwan), Other countries |
| `data_dictionary.csv` | Meaning and unit of every column in the tables above |
| `data_checks.csv` | Accounting checks recorded when the tables were built, plus notes on restated lines |
| `all_facts_long.csv` | Every extracted number in long format, with its source report (for traceability) |
| `apple_final.duckdb`, `load_into_duckdb.sql` | DuckDB copy of the same tables (not used by the scripts here) |

Conventions: money in USD millions; EPS in USD per share; share counts in thousands;
capex, dividends and buybacks are **negative** numbers (cash out); the latest report that contains a year wins
(`source_report` column).

### Verification

- `05_load_final_data.py` recalculates the accounting identities (gross margin, assets = liabilities + equity,
  cash flow adds up, categories/regions/countries add up to net sales).
- It also compares 248 statement values with the independent build from the raw CSVs
  (`02_build_database.py` and `04_build_metrics.py`): 0 differences.
- `05a_patch_final_data.py` added two missing items, both taken from the raw FY2024 filing data:
  FY2024 sales by country (U.S. 142,196; China 66,952; Other 181,887) and FY2024 cash beginning/ending
  balances (30,737 and 29,943). Originals are kept in `final_data/_backup_before_patch/`.
  The root cause is in the table-building script, so the patch should be folded in there.
  `all_facts_long.csv`, `data_checks.csv` and the DuckDB file were not regenerated.

### Database tables in `data/apple_final.db`

The six data tables above, plus `data_dictionary`, `data_checks`, `all_facts_long`.
The assistant opens this database **read-only**.

## Known limits

- Structured numbers cover FY2019 to FY2024 only; the balance sheet starts in FY2020.
- No FY2025 or quarterly numbers in the database yet (they exist in the text reports; parsing is planned).
- The statements of shareholders' equity are not loaded.
- Regional operating income excludes unallocated R&D and corporate costs, so regions sum to more than consolidated operating income.
- "China" in `sales_by_region` is Greater China (includes Hong Kong and Taiwan).

## How to run

Requires Python 3.10+ (developed on 3.14).

```
python -m venv .venv
.venv\Scripts\Activate.ps1            # Windows PowerShell
pip install -r requirements.txt
pip install groq python-dotenv        # needed from Step 3 onwards

python 00_check_setup.py
python 01_inspect_data.py
python 02_build_database.py           # raw CSVs -> data/apple_financials.db (cross-check)
python 04_build_metrics.py            # optional, enables the cross-check in step 5

python 05a_patch_final_data.py        # one-time fix of two gaps in final_data
python 05_load_final_data.py          # final_data -> data/apple_final.db, with checks

# Steps 3 and 6 need a free Groq API key in a file named .env :
#   GROQ_API_KEY=your_key_here
python 03_first_llm_call.py
python 06_text_to_sql.py "What was net income in 2023?"
python 06_text_to_sql.py --demo
python 06_text_to_sql.py --schema     # shows exactly what the LLM is told
```

## Safety

- The API key lives only in `.env`, which is listed in `.gitignore`. Never commit it.
- The database is opened **read-only** by the assistant, and only a single `SELECT` is accepted.
- `data/` and `outputs/` are generated locally and are not committed. `final_data/` is committed.

## Outputs

- `outputs/data_inventory.txt`: report from Step 1.
- `outputs/query_log.jsonl`: every question, SQL, result, token count and time (used later for evaluation).

## Tools

Python, pandas, SQLite, Groq API (`openai/gpt-oss-120b`). Planned: LangGraph, ChromaDB, rank_bm25,
sentence-transformers, RAGAS, LangSmith, Streamlit.
