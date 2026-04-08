# SQL Queries for Consistent Pagination Using `row_id` Primary Key

I've created SQL queries that use the `row_id` primary key to ensure **consistent and deterministic pagination** for your front-end UI.

---

## Core Pagination Pattern

**Key principle:** Always use `ORDER BY row_id` to ensure consistent, deterministic ordering across all pagination requests.

---

## 1) Basic Pagination (No Filters)

```sql
-- First page (rows 1–20)
SELECT 
  `row_id`,
  `sp_test_id`,
  `cl_test_id`,
  `brand_L1`,
  `test_country`,
  `test_year`,
  `prod_type`,
  `stage`,
  `test_status`,
  `score_main`,
  `ingestion_timestamp`
FROM `onesource_eu_dev_rni`.`ux_sn_global`.`ux_sn_conso_master_table_dbapp`
ORDER BY `row_id`
LIMIT 20;
````

```sql
-- Second page (rows 21–40)
SELECT ...
FROM `onesource_eu_dev_rni`.`ux_sn_global`.`ux_sn_conso_master_table_dbapp`
ORDER BY `row_id`
LIMIT 20 OFFSET 20;
```

```sql
-- Third page (rows 41–60)
SELECT ...
FROM `onesource_eu_dev_rni`.`ux_sn_global`.`ux_sn_conso_master_table_dbapp`
ORDER BY `row_id`
LIMIT 20 OFFSET 40;
```

**Source:** Basic pagination query – First page

***

## 2) Filtered Pagination with `LIMIT` & `OFFSET`

**Example:** Filter by country and year

```sql
-- First page with filters
SELECT 
  `row_id`,
  `sp_test_id`,
  `cl_test_id`,
  `brand_L1`,
  `test_country`,
  `test_year`,
  `prod_type`,
  `stage`,
  `test_status`,
  `score_main`,
  `ingestion_timestamp`
FROM `onesource_eu_dev_rni`.`ux_sn_global`.`ux_sn_conso_master_table_dbapp`
WHERE `test_country` = 'ES' 
  AND `test_year` = 2024
ORDER BY `row_id`
LIMIT 20;
```

```sql
-- Second page with same filters
SELECT ...
FROM `onesource_eu_dev_rni`.`ux_sn_global`.`ux_sn_conso_master_table_dbapp`
WHERE `test_country` = 'ES' 
  AND `test_year` = 2024
ORDER BY `row_id`
LIMIT 20 OFFSET 20;
```

**Source:** Filtered pagination – Country and Year filter with first page

***

## 3) Multi-Filter Example with Larger Batch Size

**Example:** Filter by brand and status

```sql
SELECT 
  `row_id`,
  `sp_test_id`,
  `cl_test_id`,
  `brand_L1`,
  `test_country`,
  `test_year`,
  `prod_type`,
  `stage`,
  `test_status`,
  `score_main`,
  `ingestion_timestamp`
FROM `onesource_eu_dev_rni`.`ux_sn_global`.`ux_sn_conso_master_table_dbapp`
WHERE `brand_L1` ILIKE '%Almiron%'
  AND `test_status` = 'Complete'
ORDER BY `row_id`
LIMIT 50;
```

**Source:** Multi-filter example – Brand and Status with batch size 50

***

## 4) Range-Based Filtering

**Example:** Fetch records after a specific `row_id`

```sql
SELECT 
  `row_id`,
  `sp_test_id`,
  `cl_test_id`,
  `brand_L1`,
  `test_country`,
  `test_year`,
  `prod_type`,
  `stage`,
  `test_status`,
  `score_main`,
  `ingestion_timestamp`
FROM `onesource_eu_dev_rni`.`ux_sn_global`.`ux_sn_conso_master_table_dbapp`
WHERE `row_id` > 1000
  AND `test_year` >= 2023
ORDER BY `row_id`
LIMIT 100;
```

**Source:** Range-based filtering – row\_id greater than 1000 with year filter

***

## Additional Filter Examples

### By Multiple Brands

```sql
WHERE `brand_L1` IN ('Fortimel', 'Almiron', 'Infatrini')
ORDER BY `row_id`
LIMIT 50;
```

### By Date Range

```sql
WHERE `test_year` BETWEEN 2023 AND 2024
  AND `ingestion_timestamp` >= '2026-01-01'
ORDER BY `row_id`
LIMIT 50;
```

### By Product Type and Score

```sql
WHERE `prod_type` = 'Liquid'
  AND `score_main` IN ('superior', 'parity')
ORDER BY `row_id`
LIMIT 50;
```

### Text Search (Case-Insensitive)

```sql
WHERE `sp_test_id` ILIKE '%ES_AO%'
  OR `cl_test_id` ILIKE '%ES_ADU%'
ORDER BY `row_id`
LIMIT 50;
```

***

## Best Practices for Front-End Implementation

*   **Always use `ORDER BY row_id`** — Ensures consistent ordering even when filters change
*   **Calculate `OFFSET`** — `OFFSET = (page_number - 1) × page_size`
*   **Keep page size reasonable** — Recommend **20–100 rows per page** for optimal performance
*   **Include `row_id` in results** — Useful for debugging and cursor-based pagination
*   **Handle empty results** — If returned rows `< LIMIT`, you’ve likely reached the last page
*   **Maintain filter state** — Apply the same `WHERE` clause across all pages of a session

***

## Performance Considerations

*   The `row_id` primary key ensures fast, indexed lookups
*   Filtering on indexed columns (`test_country`, `test_year`, `brand_L1`) improves performance
*   For very large `OFFSET` values (`> 10,000`), consider **cursor-based pagination** using **WHERE row_id > last_seen_id**.
