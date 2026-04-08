import os
import math
import certifi
import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config
from dotenv import load_dotenv
from datetime import date
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


# ---------------- PAGE CONFIG ----------------
st.set_page_config(
    page_title="SN UX – Change Logs",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown("""
<style>

    /* ========= MODIFY st.app_header() ========== */

    /* Main header container */
    [data-testid="stAppHeader"] {
        height: 1rem !important;         /* reduce height */
        min-height: 1rem !important;
        padding: 0.2rem 1rem !important;    /* small padding */
        background-color: white !important; /* optional */
        box-shadow: none !important;        /* remove bottom shadow */
        border-bottom: 1px solid #ddd !important;  /* thin line */
    }

    /* Header title text */
    [data-testid="stAppHeader"] h1,
    [data-testid="stAppHeader"] h2,
    [data-testid="stAppHeader"] h3 {
        font-size: 16px !important;          /* smaller title */
        margin: 0 !important;
        padding: 0 !important;
        line-height: 1 !important;
    }

    /* If header has buttons/icons */
    [data-testid="stAppHeader"] button {
        transform: scale(0.85);
        padding: 2px 4px !important;
        font-size: 12px !important;
    }

</style>
""", unsafe_allow_html=True)

st.markdown("""
<style>

    /* --------------------------------------------- */
    /* REDUCE SIDEBAR WIDTH (WORKS IN ALL PAGES)     */
    /* --------------------------------------------- */

    /* Sidebar container */
    [data-testid="stSidebar"] {
        width: 12rem !important;       /* CHOOSE SIZE */
        min-width: 12rem !important;
        max-width: 12rem !important;
    }

    /* Reduce internal padding */
    [data-testid="stSidebar"] > div:first-child {
        padding: 0.5rem 0.8rem !important;
    }

    /* Compact sidebar text */
    [data-testid="stSidebar"] * {
        font-size: 13px !important;
    }

</style>
""", unsafe_allow_html=True)

# 🔥 Global page layout + font reducer + margins (USE ON ALL PAGES)
st.markdown("""
<style>

    /* --------------------------------------------- */
    /* PAGE MARGINS + TOP SPACING (your requirement) */
    /* --------------------------------------------- */
    .block-container {
        padding-top: 0.5rem !important;     /* top space */
        padding-left: 2rem !important;    /* left margin */
        padding-right: 2rem !important;   /* right margin */
    }

    /* --------------------------------------------- */
    /* GLOBAL FONT SIZE REDUCER (safe for all pages) */
    /* --------------------------------------------- */
    html, body, [class*="css"] {
        font-size: 14px !important;
    }

    /* Title (st.title) */
    h1 {
        font-size: 28px !important;
        font-weight: 700 !important;
        margin-top: 1rem !important;
        margin-bottom: 0.5rem !important;
    }

    /* Headers (st.header / h2 / h3 etc.) */
    h2 {
        font-size: 20px !important;
        font-weight: 650 !important;
    }
    h3 {
        font-size: 18px !important;
        font-weight: 600 !important;
    }
    h4 {
        font-size: 16px !important;
    }

    /* Tabs */
    .stTabs [data-baseweb="tab"] {
        font-size: 14px !important;
    }

    /* Expander header */
    .streamlit-expanderHeader {
        font-size: 12px !important;
    }

    /* Buttons */
    .stButton > button {
        font-size: 13px !important;
        padding: 4px 10px !important;
    }

    /* Sidebars */
    .sidebar .css-1d391kg, .sidebar .css-1n76uvr {
        font-size: 14px !important;
    }

    /* DataFrames (table fonts) */
    .dataframe td, .dataframe th {
        font-size: 13px !important;
    }

    /* Compact table rows */
    .stDataFrame tbody tr td {
        padding-top: 4px !important;
        padding-bottom: 4px !important;
    }

</style>
""", unsafe_allow_html=True)

st.logo(
    "app/assets/DANONE_LOGO_HORIZONTAL.png",
    size="large",
    icon_image="app/assets/DANONE_LOGO_HORIZONTAL.png",
)


st.markdown(
    """
    <h1 class="page-title">
        SN UX – Table Change Logs (Delta History)
    </h1>
    """,
    unsafe_allow_html=True
)


# ---------------- SSL ----------------
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

# ---------------- ENV ----------------
def is_running_in_databricks():
    return "DATABRICKS_RUNTIME_VERSION" in os.environ

if not is_running_in_databricks():
    load_dotenv()

cfg = Config()

# ---------------- SQL HELPERS ----------------
def run_sql(query: str) -> pd.DataFrame:
    token = st.context.headers.get("X-Forwarded-Access-Token")
    with sql.connect(
        server_hostname=cfg.host,
        http_path=f"/sql/1.0/warehouses/{cfg.warehouse_id}",
        access_token=token if token else None,
        credentials_provider=None if token else (lambda: cfg.authenticate),
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(query)
            try:
                return cur.fetchall_arrow().to_pandas()
            except Exception:
                return pd.DataFrame()

# ---------------- CONFIG ----------------
BASE_TABLE = "onesource_eu_dev_rni.ux_sn_global.ux_sn_conso_master_table_dbapp"
PAGE_SIZE = 10
HIDDEN_OPERATIONS = ["OPTIMIZE"]

# ---------------- SESSION STATE ----------------
defaults = {
    "page": 1,
    "total_pages": 1,
    "total_rows": 0,
    "filter_triggered": False,
    "sort_triggered": False,
    "active_start_date": None,
    "active_end_date": None,
    "active_users": ["ALL"],
    "sort_col": "version",
    "sort_order": "descending",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ======================================================
# ✅ Detect DESCRIBE HISTORY schema (user vs userName)
# ======================================================
probe = run_sql(f"SELECT * FROM (DESCRIBE HISTORY {BASE_TABLE}) LIMIT 1")
if probe.empty:
    st.warning("No history found for this table.")
    st.stop()

cols = set(probe.columns)

TS_COL = "timestamp"
OP_COL = "operation" if "operation" in cols else None

if "user" in cols:
    USER_COL = "user"
elif "userName" in cols:
    USER_COL = "userName"
elif "user_name" in cols:
    USER_COL = "user_name"
else:
    USER_COL = None

# ======================================================
# ✅ SQL helpers
# ======================================================
def sql_escape(s: str) -> str:
    return (s or "").replace("'", "''")

def list_sql(items):
    return ", ".join([f"'{sql_escape(x)}'" for x in items])

def append_condition(where_clause: str, cond: str) -> str:
    if not cond:
        return where_clause
    if not where_clause:
        return "WHERE " + cond
    return where_clause + " AND " + cond

def build_where_clause() -> str:
    wc = ""

    if OP_COL and HIDDEN_OPERATIONS:
        wc = append_condition(wc, f"{OP_COL} NOT IN ({list_sql(HIDDEN_OPERATIONS)})")

    sd = st.session_state.get("active_start_date")
    ed = st.session_state.get("active_end_date")
    if sd and ed:
        wc = append_condition(wc, f"DATE({TS_COL}) BETWEEN DATE('{sd}') AND DATE('{ed}')")

    users = st.session_state.get("active_users", ["ALL"])
    if USER_COL and not (len(users) == 1 and users[0] == "ALL"):
        wc = append_condition(wc, f"{USER_COL} IN ({list_sql(users)})")

    return wc

def build_order_by_clause() -> str:
    col = st.session_state.get("sort_col", "version")
    ord_ = st.session_state.get("sort_order", "descending")
    direction = "ASC" if ord_ == "ascending" else "DESC"

    allowed = {"version", TS_COL}
    if OP_COL:
        allowed.add(OP_COL)
    if USER_COL:
        allowed.add(USER_COL)

    if col not in allowed:
        col = "version"
        direction = "DESC"

    return f"ORDER BY {col} {direction}"

# ======================================================
# ✅ Sidebar metadata (dates + users)
# ======================================================
meta_df = run_sql(f"""
SELECT
  MIN(DATE({TS_COL})) AS min_date,
  MAX(DATE({TS_COL})) AS max_date
FROM (DESCRIBE HISTORY {BASE_TABLE})
WHERE {OP_COL} NOT IN ({list_sql(HIDDEN_OPERATIONS)})
""")

min_d = pd.to_datetime(meta_df.loc[0, "min_date"]).date()
max_d = pd.to_datetime(meta_df.loc[0, "max_date"]).date()

if st.session_state["active_start_date"] is None:
    st.session_state["active_start_date"] = min_d
if st.session_state["active_end_date"] is None:
    st.session_state["active_end_date"] = max_d

users_df = run_sql(f"""
SELECT DISTINCT {USER_COL} AS user_val
FROM (DESCRIBE HISTORY {BASE_TABLE})
WHERE {OP_COL} NOT IN ({list_sql(HIDDEN_OPERATIONS)})
AND {USER_COL} IS NOT NULL
ORDER BY user_val
""")

user_options = ["ALL"] + users_df["user_val"].dropna().tolist()

# ======================================================
# ✅ Sidebar filters (same UX as example)
# ======================================================
def handle_all_logic(key_name):
    def on_change():
        vals = st.session_state[key_name]
        if "ALL" in vals and len(vals) > 1 and vals[0] == "ALL":
            st.session_state[key_name] = [v for v in vals if v != "ALL"]
        elif "ALL" in vals and vals[-1] == "ALL":
            st.session_state[key_name] = ["ALL"]
        elif len(vals) == 0:
            st.session_state[key_name] = ["ALL"]
    return on_change

with st.sidebar:
    st.header("Choose log filters")

    filter_clicked = st.button("Filter logs", type="primary", use_container_width=True)

    st.date_input(
        "Select date range",
        value=(st.session_state["active_start_date"], st.session_state["active_end_date"]),
        min_value=min_d,
        max_value=max_d,
        key="date_filter",
    )

    st.multiselect(
        "Select user",
        options=user_options,
        default=st.session_state.get("active_users", ["ALL"]),
        key="user_filter",
        on_change=handle_all_logic("user_filter"),
    )

    if filter_clicked:
        st.session_state["filter_triggered"] = True
        st.rerun()

# ======================================================
# ✅ Sort form
# ======================================================
sort_columns = ["version", TS_COL]
if USER_COL:
    sort_columns.append(USER_COL)
if OP_COL:
    sort_columns.append(OP_COL)

with st.form("sort_form", clear_on_submit=False):
    sc1, sc2, sc3 = st.columns([2.5, 2.5, 1], vertical_alignment="bottom")

    with sc1:
        sort_col = st.selectbox("Column name", options=sort_columns)
    with sc2:
        sort_ord = st.selectbox("Sort order name", options=["ascending", "descending"])
    with sc3:
        sort_submitted = st.form_submit_button("Sort logs", type="primary", use_container_width=True)

    if sort_submitted:
        st.session_state["sort_col"] = sort_col
        st.session_state["sort_order"] = sort_ord
        st.session_state["sort_triggered"] = True
        st.rerun()

# ======================================================
# ✅ Apply filter / sort
# ======================================================
if st.session_state["filter_triggered"]:
    st.session_state["active_start_date"], st.session_state["active_end_date"] = st.session_state["date_filter"]
    st.session_state["active_users"] = st.session_state.get("user_filter", ["ALL"])
    st.session_state["page"] = 1
    st.session_state["filter_triggered"] = False

if st.session_state["sort_triggered"]:
    st.session_state["page"] = 1
    st.session_state["sort_triggered"] = False

# ======================================================
# ✅ Pagination
# ======================================================
where_clause = build_where_clause()

count_df = run_sql(f"""
SELECT COUNT(*) AS total_rows
FROM (DESCRIBE HISTORY {BASE_TABLE})
{where_clause}
""")

st.session_state["total_rows"] = int(count_df.loc[0, "total_rows"])
st.session_state["total_pages"] = max(1, math.ceil(st.session_state["total_rows"] / PAGE_SIZE))

page = st.session_state["page"]
offset = (page - 1) * PAGE_SIZE
order_by = build_order_by_clause()

page_df = run_sql(f"""
SELECT *
FROM (DESCRIBE HISTORY {BASE_TABLE})
{where_clause}
{order_by}
LIMIT {PAGE_SIZE}
OFFSET {offset}
""")

st.caption(
    f"Total records: **{st.session_state['total_rows']}** | "
    f"Page **{page}** of **{st.session_state['total_pages']}**"
)
st.divider()

# ======================================================
# ✅ Render versions (NO rollback)
# ======================================================
if page_df.empty:
    st.info("No log records found.")
else:
    for _, row in page_df.iterrows():
        version = int(row.get("version"))
        ts = row.get(TS_COL)
        user = row.get(USER_COL, "unknown") if USER_COL else "unknown"
        op = row.get(OP_COL, "") if OP_COL else ""

        with st.expander(f"Version {version} | {user} | {ts}"):
            summary_df = pd.DataFrame([{
                "Version": version,
                "Timestamp": ts,
                "User": user,
                "Operation": op
            }])
            st.dataframe(summary_df, use_container_width=True, hide_index=True, column_config=_bold_col_cfg(summary_df))

st.divider()

# ======================================================
# ✅ Pagination controls
# ======================================================
c1, c2, c3 = st.columns([1, 2, 1])

with c1:
    if st.button("Previous", disabled=page <= 1):
        st.session_state["page"] -= 1
        st.rerun()

with c2:
    st.markdown(
        f"<div style='text-align:center;font-weight:600;'>"
        f"Page {page} of {st.session_state['total_pages']}"
        f"</div>",
        unsafe_allow_html=True
    )

with c3:
    if st.button("Next", disabled=page >= st.session_state["total_pages"]):
        st.session_state["page"] += 1
        st.rerun()