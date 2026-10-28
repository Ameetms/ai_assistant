# Final Apple tables for DuckDB

`build_final_tables.py` takes your yearly `Financial_Report_YYYY_CSV` folders and combines them into
**one clean set of CSV tables** (and a ready-made DuckDB database) that you can query with SQL.

It does not change, move or delete anything you already have (including `build_database.py`).

## 1. Put the files in place

```
ai_assistant_project/
  ai_assistant/
    build_final_tables.py      <-- copy this file here, next to build_database.py
    tables/
      Financial_Report_2021_CSV/   (your existing folders - no change needed)
      Financial_Report_2022_CSV/
      Financial_Report_2023_CSV/
      Financial_Report_2024_CSV/
```
`example_queries.sql` can go anywhere.

## 2. Run it

In the VS Code terminal (with your `.venv` active), from inside the `ai_assistant` folder:

```
pip install duckdb
python build_final_tables.py
```

It finds the yearly folders by name (`Financial_Report_YYYY...`) and reads every CSV inside them. It
recognises each statement by its **title**, so file names and order do not matter.

## 3. What you get (in a new folder `final_data/`)

| File | One row per | Contents |
|---|---|---|
| `income_statement.csv` | fiscal year | net sales, cost of sales, margin, expenses, net income, EPS, shares, Products vs Services split |
| `balance_sheet.csv` | fiscal year | cash, securities, receivables, debt, equity, totals |
| `cash_flow.csv` | fiscal year | operating cash flow, capex, dividends, buybacks, cash balances |
| `sales_by_category.csv` | year x category | iPhone, Mac, iPad, Wearables/Home/Accessories, Services |
| `sales_by_region.csv` | year x region | net sales and segment operating income for the 5 regions |
| `sales_by_country.csv` | year x group | U.S., China, Other countries |
| `all_facts_long.csv` | every number | nothing is lost: every line from every statement |
| `data_checks.csv` | check | do the totals add up? did Apple change a number in a later report? |
| `data_dictionary.csv` | column | plain-English meaning and unit of every column |
| `apple_final.duckdb` | - | all of the above, loaded into DuckDB |
| `load_into_duckdb.sql` | - | the SQL that loads the CSVs, if you want to do it yourself |

How the rows are chosen: a fiscal year appears in up to three yearly reports. The script keeps the
number from the **newest** report (Apple sometimes re-presents earlier years) and tells you which
report each row came from (`source_report`). Money is in USD millions. Cash paid out (capex,
dividends, buybacks) is **negative**, exactly as Apple reports it.

## 4. Check it worked

The last lines it prints should look like this (counts will differ with your years):

```
Fiscal years found : FY2019, FY2020, ... FY2024
Checks: NN passed, 0 failed, N numbers changed between reports.
```

* `0 failed` means total net sales equals the sum of categories, regions and countries, the balance
  sheet balances, and the cash flow adds up, for every year.
* Open `data_checks.csv`. Rows whose status starts with `CHANGED` are Apple re-presenting an old number
  (for example two "Other" cash-flow lines moved between the 2021 and 2022 reports). That is expected.
* Spot-check against Apple's filings:

| Year | Net sales | iPhone | Services | Total assets |
|---|---|---|---|---|
| FY2021 | 365,817 | 191,973 | 68,425 | 351,002 |
| FY2022 | 394,328 | | | 352,755 |
| FY2025 | 416,161 | 209,586 | 109,158 | 359,241 |

## 5. Use it in DuckDB

* **Python / notebook:** `import duckdb; con = duckdb.connect("final_data/apple_final.duckdb"); con.sql("SELECT * FROM income_statement").show()`
* **DuckDB command line:** `duckdb final_data/apple_final.duckdb`
* **VS Code SQL extension or DBeaver:** open `final_data/apple_final.duckdb` as a DuckDB database.
* `example_queries.sql` has 10 analyst-style queries: growth and margins, category mix, regional
  profitability, Products vs Services margin, free cash flow, net cash, Greater China share, data checks.

## 6. Add FY2025 (you do not have it yet)

Your four folders cover reports 2021 to 2024, so the latest year is FY2024. To add FY2025:

1. Download Apple's FY2025 workbook: https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/Financial_Report.xlsx
2. Save it as `tables/Financial_Report_2025.xlsx` (the script reads `.xlsx` directly; run `pip install openpyxl` once).
3. Run `python build_final_tables.py` again.

## 7. Not included yet

* **Quarterly numbers.** The yearly reports only hold full-year figures. Quarterly data comes from the
  10-Q reports; their `Financial_Report` files can be added the same way as the next step.
* **Text of the filings** for the document-search side. Your `prepare_text.py` already handles that.

## 8. What was and was not tested

Tested on the real FY2021 and FY2022 workbooks, read three ways (the .xlsx itself, Excel-style CSVs
and pandas-style CSVs): identical results, all 45 checks passed, values match the filings. The newer
2023-and-later statement layout was tested on a mock-up built from the figures printed in your FY2025
10-K. I have not seen your 2023 and 2024 files or a real FY2025 workbook. If the run prints
`NOT found: ...`, `FAILED:` lines or "line items were not given a column name", copy the printed
output and send it over; those messages are designed to point at exactly what differs.
