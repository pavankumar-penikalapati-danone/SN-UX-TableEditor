import os
import re
import certifi
import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config
from dotenv import load_dotenv
from config import SHARED_CSS, APP_LOGO_IMAGE, APP_LOGO_ICON, APP_LOGO_LINK
# Convert text to Unicode Mathematical Bold Sans-Serif for visual bold in st.dataframe headers
def _to_bold(text):
    result = []
    for ch in str(text):
        if 'A' <= ch <= 'Z':
            result.append(chr(0x1D5D4 + ord(ch) - ord('A')))
        elif 'a' <= ch <= 'z':
            result.append(chr(0x1D5EE + ord(ch) - ord('a')))
        elif '0' <= ch <= '9':
            result.append(chr(0x1D7EC + ord(ch) - ord('0')))
        else:
            result.append(ch)
    return ''.join(result)

def _bold_col_cfg(df):
    return {c: st.column_config.Column(label=_to_bold(c)) for c in df.columns}


# ----------------------------------------
# STREAMLIT SETUP
# ----------------------------------------
st.set_page_config(page_title="Delta Version Diff (All Columns)", layout="wide", initial_sidebar_state="expanded")
st.markdown(SHARED_CSS, unsafe_allow_html=True)



st.markdown(
    """
    <h1 class="page-title">
        Delta Version Diff – Previous vs Current (All Columns)
    </h1>
    """,
    unsafe_allow_html=True
)

st.logo(
    APP_LOGO_IMAGE, size="large",
    icon_image=APP_LOGO_ICON,
    link=APP_LOGO_LINK,
)

# ── Hide Admin page from sidebar for non-admin users ─────────
_admin_users_env = [u.strip().lower() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()]
_nav_user = ""
try:
    _hdrs = st.context.headers or {}
    for _k in ("X-Forwarded-Email", "X-Forwarded-Preferred-Username"):
        _v = _hdrs.get(_k, "").strip()
        if _v:
            _nav_user = _v.lower()
            break
except Exception:
    pass
if _admin_users_env and _nav_user not in _admin_users_env:
    st.markdown('<style>[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"] { display: none !important; }</style>', unsafe_allow_html=True)


def is_running_in_databricks():
    return "DATABRICKS_RUNTIME_VERSION" in os.environ

if not is_running_in_databricks():
    load_dotenv()

cfg = Config()

_HOST = (cfg.host or "").replace("https://", "").replace("http://", "").rstrip("/")
_HTTP_PATH = f"/sql/1.0/warehouses/{cfg.warehouse_id}"
_CA_FILE = certifi.where()


def run_sql(query: str) -> pd.DataFrame:
    token = st.context.headers.get("X-Forwarded-Access-Token") if hasattr(st, "context") else None
    if token:
        conn = sql.connect(
            server_hostname=_HOST,
            http_path=_HTTP_PATH,
            access_token=token,
            _tls_trusted_ca_file=_CA_FILE,
            _tls_no_verify=False,
        )
    else:
        conn = sql.connect(
            server_hostname=cfg.host,
            http_path=_HTTP_PATH,
            credentials_provider=lambda: cfg.authenticate,
        )
    with conn:
        with conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchall_arrow().to_pandas()

# ----------------------------------------
# VALIDATION HELPERS
# ----------------------------------------
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

def q_ident(name: str) -> str:
    name = name.strip()
    if not IDENT_RE.match(name):
        raise ValueError(f"Invalid identifier: {name}")
    return f"`{name}`"

def q_fqn(fqn: str) -> str:
    if not re.match(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$", fqn):
        raise ValueError("Table must be catalog.schema.table")
    return fqn

# ----------------------------------------
# DEFAULTS
# ----------------------------------------
DEFAULT_TABLE = "onesource_eu_dev_rni.ux_sn_global.ux_sn_conso_master_table_dbapp_clone"
DEFAULT_PK = "row_id"
HIDE_OPERATIONS = ["OPTIMIZE"]

# ----------------------------------------
# SIDEBAR INPUT
# ----------------------------------------
with st.sidebar:
    st.header("Settings")
    run_btn = st.button("Compute Diff", type="primary", use_container_width=True)
    table_fqn = st.text_input("Table (catalog.schema.table)", value=DEFAULT_TABLE)
    pk_col = st.text_input("Primary key column", value=DEFAULT_PK)
    history_limit = st.number_input("Versions to load", 50, 5000, 500, 50)
    max_changes = st.number_input("Max diff rows", 100, 50000, 5000, 100)

t = q_fqn(table_fqn)
pk = q_ident(pk_col)

# ----------------------------------------
# LOAD HISTORY
# ----------------------------------------
hide_ops_sql = ", ".join([f"'{op}'" for op in HIDE_OPERATIONS])

try:
    history_df = run_sql(f"""
    SELECT *
    FROM (DESCRIBE HISTORY {t})
    WHERE operation NOT IN ({hide_ops_sql})
    ORDER BY version DESC
    LIMIT {history_limit}
    """)
except Exception as _ex:
    st.error(f"Failed to load table history: {_ex}")
    st.stop()

if history_df.empty:
    st.warning("No history found.")
    st.stop()

versions = history_df["version"].astype(int).tolist()
selected_version = st.selectbox("Current Version", versions, index=0)

pos = list(history_df["version"]).index(selected_version)
if pos == len(history_df) - 1:
    st.info("Oldest version loaded — no previous version available.")
    st.stop()

prev_version = int(history_df.loc[pos + 1, "version"])
st.info(f"Comparing **prev = {prev_version} → curr = {selected_version}**")

# ----------------------------------------
# LOAD SCHEMA
# ----------------------------------------
try:
    desc_df = run_sql(f"DESCRIBE {t}")
    all_cols = [
        c.strip()
        for c in desc_df["col_name"].tolist()
        if c and not str(c).startswith("#")
    ]
except Exception as _ex:
    st.error(f"Failed to load table schema: {_ex}")
    st.stop()

diff_cols = [c for c in all_cols if c != pk_col]
st.write(f"Detected **{len(diff_cols)}** non-PK columns.")

# Stack for updates
stack_items = []
for c in diff_cols:
    stack_items.extend([
        f"'{c}'",
        f"CAST(prev.`{c}` AS STRING)",
        f"CAST(curr.`{c}` AS STRING)"
    ])
stack_expr = (
    f"stack({len(diff_cols)}, {', '.join(stack_items)}) "
    f"s as column_name, old_value, new_value"
)

# Column lists excluding PK (IMPORTANT FIX)
insert_col_str = ", ".join([f"curr.`{c}`" for c in all_cols if c != pk_col])
delete_col_str = ", ".join([f"prev.`{c}`" for c in all_cols if c != pk_col])

# ----------------------------------------
# SQL QUERIES
# ----------------------------------------

update_sql = f"""
SELECT
    curr.{pk} AS key_id,
    'UPDATE' AS change_type,
    column_name,
    old_value,
    new_value
FROM {t} VERSION AS OF {selected_version} curr
JOIN {t} VERSION AS OF {prev_version} prev
    ON curr.{pk} = prev.{pk}
LATERAL VIEW {stack_expr}
WHERE old_value IS DISTINCT FROM new_value
LIMIT {max_changes}
"""

insert_sql = f"""
SELECT
    curr.{pk} AS key_id,
    'INSERT' AS change_type,
    {insert_col_str}
FROM {t} VERSION AS OF {selected_version} curr
LEFT JOIN {t} VERSION AS OF {prev_version} prev
    ON curr.{pk} = prev.{pk}
WHERE prev.{pk} IS NULL
LIMIT {max_changes}
"""

delete_sql = f"""
SELECT
    prev.{pk} AS key_id,
    'DELETE' AS change_type,
    {delete_col_str}
FROM {t} VERSION AS OF {prev_version} prev
LEFT JOIN {t} VERSION AS OF {selected_version} curr
    ON prev.{pk} = curr.{pk}
WHERE curr.{pk} IS NULL
LIMIT {max_changes}
"""

# ----------------------------------------
# RUN DIFF
# ----------------------------------------
# Auto-compute on first load; re-compute on button click
_should_compute = run_btn or "delta_diff_computed" not in st.session_state

if _should_compute:
    try:
        with st.spinner("Computing diffs..."):
            updated_df = run_sql(update_sql)
            inserted_df = run_sql(insert_sql)
            deleted_df = run_sql(delete_sql)

            # add empty diff columns for inserts/deletes
            for df in [inserted_df, deleted_df]:
                df["column_name"] = ""
                df["old_value"] = ""
                df["new_value"] = ""

            final_df = pd.concat([updated_df, inserted_df, deleted_df], ignore_index=True)

            # Cache results in session state
            st.session_state["delta_updated_df"] = updated_df
            st.session_state["delta_inserted_df"] = inserted_df
            st.session_state["delta_deleted_df"] = deleted_df
            st.session_state["delta_final_df"] = final_df
            st.session_state["delta_diff_computed"] = True
            st.session_state["delta_prev_version"] = prev_version
            st.session_state["delta_curr_version"] = selected_version
    except Exception as _ex:
        st.error(f"Failed to compute diff: {_ex}")
        st.stop()

if st.session_state.get("delta_diff_computed"):
    updated_df = st.session_state["delta_updated_df"]
    inserted_df = st.session_state["delta_inserted_df"]
    deleted_df = st.session_state["delta_deleted_df"]
    final_df = st.session_state["delta_final_df"]

    # ----------------------------------------
    # SUMMARY
    # ----------------------------------------
    st.markdown("## Summary (Record Level)")
    c1, c2, c3 = st.columns(3)

    with c1:
        st.write("### Updated Keys")
        st.code(", ".join(map(str, sorted(updated_df["key_id"].unique())))
                if not updated_df.empty else "(none)")

    with c2:
        st.write("### Inserted Keys")
        st.code(", ".join(map(str, sorted(inserted_df["key_id"].unique())))
                if not inserted_df.empty else "(none)")

    with c3:
        st.write("### Deleted Keys")
        st.code(", ".join(map(str, sorted(deleted_df["key_id"].unique())))
                if not deleted_df.empty else "(none)")

    # ----------------------------------------
    # TABS UI
    # ----------------------------------------
    st.markdown("## 🔎 Change Details")

    tab1, tab2, tab3, tab4 = st.tabs([
        "🔄 Updates", "➕ Inserts", "➖ Deletes", "📘 All Changes"
    ])

    # UPDATES TAB
    with tab1:
        st.markdown("### 🔄 Updated Rows (Column-level Differences)")
        if updated_df.empty:
            st.info("No updated rows found.")
        else:
            _u_df = updated_df[["key_id", "change_type", "column_name", "old_value", "new_value"]]
            st.dataframe(
                _u_df,
                use_container_width=True, hide_index=True,
                column_config=_bold_col_cfg(_u_df)
            )

    # INSERTS TAB
    with tab2:
        st.markdown("### ➕ Inserted Rows (Full Row Data)")
        if inserted_df.empty:
            st.info("No inserted rows found.")
        else:
            insert_cols = [c for c in inserted_df.columns if c not in ["column_name", "old_value", "new_value"]]
            st.dataframe(inserted_df[insert_cols],use_container_width=True, hide_index=True, column_config=_bold_col_cfg(inserted_df[insert_cols]))

    # DELETES TAB
    with tab3:
        st.markdown("### ➖ Deleted Rows (Full Row Data)")
        if deleted_df.empty:
            st.info("No deleted rows found.")
        else:
            delete_cols = [c for c in deleted_df.columns if c not in ["column_name", "old_value", "new_value"]]
            st.dataframe(deleted_df[delete_cols], use_container_width=True, hide_index=True, column_config=_bold_col_cfg(deleted_df[delete_cols]))

    # ALL TAB
    with tab4:
        st.markdown("### 📘 All Change Types Combined")
        st.dataframe(final_df,use_container_width=True, hide_index=True, column_config=_bold_col_cfg(final_df))

    # ----------------------------------------
    # DOWNLOAD
    # ----------------------------------------
    st.download_button(
        "Download CSV",
        final_df.to_csv(index=False),
        file_name=f"delta_diff_{prev_version}_to_{selected_version}.csv",
        mime="text/csv",
    )

    # ----------------------------------------
    # SHOW SQL
    # ----------------------------------------
    with st.expander("Show SQL Queries"):
        st.code(update_sql, "sql")
        st.code(insert_sql, "sql")
        st.code(delete_sql, "sql")