# Row IDs, Concurrency, and Conflict Handling

> **Context:** Discussion about Databricks `IDENTITY` behavior, concurrent edits in a Streamlit app, and strategies to avoid “lost updates”.

## 1) Initial Question

**User:**  
*It was successful. `row_id` primary key is now created. What if I delete a few rows? Will the remaining `row_id` values remain the same? Will the deleted `row_id` be assigned to any new rows added to table in future?*

**Answer:**  
Great! Yes, the `row_id` values will remain stable after deletions, and deleted IDs will **not** be reused for new rows.

### How `IDENTITY` columns behave

**After deleting rows:**

*   Remaining rows **keep** their original `row_id` values unchanged.
*   The `row_id` sequence **continues** from where it left off, not from any gaps.
*   Deleted IDs are **permanently skipped** and never reassigned.

**Example**  
If you have rows with `row_id` 1, 2, 3, 4, 5 and delete rows 2 and 4:

*   Remaining rows: **1, 3, 5** (values stay the same)
*   Next inserted row gets **`row_id = 6`** (not 2 or 4)
*   IDs **2 and 4** are permanently lost

### Why this matters for your Streamlit app

*   ✅ **Good:** `row_id` is a stable, permanent identifier for each row
*   ✅ **Good:** You can safely use `row_id` as a reference in your app logic
*   ℹ️ **Note:** There will be **gaps** in the sequence after deletions, which is normal and expected

### Important limitation (Databricks)

**IDENTITY columns in Databricks disable concurrent transactions.** This means only **one write operation** can happen at a time on this table. For a single‑user Streamlit app, this is fine, but be aware if multiple users will edit simultaneously.

> **Bottom line:** The `row_id` provides a reliable, unchanging identifier—perfect for tracking and editing specific rows in your table‑editor app.
> 

## 2) Follow‑up on “multiple users edit”

**User:**  
*What is the meaning of multiple users edit? Does it mean only one SQL query can run on the table at a time? But at the same time many users will be able to use the Streamlit app right but only one user can run the query?*

**Answer:**  
The **“multiple users edit”** limitation means that **only one write operation** (`INSERT`, `UPDATE`, `DELETE`, `MERGE`) can execute on the table **at a time**. Here’s what that means practically:

### What’s allowed simultaneously

*   ✅ Multiple users **reading** (**`SELECT`** queries) — no problem; reads can be concurrent.
*   ✅ Multiple users **using the Streamlit app**.
*   ✅ Multiple **`SELECT`** queries at the same time.

### What’s **not** allowed simultaneously

*   ❌ Two users trying to **`INSERT`/`UPDATE`/`DELETE`** at the exact same time.
*   ❌ One user editing **while** another user edits; operations are **serialized (queued)**.

### Practical examples (Streamlit app)

**Scenario 1: No conflict**

*   User A is browsing (SELECT)
*   User B is browsing (SELECT)
*   User C edits a row (UPDATE)
*   ✅ All fine — reads don’t block each other or writes

**Scenario 2: Conflict**

*   User A clicks **Save** (UPDATE row 1) at `10:00:00.000`
*   User B clicks **Save** (UPDATE row 2) at `10:00:00.001`
*   ❌ User B’s operation waits until User A’s completes, or may fail with a conflict error

**For your use case:** If edits are occasional, this is usually fine. Conflicts only happen when two users literally click “Save” within milliseconds.

**Alternative if concurrent writes are critical:** Consider a **`ROW_NUMBER()`** approach instead of `IDENTITY`—you lose auto‑increment for new rows but **gain concurrent write capability**.


## 3) Handling conflicting updates (optimistic concurrency)

**User:**  
*So, for IDENTITY columns the updates will be sequential but even then, how do we handle conflicting updates of two users (e.g., when both users fetch data they see a cell value as A, one user updates it to B but the second user still sees A & changes it to C). And will we handle a similar situation for `ROW_NUMBER()` approach?*

**Answer:**  
You’ve identified **optimistic concurrency control**. This issue exists regardless of `IDENTITY` vs `ROW_NUMBER()`—it’s about **data versioning**, not ID generation.

### The “Lost Update” scenario

1.  User A reads row: `name = "A"`
2.  User B reads row: `name = "A"`
3.  User A updates to `name = "B"` and saves
4.  User B updates to `name = "C"` and saves (overwrites A’s change)  
    **Result:** User A’s change is lost

This happens with both `IDENTITY` and `ROW_NUMBER()` because **neither** prevents concurrent modifications to the **same** row.


## 4) Solutions for your Streamlit app

### Option 1: Optimistic locking with a **version** column (recommended)

Add a version counter and last‑updated timestamp to detect and reject conflicting updates.

```sql
-- Add version tracking
ALTER TABLE onesource_eu_dev_rni.ux_sn_global.ux_sn_conso_master_table_dbapp
ADD COLUMN version BIGINT DEFAULT 0;

ALTER TABLE onesource_eu_dev_rni.ux_sn_global.ux_sn_conso_master_table_dbapp
ADD COLUMN last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP();
```

**In Streamlit (conceptual flow):**

```python
# When user loads data, store the version/timestamp returned with the row
original_version = row["version"]
original_timestamp = row["last_updated"]

# When user saves, update only if version has not changed
rows_affected = spark.sql(f"""
UPDATE onesource_eu_dev_rni.ux_sn_global.ux_sn_conso_master_table_dbapp
SET name = 'C',
    version = version + 1,
    last_updated = CURRENT_TIMESTAMP()
WHERE row_id = {row_id}
  AND version = {original_version}
""").collect()[0][0]  # Example of retrieving affected row count

if rows_affected == 0:
    # Conflict detected — someone else modified this row
    st.error("This row was modified by another user. Please refresh and try again.")
else:
    st.success("Saved successfully!")
```

> **Why this works:** Two users saving with the same `version` will result in only the **first** update succeeding; the second sees `rows_affected = 0` and gets a friendly conflict message.

***

### Option 2: Use **Delta Lake Time Travel** to detect table changes

Rely on Delta’s table versions to detect that **something** changed since the user loaded the data.

```python
# When loading data
table_version = spark.sql("DESCRIBE HISTORY onesource_eu_dev_rni.ux_sn_global.ux_sn_conso_master_table_dbapp LIMIT 1") \
                     .collect()[0]["version"]

# Before saving
current_version = spark.sql("DESCRIBE HISTORY onesource_eu_dev_rni.ux_sn_global.ux_sn_conso_master_table_dbapp LIMIT 1") \
                       .collect()[0]["version"]

if current_version != table_version:
    st.warning("Table was modified by another user. Showing latest data...")
    # Reload and ask user to reapply changes
```

> **Note:** This detects **any** table change, not necessarily the same row; it’s simpler but coarser‑grained than the version column.

***

### Option 3: **Last‑write‑wins** (simplest, but risky)

Do nothing special; the last save overwrites prior changes.

```python
# Simple update - no conflict detection
spark.sql(f"""
UPDATE onesource_eu_dev_rni.ux_sn_global.ux_sn_conso_master_table_dbapp
SET name = 'C'
WHERE row_id = {row_id}
""")
```

⚠️ **Risk:** Silent data loss when two users edit the same row.

***

## 5) `IDENTITY` vs `ROW_NUMBER()` for concurrent writes

| Aspect                      | `IDENTITY` Column                   | `ROW_NUMBER()`                      |
| --------------------------- | ----------------------------------- | ----------------------------------- |
| Concurrent **READS**        | ✅ Unlimited                         | ✅ Unlimited                         |
| Concurrent **WRITES**       | ❌ Serialized (one at a time)        | ✅ Allowed (multiple simultaneous)   |
| Conflict detection          | ❌ Not built-in (use version column) | ❌ Not built-in (use version column) |
| Auto‑increment for new rows | ✅ Automatic                         | ❌ Manual (app must assign keys)     |

**Key point:** Both approaches **still need** optimistic concurrency (Option 1) or a similar safeguard to prevent lost updates on **the same row**. The `IDENTITY` limitation only affects **parallelism** across different rows; it does **not** solve same‑row conflicts.

***

## 6) Recommendation

*   Keep your **`row_id` as `IDENTITY`** for stable, automatic keys.
*   Add **`version`** and **`last_updated`** columns.
*   Implement **optimistic locking** in Streamlit (check `version` in `WHERE` and increment it on success).
*   Show a clear **“this row was modified by someone else”** message and prompt users to refresh.

This gives you reliable IDs plus proper conflict detection—ideal for collaborative editing in your Streamlit app.
