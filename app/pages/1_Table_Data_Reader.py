# ============================================================
# 1_Table_Data_Reader.py  –  Read-Only Table Viewer (Page 1)
# ============================================================
# All constants, CSS, and common helpers imported from
# app/config.py.  Page-specific: Insights View profiling,
# cached query, dynamic schema introspection.
# ============================================================

from __future__ import annotations

import copy
import math
import time
import uuid

import pandas as pd
import streamlit as st

# ── Import from shared config ────────────────────────────────
from config import (
    CATALOG, SCHEMA, MAIN_TABLE, TABLE_FQN, PAGE_SIZE,
    SELECT_COLUMNS, FILTER_COLUMNS, PINNED_COLUMNS,
    SCHEMA_DTYPE, INSIGHTS_MANDATORY_COLUMNS,
    SHARED_CSS, APP_LOGO_IMAGE, APP_LOGO_ICON, APP_LOGO_LINK,
    APP_TITLE,
    cfg, CA_FILE, HOST, HTTP_PATH,
    to_bold,
    get_user_token, sp_connection, run_query,
)
from databricks import sql


# ═════════════════════════════════════════════════════════════
#  PAGE-SPECIFIC: CACHED QUERY (5 min TTL for read-only page)
# ═════════════════════════════════════════════════════════════

@st.cache_data(ttl=300)
def cached_query(query: str, user_token: str) -> pd.DataFrame:
    """Cached read query using user OBO token (TTL = 5 min)."""
    with sql.connect(
        server_hostname=HOST,
        http_path=HTTP_PATH,
        access_token=user_token,
        _tls_no_verify=False,
        _ext_debug_info=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(query)
            return cursor.fetchall_arrow().to_pandas()


# ═════════════════════════════════════════════════════════════
#  PAGE-SPECIFIC: DYNAMIC SCHEMA INTROSPECTION
# ═════════════════════════════════════════════════════════════

def map_pd_dtype(data_type: str):
    dt = (data_type or "").lower()
    if dt in ("int", "integer", "bigint", "smallint", "tinyint"):
        return pd.Int64Dtype()
    if dt in ("float", "double", "real", "decimal", "numeric"):
        return "float64"
    if dt in ("boolean",):
        return "boolean"
    if "timestamp" in dt or dt == "date":
        return "datetime64[ns]"
    return pd.StringDtype()


def fetch_table_schema(catalog: str, schema: str, table: str) -> pd.DataFrame:
    q = f"""
    SELECT column_name, data_type, ordinal_position
    FROM {catalog}.information_schema.columns
    WHERE table_schema = '{schema}' AND table_name = '{table}'
    ORDER BY ordinal_position
    """
    user_token = get_user_token()
    if user_token:
        return cached_query(q, user_token)
    return run_query(q)


def get_dtype_map(catalog: str, schema: str, table: str) -> dict[str, object]:
    sch = fetch_table_schema(catalog, schema, table)
    return {r["column_name"]: map_pd_dtype(r["data_type"]) for _, r in sch.iterrows()}


def build_select_query(table_fqn: str, select_cols: list[str]) -> str:
    cols_sql = ",\n        ".join(select_cols)
    return f"""
    SELECT
        {cols_sql}
    FROM
        {table_fqn}
    ORDER BY
        {select_cols[0]}
    -- cache_buster: {str(uuid.uuid4())}
    """


# ═════════════════════════════════════════════════════════════
#  STREAMLIT PAGE CONFIG & STYLES
# ═════════════════════════════════════════════════════════════

st.set_page_config(
    page_title=APP_TITLE, layout="wide", initial_sidebar_state="expanded",
)

st.markdown(SHARED_CSS, unsafe_allow_html=True)

if APP_LOGO_IMAGE:
    st.logo(
        APP_LOGO_IMAGE, size="large",
        icon_image=APP_LOGO_ICON,
        link=APP_LOGO_LINK,
    )

# Initialize fetch-state gate
if "init_data_fetch" not in st.session_state:
    st.session_state["init_data_fetch"] = False


# ═════════════════════════════════════════════════════════════
#  AUTO-FETCH TABLE ON PAGE LOAD
# ═════════════════════════════════════════════════════════════

if not st.session_state["init_data_fetch"]:
    try:
        dtype_map = get_dtype_map(CATALOG, SCHEMA, MAIN_TABLE)
        all_cols = list(dtype_map.keys())

        select_cols = SELECT_COLUMNS or all_cols
        select_cols = [c for c in select_cols if c in all_cols]
        if not select_cols:
            raise ValueError(
                "DISPLAY_COLUMNS produced no valid columns. Check env."
            )

        sql_query = build_select_query(TABLE_FQN, select_cols)

        with st.spinner(f"Loading {TABLE_FQN} from Databricks..."):
            user_token = get_user_token()
            if user_token:
                data = cached_query(sql_query, user_token)
            else:
                data = run_query(sql_query)

            for c in data.columns:
                if c in dtype_map:
                    try:
                        data[c] = data[c].astype(dtype_map[c])
                    except Exception:
                        pass

            st.session_state["data"] = data
            st.session_state["read_snapshot"] = copy.deepcopy(data)
            st.session_state["sfe_df"] = copy.deepcopy(data)
            st.session_state["page_size"] = PAGE_SIZE
            st.session_state["page"] = 1
            st.session_state["start_index"] = 0
            st.session_state["end_index"] = PAGE_SIZE
            st.session_state["total_pages"] = max(
                1, math.ceil(len(data) / PAGE_SIZE),
            )

        if data.empty:
            st.info("The table loaded successfully but returned **0 rows**.")
        else:
            st.session_state["init_data_fetch"] = True
            st.rerun()

    except Exception as ex:
        st.error(f"Failed to auto-load table: {ex}")


# ═════════════════════════════════════════════════════════════
#  TABS: Insights View + Table View
# ═════════════════════════════════════════════════════════════

tab1, tab2 = st.tabs(["Insights View", "Table View"])


# ── INSIGHTS VIEW ────────────────────────────────────────────
with tab1:
    if "sfe_df" in st.session_state:
        _insight_df = st.session_state["sfe_df"]
        _total_records = len(_insight_df)

        _mandatory_cols = [
            c for c in INSIGHTS_MANDATORY_COLUMNS if c in _insight_df.columns
        ]
        _total_columns = len(_mandatory_cols)

        # Build column profiling dataframe
        _profiling_rows = []
        for _col in _mandatory_cols:
            _null_cnt = int(_insight_df[_col].isna().sum())
            _non_null_cnt = _total_records - _null_cnt
            _null_pct = (
                round(_null_cnt / _total_records * 100, 1)
                if _total_records > 0 else 0.0
            )
            _profiling_rows.append({
                "Column Name": _col,
                "Data Type": str(_insight_df[_col].dtype),
                "Total Records": _total_records,
                "Non-Null Count": _non_null_cnt,
                "Null Count": _null_cnt,
                "Null %": _null_pct,
            })

        _profile_df = (
            pd.DataFrame(_profiling_rows)
            .sort_values("Null %", ascending=False)
            .reset_index(drop=True)
        )

        _fully_populated = int((_profile_df["Null Count"] == 0).sum())
        _cols_with_nulls = _total_columns - _fully_populated

        # Summary KPI metrics
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        with kpi1:
            st.metric("Total Records", f"{_total_records:,}")
        with kpi2:
            st.metric("Mandatory Columns", _total_columns)
        with kpi3:
            st.metric("Fully Populated Columns", _fully_populated)
        with kpi4:
            st.metric("Columns with Nulls", _cols_with_nulls)

        # Profiling table with traffic-light gradient
        st.subheader("Column Profiling Details")

        def _traffic_gradient(val):
            """Smooth RdYlGn_r gradient for Null % column."""
            try:
                v = float(val)
            except (ValueError, TypeError):
                return ""
            v = max(0.0, min(100.0, v))
            ratio = v / 100.0
            if ratio <= 0.5:
                t = ratio / 0.5
                r = int(26 + (255 - 26) * t)
                g = int(152 + (255 - 152) * t)
                b = int(80 + (191 - 80) * t)
            else:
                t = (ratio - 0.5) / 0.5
                r = int(255 + (215 - 255) * t)
                g = int(255 + (48 - 255) * t)
                b = int(191 + (39 - 191) * t)
            text_color = "#ffffff" if ratio < 0.2 or ratio > 0.7 else "#1a1a2e"
            return f"background-color: rgb({r},{g},{b}); color: {text_color}"

        _bold_col_map = {col: to_bold(col) for col in _profile_df.columns}
        _profile_display = _profile_df.rename(columns=_bold_col_map)
        _bold_null_pct = _bold_col_map["Null %"]

        _styled = (
            _profile_display.style
            .map(_traffic_gradient, subset=[_bold_null_pct])
            .format({_bold_null_pct: "{:.1f}"})
            .set_table_styles([
                {"selector": "th", "props": [
                    ("font-weight", "bold"),
                    ("text-align", "center"),
                ]},
            ])
            .set_properties(**{"text-align": "center"})
        )
        st.dataframe(
            _styled, use_container_width=True, hide_index=True, height=400,
        )
    else:
        st.info(
            "Loading table data from Databricks Unity Catalog..."
        )


# ── TABLE VIEW ───────────────────────────────────────────────
with tab2:
    if "read_snapshot" in st.session_state and st.session_state["init_data_fetch"]:

        if "sfe_df" not in st.session_state:
            st.session_state["sfe_df"] = copy.deepcopy(
                st.session_state["read_snapshot"]
            )

        # Filter columns
        if "filter_col_list" not in st.session_state:
            if FILTER_COLUMNS:
                st.session_state["filter_col_list"] = [
                    c for c in FILTER_COLUMNS
                    if c in st.session_state["read_snapshot"].columns
                ]
            else:
                st.session_state["filter_col_list"] = list(
                    st.session_state["read_snapshot"].columns[:3]
                )
            # Always include sp_test_id filter if present
            if (
                "sp_test_id" in st.session_state["read_snapshot"].columns
                and "sp_test_id" not in st.session_state["filter_col_list"]
            ):
                st.session_state["filter_col_list"].insert(0, "sp_test_id")

        # Sidebar filter form
        @st.fragment
        def sidebar_filter_form():
            def handle_filter_change(col_name):
                def filter_on_change():
                    current_selection = st.session_state[col_name + "_filter"]
                    if (
                        "ALL" in current_selection
                        and len(current_selection) > 1
                        and "ALL" == current_selection[0]
                    ):
                        st.session_state[col_name + "_filter"] = [
                            opt for opt in current_selection if opt != "ALL"
                        ]
                    elif (
                        ("ALL" in current_selection and "ALL" == current_selection[-1])
                        or len(current_selection) == 0
                    ):
                        st.session_state[col_name + "_filter"] = ["ALL"]
                    else:
                        st.session_state[col_name + "_filter"] = current_selection
                return filter_on_change

            st.header("Choose table filters")
            filter_clicked = st.button(
                "Filter table", type="primary", use_container_width=True,
            )

            for col in st.session_state["filter_col_list"]:
                options = (
                    ["ALL"]
                    + st.session_state["read_snapshot"][col]
                    .dropna().astype(str).unique().tolist()
                )
                st.multiselect(
                    label=f"Select the {col}:",
                    options=options,
                    key=f"{col}_filter",
                    default="ALL",
                    on_change=handle_filter_change(col),
                )

            if filter_clicked:
                st.session_state["filter_triggered"] = True
                st.rerun()

        with st.sidebar:
            sidebar_filter_form()

        rc1, rc2 = st.columns([4, 1], vertical_alignment="bottom")
        with rc1:
            st.markdown(
                "<h2 style='font-size:28px; font-weight:700; margin-bottom:5px;'>"
                "[Read] SN UX CONSO Master Table</h2>",
                unsafe_allow_html=True,
            )
        with rc2:
            if st.button("Edit table", type="primary"):
                st.switch_page("pages/2_Table_Data_Editor.py")

        # Sort Form
        with st.form("sort_form", clear_on_submit=False):
            sc1, sc2, sc3 = st.columns([2, 2, 1], vertical_alignment="bottom")
            with sc1:
                sort_col = st.selectbox(
                    "Column name",
                    options=st.session_state["read_snapshot"].columns,
                )
            with sc2:
                sort_ord = st.selectbox(
                    "Sort order name",
                    options=["ascending", "descending"],
                )
            with sc3:
                sort_submitted = st.form_submit_button(
                    "Sort table", type="primary",
                )

        # Apply filters
        if st.session_state.get("filter_triggered"):
            sfe_df_cpy = copy.deepcopy(st.session_state["read_snapshot"])
            all_filter_all = True

            first_col = st.session_state["filter_col_list"][0]
            first_key = first_col + "_filter"
            if not (
                len(st.session_state[first_key]) == 1
                and "ALL" in st.session_state[first_key]
            ):
                st.session_state["sfe_df"] = sfe_df_cpy[
                    sfe_df_cpy[first_col].astype(str).isin(
                        [str(x) for x in st.session_state[first_key]]
                    )
                ]
                all_filter_all = False
            else:
                st.session_state["sfe_df"] = sfe_df_cpy

            for filter_col in st.session_state["filter_col_list"][1:]:
                sel = st.session_state[filter_col + "_filter"]
                if not (len(sel) == 1 and "ALL" in sel):
                    st.session_state["sfe_df"] = st.session_state["sfe_df"][
                        st.session_state["sfe_df"][filter_col]
                        .astype(str).isin([str(x) for x in sel])
                    ]
                    all_filter_all = False

            if all_filter_all:
                st.session_state["sfe_df"] = sfe_df_cpy
            else:
                st.session_state["sfe_df"] = st.session_state["sfe_df"].reset_index(
                    drop=True
                )

            st.session_state["page"] = 1
            st.session_state["start_index"] = 0
            st.session_state["end_index"] = st.session_state["page_size"]
            st.session_state["total_pages"] = max(
                1,
                math.ceil(
                    len(st.session_state["sfe_df"]) / st.session_state["page_size"]
                ),
            )
            st.session_state["filter_triggered"] = False

        # Sort
        if sort_submitted:
            st.session_state["sfe_df"] = (
                st.session_state["sfe_df"]
                .sort_values(
                    by=sort_col,
                    ascending=(sort_ord == "ascending"),
                )
                .reset_index(drop=True)
            )
            st.session_state["page"] = 1
            st.session_state["start_index"] = 0
            st.session_state["end_index"] = st.session_state["page_size"]

        # Dataframe display
        pinned = PINNED_COLUMNS or []
        column_config = {}

        for c in pinned:
            if c in st.session_state["sfe_df"].columns:
                if pd.api.types.is_datetime64_any_dtype(st.session_state["sfe_df"][c]):
                    column_config[c] = st.column_config.DatetimeColumn(
                        label=to_bold(c), format="localized",
                        disabled=True, pinned=True,
                    )
                elif pd.api.types.is_numeric_dtype(st.session_state["sfe_df"][c]):
                    column_config[c] = st.column_config.NumberColumn(
                        label=to_bold(c), disabled=True, pinned=True,
                    )
                else:
                    column_config[c] = st.column_config.TextColumn(
                        label=to_bold(c), disabled=True, pinned=True,
                    )

        for c in st.session_state["sfe_df"].columns:
            if c not in column_config:
                column_config[c] = st.column_config.Column(label=to_bold(c))

        st.dataframe(
            data=st.session_state["sfe_df"][
                st.session_state["start_index"]:st.session_state["end_index"]
            ],
            height=250,
            use_container_width=True,
            hide_index=True,
            column_config=column_config,
        )

        # Pagination
        col1, col2, col3 = st.columns([1, 2, 1])

        with col1:
            left, mid, right = st.columns([1, 2, 1])
            with mid:
                prev_clicked = st.button(
                    "Previous", use_container_width=True,
                    disabled=st.session_state["page"] <= 1,
                    key="prev_btn",
                )
                if prev_clicked and st.session_state["page"] > 1:
                    st.session_state["page"] -= 1
                    st.session_state["start_index"] = (
                        (st.session_state["page"] - 1) * st.session_state["page_size"]
                    )
                    st.session_state["end_index"] = (
                        st.session_state["start_index"] + st.session_state["page_size"]
                    )
                    st.rerun()

        with col2:
            st.markdown(
                f"<div style='text-align:center; font-weight:600; margin:0;'>"
                f"Page {st.session_state['page']} of {st.session_state['total_pages']}"
                f"</div>",
                unsafe_allow_html=True,
            )

        with col3:
            left, mid, right = st.columns([1, 2, 1])
            with mid:
                next_clicked = st.button(
                    "Next", use_container_width=True,
                    disabled=st.session_state["page"] >= st.session_state["total_pages"],
                    key="next_btn",
                )
                if next_clicked and st.session_state["page"] < st.session_state["total_pages"]:
                    st.session_state["page"] += 1
                    st.session_state["start_index"] = (
                        (st.session_state["page"] - 1) * st.session_state["page_size"]
                    )
                    st.session_state["end_index"] = (
                        st.session_state["start_index"] + st.session_state["page_size"]
                    )
                    st.rerun()

    else:
        st.info(
            "Loading table data from Databricks Unity Catalog..."
        )
