# COMMAND ----------
import os
from dotenv import load_dotenv

# COMMAND ----------

# Set up Databricks or local MLflow tracking
def is_databricks() -> bool:
    """Check if the code is running in a Databricks environment."""
    return "DATABRICKS_RUNTIME_VERSION" in os.environ

# COMMAND ----------

# For scripts, not notebook cells: uv command can also be used to load env variables form .env
# UV command: python -m uv run --env-file .env python your_script.py
if not is_databricks():
    print("Code is running in LOCAL venv!!")
    load_dotenv()

    DB_HOST = os.getenv("DATABRICKS_HOST")
    DB_WH = os.getenv("DATABRICKS_WAREHOUSE_ID")

    print(f"DATABRICKS_HOST: {DB_HOST}")
    print(f"DATABRICKS_WAREHOUSE_ID: {DB_WH}")


# COMMAND ----------
