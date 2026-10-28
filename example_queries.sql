-- ===========================================================================================
-- Example DuckDB queries for the Apple final tables
-- Open the database:   duckdb final_data/apple_final.duckdb         (DuckDB command line)
--   or in Python:      import duckdb
--                      con = duckdb.connect("final_data/apple_final.duckdb")
--                      con.sql("SELECT * FROM income_statement").show()
-- Tables: income_statement, balance_sheet, cash_flow, sales_by_category, sales_by_region,
--         sales_by_country, all_facts_long, data_checks, data_dictionary
-- All money is USD millions. Fiscal year ends in late September.
-- ===========================================================================================

-- 1. Sales growth and margins, year by year
SELECT fiscal_year,
       net_sales,
       ROUND(100.0 * (net_sales - LAG(net_sales) OVER (ORDER BY fiscal_year))
             / LAG(net_sales) OVER (ORDER BY fiscal_year), 1) AS sales_growth_pct,
       ROUND(100.0 * gross_margin / net_sales, 1)     AS gross_margin_pct,
       ROUND(100.0 * operating_income / net_sales, 1) AS operating_margin_pct,
       ROUND(100.0 * net_income / net_sales, 1)       AS net_margin_pct
FROM income_statement
ORDER BY fiscal_year;

-- 2. Which product categories are growing, and how big is each one?
SELECT fiscal_year, category, net_sales_usd_m,
       ROUND(100.0 * net_sales_usd_m / SUM(net_sales_usd_m) OVER (PARTITION BY fiscal_year), 1) AS share_of_sales_pct,
       ROUND(100.0 * (net_sales_usd_m - LAG(net_sales_usd_m) OVER (PARTITION BY category ORDER BY fiscal_year))
             / LAG(net_sales_usd_m) OVER (PARTITION BY category ORDER BY fiscal_year), 1) AS yoy_growth_pct
FROM sales_by_category
ORDER BY fiscal_year, net_sales_usd_m DESC;

-- 3. Regions in the latest year: size and segment profitability
SELECT region, net_sales_usd_m, operating_income_usd_m,
       ROUND(100.0 * operating_income_usd_m / net_sales_usd_m, 1) AS segment_margin_pct,
       ROUND(100.0 * net_sales_usd_m / SUM(net_sales_usd_m) OVER (), 1) AS share_of_sales_pct
FROM sales_by_region
WHERE fiscal_year = (SELECT MAX(fiscal_year) FROM sales_by_region)
ORDER BY net_sales_usd_m DESC;

-- 4. Products vs Services: Services earns a much higher gross margin
SELECT fiscal_year,
       ROUND(100.0 * (products_net_sales - products_cost_of_sales) / products_net_sales, 1) AS products_gross_margin_pct,
       ROUND(100.0 * (services_net_sales - services_cost_of_sales) / services_net_sales, 1) AS services_gross_margin_pct,
       ROUND(100.0 * services_net_sales / net_sales, 1) AS services_share_of_sales_pct
FROM income_statement
ORDER BY fiscal_year;

-- 5. Free cash flow and money returned to shareholders (capex, dividends and buybacks are negative in the data)
SELECT fiscal_year,
       operating_cash_flow,
       operating_cash_flow + capital_expenditures AS free_cash_flow,
       -(dividends_paid + share_repurchases)      AS returned_to_shareholders,
       ROUND(100.0 * -(dividends_paid + share_repurchases) / (operating_cash_flow + capital_expenditures), 0) AS pct_of_free_cash_flow_returned
FROM cash_flow
ORDER BY fiscal_year;

-- 6. Cash, debt and net cash position
SELECT fiscal_year,
       cash_and_equivalents + marketable_securities_current + marketable_securities_non_current AS cash_and_securities,
       commercial_paper + term_debt_current + term_debt_non_current                              AS total_debt,
       cash_and_equivalents + marketable_securities_current + marketable_securities_non_current
         - (commercial_paper + term_debt_current + term_debt_non_current)                        AS net_cash
FROM balance_sheet
ORDER BY fiscal_year;

-- 7. How dependent is Apple on Greater China? (joins two tables)
SELECT r.fiscal_year,
       r.net_sales_usd_m AS greater_china_sales,
       i.net_sales       AS total_net_sales,
       ROUND(100.0 * r.net_sales_usd_m / i.net_sales, 1) AS greater_china_share_pct
FROM sales_by_region r
JOIN income_statement i USING (fiscal_year)
WHERE r.region = 'Greater China'
ORDER BY r.fiscal_year;

-- 8. Data quality: anything that does not add up, or that Apple changed in a later report
SELECT * FROM data_checks WHERE status NOT IN ('OK', 'OK_rounding');

-- 9. What does each column mean?
SELECT column_name, description, unit FROM data_dictionary WHERE table_name = 'income_statement';

-- 10. Find any number by name (all_facts_long keeps every line from the reports)
SELECT fiscal_year, line_item, value, unit
FROM all_facts_long
WHERE table_name = 'cash_flow' AND line_item ILIKE '%dividends%'
ORDER BY fiscal_year;
