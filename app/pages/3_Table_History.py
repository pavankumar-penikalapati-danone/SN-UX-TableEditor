# ============================================================
# 3_Table_History.py  –  Change Logs + Delta Diff (merged)
# ============================================================
# Tab 1: Change Logs – DESCRIBE HISTORY with filters & pagination
# Tab 2: Delta Diff  – Column-level diff between two versions
# ============================================================

import os
import re
import math
import certifi
import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config
from dotenv import load_dotenv
from datetime import date
from config import SHARED_CSS, APP_LOGO_IMAGE, APP_LOGO_ICON, APP_LOGO_LINK


# ── Shared helpers ───────────────────────────────────────────
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


# ═════════════════════════════════════════════════════════════
#  PAGE CONFIG & STYLES
# ═════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="SN UX – Table History",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(SHARED_CSS, unsafe_allow_html=True)

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
_approver_emails = [e.strip().lower() for e in os.environ.get("TEST_STATUS_APPROVERS", "").split(",") if e.strip()]
_hide_css = []
if _admin_users_env and _nav_user not in _admin_users_env:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"] { display: none !important; }')
if _nav_user not in _approver_emails:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Approval_Dashboard"] { display: none !important; }')
if _hide_css:
    st.markdown(f'<style>{"".join(_hide_css)}</style>', unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════
#  SSL / ENV / CONFIG
# ═════════════════════════════════════════════════════════════
os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

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
            try:
                return cur.fetchall_arrow().to_pandas()
            except Exception:
                return pd.DataFrame()


# ═════════════════════════════════════════════════════════════
#  SHARED CONSTANTS
# ═════════════════════════════════════════════════════════════
BASE_TABLE = "onesource_eu_dev_rni.ux_sn_global.ux_sn_conso_master_table_dbapp_clone"
HIDDEN_OPERATIONS = ["OPTIMIZE"]
LOGS_PAGE_SIZE = 10


# ═════════════════════════════════════════════════════════════
#  TABS
# ═════════════════════════════════════════════════════════════
tab_logs, tab_diff = st.tabs(["Change Logs", "Delta Diff"])


# ─────────────────────────────────────────────────────────────
#  TAB 1: CHANGE LOGS
# ─────────────────────────────────────────────────────────────
with tab_logs:

    # ── Session state defaults ─────────────────────────────────
    _log_defaults = {
        "log_page": 1,
        "log_total_pages": 1,
        "log_total_rows": 0,
        "log_filter_triggered": False,
        "log_sort_triggered": False,
        "log_start_date": None,
        "log_end_date": None,
        "log_users": ["ALL"],
        "log_sort_col": "version",
        "log_sort_order": "descending",
    }
    for k, v in _log_defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # ── Detect DESCRIBE HISTORY schema ─────────────────────────
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

    # ── SQL helpers ────────────────────────────────────────────
    def sql_escape(s: str) -> str:
        return (s or "").replace("'", "''")

    def list_sql(items):
        return ", ".join([f"'{sql_escape(x)}'" for x in items])

    def _log_build_where() -> str:
        wc = ""
        if OP_COL and HIDDEN_OPERATIONS:
            wc = f"WHERE {OP_COL} NOT IN ({list_sql(HIDDEN_OPERATIONS)})"

        sd = st.session_state.get("log_start_date")
        ed = st.session_state.get("log_end_date")
        if sd and ed:
            cond = f"DATE({TS_COL}) BETWEEN DATE('{sd}') AND DATE('{ed}')"
            wc = (wc + " AND " + cond) if wc else ("WHERE " + cond)

        users = st.session_state.get("log_users", ["ALL"])
        if USER_COL and not (len(users) == 1 and users[0] == "ALL"):
            cond = f"{USER_COL} IN ({list_sql(users)})"
            wc = (wc + " AND " + cond) if wc else ("WHERE " + cond)
        return wc

    def _log_build_order() -> str:
        col = st.session_state.get("log_sort_col", "version")
        direction = "ASC" if st.session_state.get("log_sort_order", "descending") == "ascending" else "DESC"
        allowed = {"version", TS_COL}
        if OP_COL:
            allowed.add(OP_COL)
        if USER_COL:
            allowed.add(USER_COL)
        if col not in allowed:
            col, direction = "version", "DESC"
        return f"ORDER BY {col} {direction}"

    # ── Sidebar: log filters ──────────────────────────────────
    meta_df = run_sql(f"""
    SELECT MIN(DATE({TS_COL})) AS min_date, MAX(DATE({TS_COL})) AS max_date
    FROM (DESCRIBE HISTORY {BASE_TABLE})
    WHERE {OP_COL} NOT IN ({list_sql(HIDDEN_OPERATIONS)})
    """)
    min_d = pd.to_datetime(meta_df.loc[0, "min_date"]).date()
    max_d = pd.to_datetime(meta_df.loc[0, "max_date"]).date()

    if st.session_state["log_start_date"] is None:
        st.session_state["log_start_date"] = min_d
    if st.session_state["log_end_date"] is None:
        st.session_state["log_end_date"] = max_d

    users_df = run_sql(f"""
    SELECT DISTINCT {USER_COL} AS user_val
    FROM (DESCRIBE HISTORY {BASE_TABLE})
    WHERE {OP_COL} NOT IN ({list_sql(HIDDEN_OPERATIONS)}) AND {USER_COL} IS NOT NULL
    ORDER BY user_val
    """)
    user_options = ["ALL"] + users_df["user_val"].dropna().tolist()

    def _log_all_toggle(key_name):
        def on_change():
            vals = st.session_state[key_name]
            if "ALL" in vals and len(vals) > 1 and vals[0] == "ALL":
                st.session_state[key_name] = [v for v in vals if v != "ALL"]
            elif ("ALL" in vals and vals[-1] == "ALL") or len(vals) == 0:
                st.session_state[key_name] = ["ALL"]
        return on_change

    with st.sidebar:
        st.header("Log Filters")
        log_filter_btn = st.button("Filter logs", type="primary", use_container_width=True)
        st.date_input(
            "Select date range",
            value=(st.session_state["log_start_date"], st.session_state["log_end_date"]),
            min_value=min_d, max_value=max_d, key="log_date_filter",
        )
        st.multiselect(
            "Select user", options=user_options,
            default=st.session_state.get("log_users", ["ALL"]),
            key="log_user_filter",
            on_change=_log_all_toggle("log_user_filter"),
        )
        if log_filter_btn:
            st.session_state["log_filter_triggered"] = True
            st.rerun()

    # ── Sort form ──────────────────────────────────────────────
    sort_columns = ["version", TS_COL]
    if USER_COL:
        sort_columns.append(USER_COL)
    if OP_COL:
        sort_columns.append(OP_COL)

    with st.form("log_sort_form", clear_on_submit=False):
        sc1, sc2, sc3 = st.columns([2.5, 2.5, 1], vertical_alignment="bottom")
        with sc1:
            sort_col = st.selectbox("Column name", options=sort_columns)
        with sc2:
            sort_ord = st.selectbox("Sort order", options=["ascending", "descending"])
        with sc3:
            sort_submitted = st.form_submit_button("Sort logs", type="primary", use_container_width=True)
        if sort_submitted:
            st.session_state["log_sort_col"] = sort_col
            st.session_state["log_sort_order"] = sort_ord
            st.session_state["log_sort_triggered"] = True
            st.rerun()

    # ── Apply filters / sort ───────────────────────────────────
    if st.session_state["log_filter_triggered"]:
        st.session_state["log_start_date"], st.session_state["log_end_date"] = st.session_state["log_date_filter"]
        st.session_state["log_users"] = st.session_state.get("log_user_filter", ["ALL"])
        st.session_state["log_page"] = 1
        st.session_state["log_filter_triggered"] = False

    if st.session_state["log_sort_triggered"]:
        st.session_state["log_page"] = 1
        st.session_state["log_sort_triggered"] = False

    # ── Pagination query ───────────────────────────────────────
    where_clause = _log_build_where()
    count_df = run_sql(f"SELECT COUNT(*) AS total_rows FROM (DESCRIBE HISTORY {BASE_TABLE}) {where_clause}")
    st.session_state["log_total_rows"] = int(count_df.loc[0, "total_rows"])
    st.session_state["log_total_pages"] = max(1, math.ceil(st.session_state["log_total_rows"] / LOGS_PAGE_SIZE))

    _log_page = st.session_state["log_page"]
    offset = (_log_page - 1) * LOGS_PAGE_SIZE
    order_by = _log_build_order()

    page_df = run_sql(f"""
    SELECT * FROM (DESCRIBE HISTORY {BASE_TABLE})
    {where_clause} {order_by}
    LIMIT {LOGS_PAGE_SIZE} OFFSET {offset}
    """)

    st.caption(
        f"Total records: **{st.session_state['log_total_rows']}** | "
        f"Page **{_log_page}** of **{st.session_state['log_total_pages']}**"
    )
    st.divider()

    # ── Render version rows ────────────────────────────────────
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
                    "Version": version, "Timestamp": ts,
                    "User": user, "Operation": op,
                }])
                st.dataframe(summary_df, use_container_width=True, hide_index=True,
                             column_config=_bold_col_cfg(summary_df))

    st.divider()

    # ── Pagination controls ────────────────────────────────────
    _tp = st.session_state["log_total_pages"]
    _tr = st.session_state["log_total_rows"]
    pg_prev, pg_lbl, pg_input, pg_of, pg_next = st.columns(
        [1, 0.5, 0.6, 2, 1], vertical_alignment="center",
    )
    with pg_prev:
        if st.button("Previous", use_container_width=True, disabled=_log_page <= 1, key="log_prev"):
            st.session_state["log_page"] -= 1
            st.rerun()
    with pg_lbl:
        st.markdown(
            "<div style='text-align:right; font-weight:600; white-space:nowrap;'>Page</div>",
            unsafe_allow_html=True,
        )
    with pg_input:
        _go = st.number_input(
            "Go to page", min_value=1, max_value=_tp,
            value=_log_page, step=1, key="log_go_page",
            label_visibility="collapsed",
        )
        if _go != _log_page:
            st.session_state["log_page"] = _go
            st.rerun()
    with pg_of:
        st.markdown(
            f"<div style='font-weight:600; white-space:nowrap;'>"
            f"of {_tp} &nbsp;|&nbsp; {_tr:,} rows</div>",
            unsafe_allow_html=True,
        )
    with pg_next:
        if st.button("Next", use_container_width=True, disabled=_log_page >= _tp, key="log_next"):
            st.session_state["log_page"] += 1
            st.rerun()


# ─────────────────────────────────────────────────────────────
#  TAB 2: DELTA DIFF
# ─────────────────────────────────────────────────────────────
with tab_diff:

    # ── Validation helpers ─────────────────────────────────────
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

    DEFAULT_PK = "row_id"

    # ── Sidebar settings ───────────────────────────────────────
    with st.sidebar:
        st.divider()
        st.header("Delta Diff Settings")
        diff_run_btn = st.button("Compute Diff", type="primary", use_container_width=True, key="diff_run")
        diff_table = st.text_input("Table (catalog.schema.table)", value=BASE_TABLE, key="diff_table")
        diff_pk = st.text_input("Primary key column", value=DEFAULT_PK, key="diff_pk")
        diff_limit = st.number_input("Versions to load", 50, 5000, 500, 50, key="diff_limit")
        diff_max = st.number_input("Max diff rows", 100, 50000, 5000, 100, key="diff_max")

    t = q_fqn(diff_table)
    pk = q_ident(diff_pk)
    hide_ops_sql = ", ".join([f"\'{op}\'" for op in HIDDEN_OPERATIONS])

    # ── Load history ───────────────────────────────────────────
    try:
        history_df = run_sql(f"""
        SELECT * FROM (DESCRIBE HISTORY {t})
        WHERE operation NOT IN ({hide_ops_sql})
        ORDER BY version DESC LIMIT {diff_limit}
        """)
    except Exception as _ex:
        st.error(f"Failed to load table history: {_ex}")
        st.stop()

    if history_df.empty:
        st.warning("No history found.")
        st.stop()

    versions = history_df["version"].astype(int).tolist()
    selected_version = st.selectbox("Current Version", versions, index=0, key="diff_version")

    pos = list(history_df["version"]).index(selected_version)
    if pos == len(history_df) - 1:
        st.info("Oldest version loaded — no previous version available.")
        st.stop()

    prev_version = int(history_df.loc[pos + 1, "version"])
    st.info(f"Comparing **prev = {prev_version} → curr = {selected_version}**")

    # ── Load schema ────────────────────────────────────────────
    try:
        desc_df = run_sql(f"DESCRIBE {t}")
        all_cols = [c.strip() for c in desc_df["col_name"].tolist()
                    if c and not str(c).startswith("#")]
    except Exception as _ex:
        st.error(f"Failed to load table schema: {_ex}")
        st.stop()

    diff_cols = [c for c in all_cols if c != diff_pk]
    st.write(f"Detected **{len(diff_cols)}** non-PK columns.")

    # ── Build SQL ──────────────────────────────────────────────
    stack_items = []
    for c in diff_cols:
        stack_items.extend([f"\'{c}\'", f"CAST(prev.`{c}` AS STRING)", f"CAST(curr.`{c}` AS STRING)"])
    stack_expr = f"stack({len(diff_cols)}, {', '.join(stack_items)}) s as column_name, old_value, new_value"

    insert_col_str = ", ".join([f"curr.`{c}`" for c in all_cols if c != diff_pk])
    delete_col_str = ", ".join([f"prev.`{c}`" for c in all_cols if c != diff_pk])

    update_sql = f"""
    SELECT curr.{pk} AS key_id, 'UPDATE' AS change_type, column_name, old_value, new_value
    FROM {t} VERSION AS OF {selected_version} curr
    JOIN {t} VERSION AS OF {prev_version} prev ON curr.{pk} = prev.{pk}
    LATERAL VIEW {stack_expr}
    WHERE old_value IS DISTINCT FROM new_value
    LIMIT {diff_max}
    """

    insert_sql = f"""
    SELECT curr.{pk} AS key_id, 'INSERT' AS change_type, {insert_col_str}
    FROM {t} VERSION AS OF {selected_version} curr
    LEFT JOIN {t} VERSION AS OF {prev_version} prev ON curr.{pk} = prev.{pk}
    WHERE prev.{pk} IS NULL LIMIT {diff_max}
    """

    delete_sql = f"""
    SELECT prev.{pk} AS key_id, 'DELETE' AS change_type, {delete_col_str}
    FROM {t} VERSION AS OF {prev_version} prev
    LEFT JOIN {t} VERSION AS OF {selected_version} curr ON prev.{pk} = curr.{pk}
    WHERE curr.{pk} IS NULL LIMIT {diff_max}
    """

    # ── Run diff ───────────────────────────────────────────────
    _should_compute = diff_run_btn or "delta_diff_computed" not in st.session_state

    if _should_compute:
        try:
            with st.spinner("Computing diffs..."):
                updated_df = run_sql(update_sql)
                inserted_df = run_sql(insert_sql)
                deleted_df = run_sql(delete_sql)
                for df in [inserted_df, deleted_df]:
                    df["column_name"] = ""
                    df["old_value"] = ""
                    df["new_value"] = ""
                final_df = pd.concat([updated_df, inserted_df, deleted_df], ignore_index=True)
                st.session_state["delta_updated_df"] = updated_df
                st.session_state["delta_inserted_df"] = inserted_df
                st.session_state["delta_deleted_df"] = deleted_df
                st.session_state["delta_final_df"] = final_df
                st.session_state["delta_diff_computed"] = True
        except Exception as _ex:
            st.error(f"Failed to compute diff: {_ex}")
            st.stop()

    if st.session_state.get("delta_diff_computed"):
        updated_df = st.session_state["delta_updated_df"]
        inserted_df = st.session_state["delta_inserted_df"]
        deleted_df = st.session_state["delta_deleted_df"]
        final_df = st.session_state["delta_final_df"]

        # ── Summary ────────────────────────────────────────────
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

        # ── Detail tabs ────────────────────────────────────────
        st.markdown("## Change Details")
        dt1, dt2, dt3, dt4 = st.tabs(["Updates", "Inserts", "Deletes", "All Changes"])

        with dt1:
            if updated_df.empty:
                st.info("No updated rows found.")
            else:
                _u = updated_df[["key_id", "change_type", "column_name", "old_value", "new_value"]]
                st.dataframe(_u, use_container_width=True, hide_index=True, column_config=_bold_col_cfg(_u))

        with dt2:
            if inserted_df.empty:
                st.info("No inserted rows found.")
            else:
                _ic = [c for c in inserted_df.columns if c not in ["column_name", "old_value", "new_value"]]
                st.dataframe(inserted_df[_ic], use_container_width=True, hide_index=True, column_config=_bold_col_cfg(inserted_df[_ic]))

        with dt3:
            if deleted_df.empty:
                st.info("No deleted rows found.")
            else:
                _dc = [c for c in deleted_df.columns if c not in ["column_name", "old_value", "new_value"]]
                st.dataframe(deleted_df[_dc], use_container_width=True, hide_index=True, column_config=_bold_col_cfg(deleted_df[_dc]))

        with dt4:
            st.dataframe(final_df, use_container_width=True, hide_index=True, column_config=_bold_col_cfg(final_df))

        # ── Download ───────────────────────────────────────────
        st.download_button(
            "Download CSV", final_df.to_csv(index=False),
            file_name=f"delta_diff_{prev_version}_to_{selected_version}.csv",
            mime="text/csv",
        )

        with st.expander("Show SQL Queries"):
            st.code(update_sql, "sql")
            st.code(insert_sql, "sql")
            st.code(delete_sql, "sql")
