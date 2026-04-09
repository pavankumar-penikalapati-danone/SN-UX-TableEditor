# Databricks notebook source
# MAGIC %md
# MAGIC # Load Macro Zone / CBU / BU / RU Data
# MAGIC Reads `Macro Zone_CBU_BU_RU All 2025.xlsx` and creates/overwrites the Delta table `onesource_eu_dev_rni.ux_sn_global.macro_zone_cbu_bu_ru_2025`.

# COMMAND ----------

# MAGIC %pip install openpyxl -q

# COMMAND ----------

import pandas as pd
from pyspark.sql import functions as F

# ── Configuration ────────────────────────────────────────────
EXCEL_PATH = "/Workspace/Users/pavankumar.penikalapati@external.danone.com/sn-ux-db-table-editor/app/assets/Macro Zone_CBU_BU_RU All 2025.xlsx"
TARGET_TABLE = "onesource_eu_dev_rni.ux_sn_global.macro_zone_cbu_bu_ru_2025"
SHEET_NAME = "Sheet1"

# ── Read Excel ───────────────────────────────────────────────
df = pd.read_excel(EXCEL_PATH, sheet_name=SHEET_NAME)
print(f"Read {len(df)} rows, {len(df.columns)} columns from '{SHEET_NAME}'")
print(f"Columns: {list(df.columns)}")

# ── Convert to Spark and clean column names ──────────────────
sdf = (spark.createDataFrame(df)
    .withColumnRenamed("Macro Zone New", "macro_zone_new")
    .withColumnRenamed("CBU", "cbu")
    .withColumnRenamed("BU", "bu")
    .withColumnRenamed("RU", "ru")
)

# ── Write to Delta table (overwrite) ────────────────────────
sdf.write.mode("overwrite").saveAsTable(TARGET_TABLE)
print(f"✓ Table created/overwritten: {TARGET_TABLE}")

# ── Verify ───────────────────────────────────────────────────
count = spark.sql(f"SELECT COUNT(*) AS cnt FROM {TARGET_TABLE}").collect()[0]["cnt"]
print(f"  Total rows: {count}")
spark.sql(f"SELECT * FROM {TARGET_TABLE} LIMIT 10").display()

# COMMAND ----------

spark.sql(f"""
    SELECT 
        macro_zone_new,
        COUNT(DISTINCT cbu) AS distinct_cbu,
        COUNT(DISTINCT bu) AS distinct_bu,
        COUNT(DISTINCT ru) AS distinct_ru,
        COUNT(*) AS total_rows
    FROM {TARGET_TABLE}
    GROUP BY macro_zone_new
    ORDER BY macro_zone_new
""").display()
