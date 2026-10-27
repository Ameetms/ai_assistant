# Apple (AAPL) data for your AI Business Analyst Assistant

One command downloads every document and builds the Excel workbook you need for the project.
All data comes from **SEC EDGAR**, where public filing content is free to access and reuse.

## What you will get

```
apple_project_data/
  Apple_Financial_Data.xlsx          <- the workbook (9 sheets, explained below)
  documents/
    10-K_annual_reports/             5 annual reports      (original .htm files)
    10-Q_quarterly_reports/          18 quarterly reports  (original .htm files)
    8-K_earnings_releases/           23 earnings releases  (original .htm files)
    text/                            a clean .txt copy of every document  <- used for RAG later
  tables/                            every workbook sheet also saved as .csv  <- used for SQL later
  raw/                               raw SEC data files (kept so nothing has to be downloaded twice)
```

Counts are for filings from 1 Jan 2021 to today and can differ by one or two. About 46 documents.

### The workbook sheets

| Sheet | What it holds | Used for |
|---|---|---|
| README | Source, dates, definitions | Reference |
| Filings_Index | Every filing, its SEC link and file names | Citations ("Source: ...") |
| Quarterly_Financials | Net sales, cost of sales, margin, operating income, EPS, cash flow, assets ... per fiscal quarter | SQL questions |
| Annual_Financials | The same per fiscal year | SQL questions |
| Sales_By_Category | iPhone, Mac, iPad, Wearables, Services per quarter | SQL questions |
| Sales_By_Region | Americas, Europe, Greater China, Japan, Rest of Asia Pacific per quarter | SQL questions |
| Segment_Facts_Raw | Every segment-level number found in the filings | Exploring / fixing |
| Checks | Do the categories and regions add up to total net sales? | **Your data-quality proof** |
| Known_Value_Checks | Numbers read by hand from Apple's June 2026 10-Q vs. what was downloaded | Trust check |

Amounts are in **USD millions**. Apple's fiscal year ends in late September, so
**FY2026 Q3 = the quarter ended 27 June 2026**. Apple does not report Q4 separately,
so Q4 = full year minus Q1 to Q3 (the sheet says so).

## Setup (one time, about 15 minutes)

### 1. Install Python
Download Python 3.11 or newer from https://www.python.org/downloads/
* **Windows:** on the first installer screen tick **"Add python.exe to PATH"**, then Install.
* **Mac:** run the installer.

Check it worked, in a terminal (Windows: search "Command Prompt"; Mac: open "Terminal"):
```
python --version          (Windows)
python3 --version         (Mac)
```
You should see `Python 3.11` or higher.

### 2. Open this folder in a terminal
Easiest: install **VS Code** (https://code.visualstudio.com), choose *File > Open Folder* and pick
this `apple_rag_project` folder, then *Terminal > New Terminal*.

### 3. Install the libraries
```
pip install -r requirements.txt           (Windows)
pip3 install -r requirements.txt          (Mac)
```
If you see `'pip' is not recognized`, use `python -m pip install -r requirements.txt` instead.

## Run it

### Quick trial first (about 1 minute, 3 documents)
Use your **real name and email**: the SEC asks every script to identify its owner, and these two
values are sent to the SEC with each request (that is the only place they go).
```
python download_apple_data.py --name "Your Name" --email you@example.com --limit 1
```
(Mac: use `python3`.) Open `apple_project_data/Apple_Financial_Data.xlsx` and look around.

### Full run (a few minutes)
```
python download_apple_data.py --name "Your Name" --email you@example.com
```
The script is polite to the SEC (at most 5 requests per second) and **safe to re-run**: finished
files are skipped, so if your internet drops just run the same command again.

## How to check the result (this is your first analyst task)

1. Open the **Checks** sheet. Every row should say `OK`: category and region sales add up to total net sales.
2. Open **Known_Value_Checks**. For FY2026 Q3 all six rows should say `OK`
   (total $109,417M; iPhone 54,252; Mac 10,352; iPad 6,191; Wearables 7,883; Services 30,739).
3. Pick two or three numbers at random, open the matching file in `documents/` and compare by eye.

If a row says `CHECK`, `no categories`, or a sheet is empty, **do not fix it yourself yet**: copy the
messages the script printed plus a screenshot of the Checks sheet and send them to Claude.
Different years sometimes label the same thing differently, and that is exactly the kind of cleaning
we will do together in the next step.

## Troubleshooting

| Message | What it means / fix |
|---|---|
| `python is not recognized` / `command not found` | Python is not installed or not on PATH. Reinstall and tick "Add to PATH" (Windows) or use `python3` (Mac). |
| `ModuleNotFoundError: No module named 'bs4'` (or pandas, lxml ...) | Run the install step again (step 3). |
| `SEC returned 403` | The SEC rejected the request. Use a real name and email, wait 10 minutes, run again. Do not run several copies at once. |
| `PermissionError ... .xlsx` | The workbook is open in Excel. Close it and run again. |
| `... downloads failed` | Run the same command again; finished files are skipped. |
| `Note: no data found for: ...` | Apple used a different accounting tag for that item. Not an error; tell Claude. |

## Good to know

* **Source and licence.** SEC EDGAR public filing content is free to access and reuse. Show
  "Source: SEC EDGAR" under answers in your project, and keep the Filings_Index sheet, which holds
  the link for every document. The SEC does not guarantee the accuracy of extracted data, which is why
  the Checks sheet exists.
* **What is not included on purpose.** Earnings-call transcripts (copyrighted by third parties; Apple
  publishes only audio) and stock prices (free price feeds restrict republishing).
* **Keeping it current.** Apple reports Q4 FY2026 on 2 November 2026. After its annual report (10-K)
  appears on EDGAR, run the full command again and the new files are added.
* **This script was tested on synthetic, SEC-shaped data, not on the live SEC site.** Your first run is
  the real test. If anything looks off, send Claude the printed output.
