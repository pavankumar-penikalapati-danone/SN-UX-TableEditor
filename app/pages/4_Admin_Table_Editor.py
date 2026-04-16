# ============================================================
# 5_Admin_Table_Editor.py  –  Admin-Only Table Editor (Page 5)
# ============================================================
# Access restricted to users listed in ADMIN_USERS env var.
# All constants, helpers, CSS, and business logic imported from
# app/config.py.  This file contains ONLY Streamlit UI code.
# ============================================================

from __future__ import annotations

import math
import os
import time
import uuid
from typing import Any

import pandas as pd
import streamlit as st

# ── Import everything from shared config ─────────────────────
from config import (
    CATALOG, SCHEMA, TABLE_FQN, PAGE_SIZE,
    SELECT_COLUMNS, DML_COLUMNS, SCHEMA_DTYPE, UPLOAD_DTYPE,
    DROPDOWN_COLS, FILTER_COLUMNS, EXCEL_TEMPLATE,
    MANDATORY_UPDATE_COLS, SEQ_GROUP_COLS,
    ADMIN_USERS, ADMIN_COLUMN_ACCESS, ADMIN_PAGE_SIZE,
    ADMIN_TABLES, MAPPING_TABLE, DROPDOWN_TABLE,
    SHARED_CSS, APP_LOGO_IMAGE, APP_LOGO_ICON, APP_LOGO_LINK,
    to_bold, is_na, validate_mandatory_cols,
    get_user_token, get_connection, run_query, run_statement,
    get_user_identity, ensure_session_id,
    compute_seq, generate_test_id,
    create_audit_table, write_audit_events, build_audit_events,
    build_column_config, dropdown_options,
    bulk_insert, bulk_delete, bulk_update,
    admin_get_visible_columns,
    build_composite_where, build_composite_set,
    sp_connection,
)

# Columns kept in data for DML but hidden from display and filters
_HIDDEN_DISPLAY_COLS = {"ingestion_timestamp", "Unnamed__64", "Unnamed__65", "Unnamed__66", "Unnamed__71", "Unnamed__72", "Unnamed__73"}


# ═════════════════════════════════════════════════════════════
#  PAGE-SPECIFIC: DISCARD CHANGES
# ═════════════════════════════════════════════════════════════

def _admin_discard_all():
    try:
        if "admin_data" not in st.session_state:
            st.warning("No data loaded.")
            return
        st.session_state["admin_sfe_df"] = st.session_state["admin_data"].copy()
        st.session_state["admin_key_counter"] = st.session_state.get("admin_key_counter", 0) + 1
        if "admin_page" in st.session_state:
            start = (st.session_state["admin_page"] - 1) * ADMIN_PAGE_SIZE
            st.session_state["admin_start_index"] = start
            st.session_state["admin_end_index"] = start + ADMIN_PAGE_SIZE
        st.session_state["admin_total_pages"] = max(
            1, math.ceil(len(st.session_state["admin_sfe_df"]) / ADMIN_PAGE_SIZE),
        )
        st.toast("All changes discarded", icon="\u267b\ufe0f")
        st.rerun()
    except Exception as ex:
        st.error(f"Failed to discard changes: {ex}")


if "admin_key_counter" not in st.session_state:
    st.session_state["admin_key_counter"] = 0


# ═════════════════════════════════════════════════════════════
#  STREAMLIT PAGE CONFIG & STYLES
# ═════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Admin Table Editor",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(SHARED_CSS, unsafe_allow_html=True)

st.logo(
    APP_LOGO_IMAGE, size="large",
    icon_image=APP_LOGO_ICON,
    link=APP_LOGO_LINK,
)


# ═════════════════════════════════════════════════════════════
#  ACCESS CONTROL
# ═════════════════════════════════════════════════════════════

try:
    current_user = get_user_identity()
except Exception as _ex:
    st.error(f"Failed to identify current user: {_ex}")
    current_user = "unknown"
current_user_lower = current_user.lower()

if ADMIN_USERS and current_user_lower not in ADMIN_USERS:
    st.error("\u26d4 Access Denied")
    st.warning(
        f"You (**{current_user}**) are not authorized to access this page. "
        "Contact an administrator to request access."
    )
    st.stop()

# ── Hide Admin page from sidebar for non-admin users ─────────
_approver_emails = [e.strip().lower() for e in os.environ.get("TEST_STATUS_APPROVERS", "").split(",") if e.strip()]
_hide_css = []
if ADMIN_USERS and current_user_lower not in ADMIN_USERS:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"] { display: none !important; }')
if current_user_lower not in _approver_emails:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Approval_Dashboard"] { display: none !important; }')
if _hide_css:
    st.markdown(f'<style>{"".join(_hide_css)}</style>', unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════
#  SIDEBAR – TABLE SELECTOR & FILTERS
# ═════════════════════════════════════════════════════════════

with st.sidebar:
    st.header("Admin Settings")
    st.caption(f"User: **{current_user}**")

    selected_table_label = st.selectbox(
        "Select Table:",
        options=list(ADMIN_TABLES.keys()),
        key="admin_table_selector",
    )
    selected_table_fqn = ADMIN_TABLES[selected_table_label]
    st.code(selected_table_fqn, language=None)
    fetch_btn = st.button(
        "Fetch Table", type="primary", use_container_width=True,
    )

    # Dynamic column filters (shown after data is loaded)
    if "admin_raw_data" in st.session_state:
        st.divider()
        st.header("Filters")

        def _admin_filter_toggle(col_key):
            """ALL / specific-value mutual exclusion."""
            def _on_change():
                sel = st.session_state[col_key]
                if "ALL" in sel and len(sel) > 1 and sel[0] == "ALL":
                    st.session_state[col_key] = [v for v in sel if v != "ALL"]
                elif ("ALL" in sel and sel[-1] == "ALL") or len(sel) == 0:
                    st.session_state[col_key] = ["ALL"]
            return _on_change

        _raw_df = st.session_state["admin_raw_data"]

        selected_filter_cols = st.multiselect(
            "Filter by columns:",
            options=[c for c in _raw_df.columns if c not in _HIDDEN_DISPLAY_COLS],
            default=[],
            key="admin_filter_cols",
        )

        admin_filters: dict[str, list] = {}
        for col in selected_filter_cols:
            raw_vals = _raw_df[col].dropna().unique()
            if pd.api.types.is_numeric_dtype(_raw_df[col]):
                unique_vals = [str(v) for v in sorted(raw_vals)]
            else:
                unique_vals = sorted([str(v) for v in raw_vals])
            selected_vals = st.multiselect(
                f"Select {col}:",
                options=["ALL"] + unique_vals,
                default=["ALL"],
                key=f"admin_flt_{col}",
                on_change=_admin_filter_toggle(f"admin_flt_{col}"),
            )
            if selected_vals and "ALL" not in selected_vals:
                admin_filters[col] = selected_vals

        filter_btn = st.button(
            "Apply Filters", type="primary",
            use_container_width=True, key="admin_apply_filters",
        )

        if filter_btn:
            st.session_state["admin_active_filters"] = admin_filters
            st.session_state["admin_page"] = 1
            st.rerun()

        if st.session_state.get("admin_active_filters"):
            if st.button(
                "Clear Filters", use_container_width=True,
                key="admin_clear_filters",
            ):
                st.session_state["admin_active_filters"] = {}
                st.session_state["admin_data"] = st.session_state["admin_raw_data"].copy()
                if selected_table_label == "Master Table":
                    st.session_state["admin_sfe_df"] = st.session_state["admin_raw_data"].copy()
                st.session_state["admin_page"] = 1
                st.session_state["admin_total_pages"] = max(
                    1, math.ceil(len(st.session_state["admin_raw_data"]) / ADMIN_PAGE_SIZE),
                )
                st.rerun()

IS_MASTER = selected_table_label == "Master Table"


# ═════════════════════════════════════════════════════════════
#  DETECT TABLE SWITCH → CLEAR STATE
# ═════════════════════════════════════════════════════════════

if "admin_prev_table" not in st.session_state:
    st.session_state["admin_prev_table"] = selected_table_fqn
elif st.session_state["admin_prev_table"] != selected_table_fqn:
    for k in list(st.session_state.keys()):
        if k.startswith("admin_") and k != "admin_table_selector":
            del st.session_state[k]
    st.session_state["admin_prev_table"] = selected_table_fqn


# ═════════════════════════════════════════════════════════════
#  FETCH TABLE DATA
# ═════════════════════════════════════════════════════════════

def _admin_fetch_table(table_fqn: str, token: str | None) -> pd.DataFrame:
    try:
        if IS_MASTER:
            query = (
                f"SELECT * "
                f"FROM {table_fqn} ORDER BY row_id "
                f"-- cache_buster: {uuid.uuid4()}"
            )
        else:
            query = (
                f"SELECT * FROM {table_fqn} ORDER BY 1 "
                f"-- cache_buster: {uuid.uuid4()}"
            )
        return run_query(query, token)
    except Exception as ex:
        st.error(f"Query execution failed for {table_fqn}: {ex}")
        raise


# ── Title bar ────────────────────────────────────────────────
rc1, rc2, rc3 = st.columns([4, 1, 1], vertical_alignment="bottom")
with rc1:
    st.markdown(
        f"<h2 style='font-size:28px; font-weight:700; margin-bottom:5px;'>"
        f"\U0001f512 [ADMIN] {selected_table_label}</h2>",
        unsafe_allow_html=True,
    )
with rc2:
    save_btn = st.button(
        "Save Changes", type="primary", key="admin_save_btn",
        use_container_width=True,
    )
with rc3:
    if IS_MASTER:
        bulk_btn = st.button(
            "Bulk INSERT", type="primary", key="admin_bulk_btn",
            use_container_width=True,
        )
    else:
        bulk_btn = False

# ── Fetch on button click ────────────────────────────────────
if fetch_btn:
    try:
        with st.spinner(f"Loading {selected_table_fqn}..."):
            token = get_user_token()
            raw = _admin_fetch_table(selected_table_fqn, token)
            if IS_MASTER:
                _safe_dtype = {k: v for k, v in SCHEMA_DTYPE.items() if k in raw.columns}
                raw = raw.astype(_safe_dtype)

            st.session_state["admin_raw_data"] = raw
            st.session_state["admin_data"] = raw.copy()
            if IS_MASTER:
                st.session_state["admin_sfe_df"] = raw.copy()
            st.session_state["admin_page"] = 1
            st.session_state["admin_total_pages"] = max(
                1, math.ceil(len(raw) / ADMIN_PAGE_SIZE),
            )

        if raw.empty:
            st.info("Table loaded but returned **0 rows**.")
        else:
            st.toast(
                f"Loaded **{len(raw):,}** rows \u00d7 **{len(raw.columns)}** columns.",
                icon="\u2705",
            )
            st.rerun()
    except Exception as ex:
        st.error(f"Failed to load table: {ex}")


# ═════════════════════════════════════════════════════════════
#  DATA EDITOR  (only shown when data is loaded)
# ═════════════════════════════════════════════════════════════

if "admin_data" not in st.session_state:
    # ── Auto-load Master Table on first visit ────────────────
    try:
        with st.spinner(f"Loading {selected_table_fqn}..."):
            token = get_user_token()
            raw = _admin_fetch_table(selected_table_fqn, token)
            if IS_MASTER:
                _safe_dtype = {k: v for k, v in SCHEMA_DTYPE.items() if k in raw.columns}
                raw = raw.astype(_safe_dtype)

            st.session_state["admin_raw_data"] = raw
            st.session_state["admin_data"] = raw.copy()
            if IS_MASTER:
                st.session_state["admin_sfe_df"] = raw.copy()
            st.session_state["admin_page"] = 1
            st.session_state["admin_total_pages"] = max(
                1, math.ceil(len(raw) / ADMIN_PAGE_SIZE),
            )

        if raw.empty:
            st.info("Table loaded but returned **0 rows**.")
            st.stop()
        else:
            st.rerun()
    except Exception as ex:
        st.error(f"Failed to auto-load table: {ex}")
        st.stop()

# ── Apply active filters ─────────────────────────────────────
active_filters = st.session_state.get("admin_active_filters", {})
if active_filters:
    try:
        filtered = st.session_state["admin_raw_data"].copy()
        for col, vals in active_filters.items():
            if col in filtered.columns:
                filtered = filtered[filtered[col].astype(str).isin(vals)]
        st.session_state["admin_data"] = filtered.reset_index(drop=True)
        if IS_MASTER:
            st.session_state["admin_sfe_df"] = filtered.reset_index(drop=True)
        st.session_state["admin_total_pages"] = max(
            1, math.ceil(len(filtered) / ADMIN_PAGE_SIZE),
        )
    except Exception as _ex:
        st.warning(f"Filter application failed: {_ex}")

full_df = st.session_state["admin_data"]
all_columns = list(full_df.columns)
visible_columns = [
    c for c in admin_get_visible_columns(current_user, selected_table_label, all_columns)
    if c not in _HIDDEN_DISPLAY_COLS
]

if not visible_columns:
    st.warning("You have no column access for this table. Contact an administrator.")
    st.stop()

pk_col = "row_id" if "row_id" in all_columns else all_columns[0]

# ── Sort form ────────────────────────────────────────────────
with st.form("admin_sort_form", clear_on_submit=False):
    sc1, sc2, sc3 = st.columns([2.5, 2.5, 1], vertical_alignment="bottom")
    with sc1:
        sort_col = st.selectbox(
            "Sort by column", options=visible_columns, key="admin_sort_col",
        )
    with sc2:
        sort_ord = st.selectbox(
            "Sort order", options=["ascending", "descending"],
            key="admin_sort_ord",
        )
    with sc3:
        sort_submitted = st.form_submit_button(
            "Sort", type="primary", use_container_width=True,
        )

if sort_submitted:
    try:
        target_key = "admin_sfe_df" if IS_MASTER else "admin_data"
        st.session_state[target_key] = (
            st.session_state[target_key]
            .sort_values(by=sort_col, ascending=(sort_ord == "ascending"))
            .reset_index(drop=True)
        )
    except Exception as _ex:
        st.warning(f"Sort failed: {_ex}")

# ── Pagination ───────────────────────────────────────────────
if "admin_page" not in st.session_state:
    st.session_state["admin_page"] = 1

if IS_MASTER:
    sfe = st.session_state.get(
        "admin_sfe_df", st.session_state["admin_data"].copy(),
    )
    total_rows = len(sfe)
else:
    total_rows = len(st.session_state["admin_data"])

total_pages = max(1, math.ceil(total_rows / ADMIN_PAGE_SIZE))
st.session_state["admin_total_pages"] = total_pages

page = st.session_state["admin_page"]
si = (page - 1) * ADMIN_PAGE_SIZE
ei = si + ADMIN_PAGE_SIZE


# ═════════════════════════════════════════════════════════════
#  MASTER TABLE EDITOR  (full feature set from page 2)
# ═════════════════════════════════════════════════════════════

if IS_MASTER:
    original_data = (
        sfe.iloc[si:ei]
        .loc[lambda d: d["row_id"].notna()]
        .sort_values("row_id")
        .reset_index(drop=True)
        .set_index("row_id")
    )

    page_slice = sfe.iloc[si:ei].copy()
    page_slice.insert(0, "_select", False)
    page_slice.insert(1, "_clr", page_slice["row_id"].apply(
        lambda x: "\U0001f7e1" if pd.isna(x) else ""
    ))

    col_cfg = build_column_config(
        st.session_state["admin_data"], dropdown_required=False,
    )
    col_cfg["_select"] = st.column_config.CheckboxColumn(
        "\u2795", width="small", pinned=True,
        help="Tick rows to copy below",
    )
    col_cfg["_clr"] = st.column_config.TextColumn(
        " ", width=35, pinned=True, disabled=True,
    )

    edited_data = st.data_editor(
        data=page_slice,
        use_container_width=True, hide_index=True,
        num_rows="dynamic",
        disabled=["sp_test_id", "cl_test_id", "ingestion_timestamp", "_clr"],
        column_config=col_cfg,
        column_order=(
            ["_select", "_clr"]
            + [c for c in page_slice.columns if c not in ("_select", "_clr") and c not in _HIDDEN_DISPLAY_COLS]
        ),
        key=f"admin_editor_{st.session_state.get('admin_key_counter', 0)}",
    )

    # ── Handle copy-row-below ─────────────────────────────────
    selected_rows = edited_data[edited_data["_select"] == True]

    if not selected_rows.empty:
        if st.button(
            f"\u2795 Copy {len(selected_rows)} row(s) below",
            type="primary", key="admin_copy_btn",
        ):
            try:
                current_sfe = st.session_state["admin_sfe_df"]
                positions: list[int] = []
                for idx in selected_rows.index:
                    if si <= idx < min(ei, len(current_sfe)):
                        positions.append(idx)

                positions.sort(reverse=True)
                result = current_sfe.copy()
                for abs_pos in positions:
                    copied = result.iloc[[abs_pos]].copy()
                    copied["row_id"] = pd.NA
                    copied["sp_test_id"] = pd.NA
                    copied["cl_test_id"] = pd.NA
                    copied["ingestion_timestamp"] = pd.NaT
                    top = result.iloc[: abs_pos + 1]
                    bot = result.iloc[abs_pos + 1:]
                    result = pd.concat([top, copied, bot], ignore_index=True)

                st.session_state["admin_sfe_df"] = result
                st.session_state["admin_total_pages"] = max(
                    1, math.ceil(len(result) / ADMIN_PAGE_SIZE),
                )
                st.toast(f"Copied {len(positions)} row(s) below", icon="\U0001f4cb")
                st.rerun()
            except Exception as _ex:
                st.error(f"Copy row failed: {_ex}")

    # Strip transient columns
    edited_data = edited_data.drop(columns=["_select", "_clr"], errors="ignore")
    edited_data = (
        edited_data.sort_values("row_id")
        .reset_index(drop=True)
        .set_index("row_id")
    )

    # ── Save modal (Master Table) ─────────────────────────────
    @st.dialog("Change Summary", width="medium")
    def admin_save_master_modal():
        try:
            # ── Merge current page edits into full working copy ──────
            _full_sfe = st.session_state["admin_sfe_df"].copy()
            _page_edited = edited_data.reset_index()  # row_id back as column
            _before = _full_sfe.iloc[:si]
            _after = _full_sfe.iloc[ei:]
            _merged = pd.concat([_before, _page_edited, _after], ignore_index=True)

            # ── Full original ────────────────────────────────────────
            _full_orig = st.session_state.get(
                "admin_raw_data", st.session_state["admin_data"],
            ).copy()

            # ── Separate new rows (row_id NA) from existing ─────────
            _new_rows = _merged[_merged["row_id"].isna()]
            _existing = (
                _merged[_merged["row_id"].notna()]
                .sort_values("row_id").reset_index(drop=True).set_index("row_id")
            )
            _orig_indexed = (
                _full_orig[_full_orig["row_id"].notna()]
                .sort_values("row_id").reset_index(drop=True).set_index("row_id")
            )

            # ── Detect changes across ALL pages ──────────────────────
            deleted_rows = _orig_indexed.index.difference(_existing.index)
            deleted_count = len(deleted_rows)
            added_count = len(_new_rows)

            common_rows = _orig_indexed.index.intersection(_existing.index)
            _compare_cols = [
                c for c in _orig_indexed.columns if c in _existing.columns
            ]
            if len(common_rows) > 0 and _compare_cols:
                diff = _orig_indexed.loc[common_rows, _compare_cols].compare(
                    _existing.loc[common_rows, _compare_cols],
                ).stack(level=0)
                updated_count = len(diff.index.levels[0]) if len(diff) else 0
            else:
                diff = pd.DataFrame()
                updated_count = 0
        except Exception as _ex:
            st.error(f"Failed to compute change summary: {_ex}")
            return

        if added_count == 0 and deleted_count == 0 and updated_count == 0:
            st.info(
                "No **data** changes detected across all pages.\n\n"
                "Only cell edits, row additions, and row deletions are "
                "tracked. Column operations (hide, resize, reorder) "
                "are visual only and not saved."
            )
            return

        st.warning(
            f"Rows added: {added_count}, "
            f"Rows deleted: {deleted_count}, "
            f"Rows updated: {updated_count}"
        )

        insert_rows_df = (
            _new_rows.set_index("row_id") if added_count > 0 else None
        )
        delete_rows_df = (
            _orig_indexed.loc[deleted_rows] if deleted_count > 0 else None
        )
        update_rows_df = (
            _existing.loc[diff.index.levels[0]] if updated_count > 0 else None
        )

        if insert_rows_df is not None:
            st.write("**Added Rows:**")
            st.dataframe(insert_rows_df)
        if delete_rows_df is not None:
            st.write("**Deleted Rows:**")
            st.dataframe(delete_rows_df)
        if update_rows_df is not None:
            st.write("**Updated Rows:**")
            st.dataframe(update_rows_df)
            st.write("**Updated Cells:**")
            st.dataframe(diff)

        scr1, scr2, _ = st.columns([1.5, 1.5, 3])
        with scr1:
            st.button("Confirm & Save", type="primary", key="admin_confirm_btn")
        with scr2:
            st.button("Discard Changes", type="primary", key="admin_discard_btn")

        if st.session_state.get("admin_discard_btn"):
            _admin_discard_all()

        if st.session_state.get("admin_confirm_btn"):
            tk = get_user_token()
            conn = get_connection(tk)
            create_audit_table(conn)

            inserted_ids, deleted_ids = [], []

            # 1) UPDATE
            if updated_count > 0:
                pre_errors = validate_mandatory_cols(
                    update_rows_df.reset_index(), MANDATORY_UPDATE_COLS,
                )
                if pre_errors:
                    st.error(
                        f"Cannot save: {len(pre_errors)} row(s) have "
                        f"missing mandatory columns "
                        f"({', '.join(MANDATORY_UPDATE_COLS)}):"
                    )
                    for e in pre_errors:
                        st.warning(e)
                else:
                    with st.spinner("Updating rows..."):
                        ok, err, err_list, _ = bulk_update(
                            conn, update_rows_df, diff, verify=False,
                        )
                        if err == 0:
                            st.success(f"[UPDATE] {update_rows_df.shape[0]} rows updated.")
                        else:
                            st.error(f"[UPDATE] failed: {err_list}")
            else:
                st.info("No rows to update.")

            # 2) INSERT
            if added_count > 0:
                insert_pre_errors = validate_mandatory_cols(
                    insert_rows_df.reset_index(), MANDATORY_UPDATE_COLS,
                )
                if insert_pre_errors:
                    st.error(
                        f"Cannot insert: {len(insert_pre_errors)} row(s) have "
                        f"missing mandatory columns "
                        f"(**{', '.join(MANDATORY_UPDATE_COLS)}**). "
                        f"Please fill in all mandatory columns before saving."
                    )
                    for e in insert_pre_errors:
                        st.warning(e)
                else:
                    with st.spinner("Inserting rows..."):
                        ok, err, err_list, rid_list = bulk_insert(
                            conn, insert_rows_df,
                        )
                        if err == 0:
                            inserted_ids = rid_list
                            st.success(f"[INSERT] {ok} rows inserted (IDs: {rid_list}).")
                        else:
                            nat_err = any(
                                "NaT" in str(e) or "infer parameter type" in str(e)
                                for e in err_list
                            )
                            if nat_err:
                                st.error(
                                    f"Insert failed: some rows have empty mandatory columns "
                                    f"(**{', '.join(MANDATORY_UPDATE_COLS)}**). "
                                    f"Please provide values for all mandatory columns."
                                )
                            else:
                                st.error(f"[INSERT] failed: {err_list}")

            # 3) DELETE
            if deleted_count > 0:
                with st.spinner("Deleting rows..."):
                    ok, err, err_list, rid_list = bulk_delete(
                        conn, delete_rows_df,
                    )
                    if err == 0:
                        deleted_ids = rid_list
                        st.success(f"[DELETE] {ok} rows deleted (IDs: {rid_list}).")
                    else:
                        st.error(f"[DELETE] failed: {err_list}")

            # 4) AUDIT
            try:
                events = build_audit_events(
                    original_df=_orig_indexed,
                    edited_df=_existing,
                    added_rows_df=insert_rows_df,
                    deleted_rows_df=delete_rows_df,
                    diff_df=diff if updated_count > 0 else None,
                    page_no=st.session_state["admin_page"],
                    session_key="admin_session_id",
                    source="Admin UI",
                    inserted_row_ids=inserted_ids,
                    deleted_row_ids=deleted_ids,
                )
                write_audit_events(conn, events)
                st.toast("Audit written", icon="\U0001f4dd")
            except Exception as ex:
                st.warning(f"Audit write skipped: {ex}")

            # 5) Re-fetch
            try:
                raw = _admin_fetch_table(selected_table_fqn, tk)
                _safe_dtype = {k: v for k, v in SCHEMA_DTYPE.items() if k in raw.columns}
                raw = raw.astype(_safe_dtype)
                st.session_state["admin_raw_data"] = raw
                st.session_state["admin_data"] = raw.copy()
                st.session_state["admin_sfe_df"] = raw.copy()
                st.session_state["admin_total_pages"] = max(
                    1, math.ceil(len(raw) / ADMIN_PAGE_SIZE),
                )
            except Exception as ex:
                st.warning(f"Auto-refresh failed: {ex}")
            st.session_state["admin_key_counter"] = st.session_state.get("admin_key_counter", 0) + 1
            st.toast("Changes saved successfully!", icon="\u2705")
            time.sleep(1)
            st.rerun()

    if save_btn:
        admin_save_master_modal()

    # ── Bulk INSERT modal (Master Table) ──────────────────────
    @st.dialog("Bulk Rows INSERT", width="medium")
    def admin_bulk_upload_modal():
        tab_file, tab_copy = st.tabs(["File upload", "Rows bulk copy table"])

        with tab_file:
            if os.path.exists(EXCEL_TEMPLATE):
                with open(EXCEL_TEMPLATE, "rb") as fh:
                    st.download_button(
                        label="Download Excel Template", data=fh,
                        file_name="bulk_update_template.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )

            uploaded = st.file_uploader(
                "Upload CSV or Excel file", type=["csv", "xlsx"],
            )
            if uploaded:
                try:
                    if uploaded.name.endswith(".csv"):
                        df_up = pd.read_csv(uploaded).astype(UPLOAD_DTYPE)
                    else:
                        df_up = pd.read_excel(uploaded).astype(UPLOAD_DTYPE)
                except Exception as _ex:
                    st.error(f"Failed to parse uploaded file: {_ex}")
                    df_up = None

                if df_up is not None:
                    st.write(f"Preview of {len(df_up)} rows to insert:")
                    st.dataframe(df_up)

                bc1, bc2, _ = st.columns([1.5, 1.5, 3])
                with bc1:
                    st.button("Confirm & Insert", type="primary",
                              key="admin_confirm_insert_btn")
                with bc2:
                    st.button("Discard upload", type="primary",
                              key="admin_discard_upload_btn")

                if st.session_state.get("admin_confirm_insert_btn"):
                    try:
                        with st.spinner("Inserting rows..."):
                            tk = get_user_token()
                            ok, err, err_list, rid_list = bulk_insert(
                                get_connection(tk), df_up,
                            )
                            if len(rid_list) == len(df_up):
                                st.success(f"[INSERT] {ok} rows inserted (IDs: {rid_list}).")
                            if err:
                                st.error(f"Failed to insert {err} rows")
                                with st.expander("View Errors"):
                                    for e in err_list:
                                        st.write(e)
                    except Exception as _ex:
                        st.error(f"Bulk insert failed: {_ex}")
                if st.session_state.get("admin_discard_upload_btn"):
                    st.rerun()

        with tab_copy:
            if "admin_bulk_edit_df" not in st.session_state:
                empty = pd.DataFrame(
                    columns=list(UPLOAD_DTYPE.keys()),
                ).astype(UPLOAD_DTYPE)
                empty_row = pd.DataFrame(
                    [{c: None for c in UPLOAD_DTYPE}],
                ).astype(UPLOAD_DTYPE)
                st.session_state["admin_bulk_edit_df"] = pd.concat(
                    [empty, empty_row], ignore_index=True,
                )

            b_edited = st.data_editor(
                data=st.session_state["admin_bulk_edit_df"],
                height=70, use_container_width=True, hide_index=True,
                num_rows="fixed",
                disabled=["sp_test_id", "cl_test_id"],
                column_config=col_cfg,
            )

            bc1, bc2, _ = st.columns(3, vertical_alignment="bottom")
            with bc1:
                st.number_input(
                    "Copy row (xTimes)", min_value=1, max_value=999,
                    step=1, key="admin_bei_nu",
                )
            with bc2:
                st.button(
                    "Insert duplicate rows", type="primary", key="admin_bei_btn",
                )

    if bulk_btn:
        admin_bulk_upload_modal()


# ═════════════════════════════════════════════════════════════
#  NON-MASTER TABLE EDITOR  (simple behaviour)
# ═════════════════════════════════════════════════════════════

else:
    page_df = st.session_state["admin_data"].iloc[si:ei][visible_columns].copy()
    original_page = st.session_state["admin_data"].iloc[si:ei][visible_columns].copy()

    col_cfg_simple: dict[str, Any] = {}
    for col in visible_columns:
        col_cfg_simple[col] = st.column_config.Column(label=to_bold(col))
    if pk_col in visible_columns:
        col_cfg_simple[pk_col] = st.column_config.Column(
            label=to_bold(pk_col), pinned=True,
        )

    edited_df = st.data_editor(
        data=page_df,
        use_container_width=True, hide_index=True,
        num_rows="dynamic",
        column_config=col_cfg_simple,
        key=f"admin_editor_{st.session_state.get('admin_key_counter', 0)}",
    )

    # ── Save modal (simple tables) ────────────────────────────
    @st.dialog("Change Summary", width="medium")
    def admin_save_simple_modal():
        orig = original_page.reset_index(drop=True)
        edit = edited_df.reset_index(drop=True)

        if orig.equals(edit) and len(orig) == len(edit):
            st.info(
                "No **data** changes detected on this page.\n\n"
                "Only cell edits, row additions, and row deletions are "
                "tracked. Column operations (hide, resize, reorder) "
                "are visual only and not saved."
            )
            return

        from collections import Counter
        def _row_key(row, cols):
            return tuple("" if pd.isna(row[c]) else str(row[c]) for c in cols)
        orig_keys = [_row_key(orig.iloc[i], visible_columns) for i in range(len(orig))]
        edit_keys = [_row_key(edit.iloc[i], visible_columns) for i in range(len(edit))]
        orig_counter = Counter(orig_keys)
        edit_counter = Counter(edit_keys)
        truly_deleted = orig_counter - edit_counter
        truly_added = edit_counter - orig_counter
        _del_remaining = dict(truly_deleted)
        deleted_indices: list[int] = []
        for i, key in enumerate(orig_keys):
            if key in _del_remaining and _del_remaining[key] > 0:
                deleted_indices.append(i)
                _del_remaining[key] -= 1
        _add_remaining = dict(truly_added)
        added_indices: list[int] = []
        for i, key in enumerate(edit_keys):
            if key in _add_remaining and _add_remaining[key] > 0:
                added_indices.append(i)
                _add_remaining[key] -= 1
        deleted_count = len(deleted_indices)
        added_count = len(added_indices)
        updated_count = 0

        st.warning(
            f"Rows added: {added_count}, "
            f"Rows deleted: {deleted_count}, "
            f"Rows updated: {updated_count}"
        )

        if added_count > 0:
            st.write("**Added Rows:**")
            st.dataframe(edit.iloc[added_indices])
        if deleted_count > 0:
            st.write("**Deleted Rows:**")
            st.dataframe(orig.iloc[deleted_indices])

        scr1, scr2, _ = st.columns([1.5, 1.5, 3])
        with scr1:
            st.button("Confirm & Save", type="primary", key="admin_confirm_btn")
        with scr2:
            st.button("Discard Changes", type="primary", key="admin_discard_btn")

        if st.session_state.get("admin_discard_btn"):
            st.session_state["admin_data"] = st.session_state["admin_raw_data"].copy()
            st.toast("Changes discarded.", icon="\u267b\ufe0f")
            time.sleep(0.5)
            st.rerun()

        if st.session_state.get("admin_confirm_btn"):
            tk = get_user_token()
            table_fqn = selected_table_fqn
            errors: list[str] = []

            pass  # cell edits handled as delete+add for non-PK tables

            if added_count > 0:
                with st.spinner("Inserting rows..."):
                    new_rows = edit.iloc[added_indices]
                    for _, row in new_rows.iterrows():
                        cols: list[str] = []
                        vals: list = []
                        for c in visible_columns:
                            v = row[c]
                            if not pd.isna(v):
                                cols.append(c)
                                vals.append(v)
                        if cols:
                            ph = ", ".join(["?"] * len(cols))
                            insert_sql = (
                                f"INSERT INTO {table_fqn} "
                                f"({', '.join(cols)}) VALUES ({ph})"
                            )
                            try:
                                run_statement(insert_sql, vals, tk)
                            except Exception as ex:
                                errors.append(f"Insert: {ex}")
                if not errors:
                    st.success(f"Inserted {added_count} row(s).")

            if deleted_count > 0:
                with st.spinner("Deleting rows..."):
                    del_rows = orig.iloc[deleted_indices]
                    for row_idx, (_, row) in enumerate(del_rows.iterrows()):
                        where_clause, where_params = build_composite_where(
                            row, visible_columns,
                        )
                        if where_clause:
                            delete_sql = (
                                f"DELETE FROM {table_fqn} WHERE {where_clause}"
                            )
                            try:
                                run_statement(delete_sql, where_params, tk)
                            except Exception as ex:
                                errors.append(f"Delete row {row_idx}: {ex}")
                if not errors:
                    st.success(f"Deleted {deleted_count} row(s).")

            if errors:
                st.error("Some operations failed:")
                for e in errors:
                    st.warning(e)
            else:
                try:
                    raw = _admin_fetch_table(table_fqn, tk)
                    st.session_state["admin_raw_data"] = raw
                    st.session_state["admin_data"] = raw.copy()
                    st.session_state["admin_total_pages"] = max(
                        1, math.ceil(len(raw) / ADMIN_PAGE_SIZE),
                    )
                except Exception as ex:
                    st.warning(f"Auto-refresh failed: {ex}")
                st.session_state["admin_key_counter"] = st.session_state.get("admin_key_counter", 0) + 1
                st.toast("Changes saved successfully!", icon="\u2705")
                time.sleep(1)
                st.rerun()

    if save_btn:
        admin_save_simple_modal()


# ═════════════════════════════════════════════════════════════
#  PAGINATION CONTROLS
# ═════════════════════════════════════════════════════════════

def _admin_go_page(delta: int):
    st.session_state["admin_page"] += delta
    st.rerun()

pg_prev, pg_lbl, pg_input, pg_of, pg_next = st.columns(
    [1, 0.5, 0.6, 2, 1], vertical_alignment="center",
)
with pg_prev:
    if st.button("Previous", use_container_width=True,
                 disabled=page <= 1, key="admin_prev_page"):
        _admin_go_page(-1)
with pg_lbl:
    st.markdown(
        "<div style='text-align:right; font-weight:600; white-space:nowrap;'>Page</div>",
        unsafe_allow_html=True,
    )
with pg_input:
    admin_go_page_num = st.number_input(
        "Go to page", min_value=1, max_value=total_pages,
        value=page, step=1, key="admin_go_page_input",
        label_visibility="collapsed",
    )
    if admin_go_page_num != page:
        st.session_state["admin_page"] = admin_go_page_num
        st.rerun()
with pg_of:
    st.markdown(
        f"<div style='font-weight:600; white-space:nowrap;'>"
        f"of {total_pages} &nbsp;|&nbsp; {total_rows:,} rows total</div>",
        unsafe_allow_html=True,
    )
with pg_next:
    if st.button("Next", use_container_width=True,
                 disabled=page >= total_pages, key="admin_next_page"):
        _admin_go_page(1)
