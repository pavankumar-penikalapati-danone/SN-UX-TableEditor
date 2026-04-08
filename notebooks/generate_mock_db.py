import pandas as pd
import sqlite3

EXCEL_FILE = "mock_data.xlsx"
DB_FILE = "mock.db"

# Load your Excel file
df = pd.read_excel(EXCEL_FILE)

# Normalize column names (match your Excel columns exactly)
df.columns = [c.strip().replace(" ", "_") for c in df.columns]

# Create SQLite database
conn = sqlite3.connect(DB_FILE)
df.to_sql("product_master", conn, if_exists="replace", index=False)
conn.close()

print("Mock DB created successfully:")
print(f"- Input Excel: {EXCEL_FILE}")
print(f"- Output DB:   {DB_FILE}")
print(f"- Table:       product_master")