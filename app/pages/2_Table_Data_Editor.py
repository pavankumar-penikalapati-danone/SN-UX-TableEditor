# ============================================================
# 2_Table_Data_Editor.py  –  SN UX Table Editor (Page 2)
# ============================================================
# All constants, helpers, CSS, and business logic imported from
# app/config.py.  This file contains ONLY Streamlit UI code.
# ============================================================

from __future__ import annotations

import math
import os
import time
import uuid

import pandas as pd
import streamlit as st

# ── Import everything from shared config ─────────────────────
from config import (
    CATALOG, SCHEMA, MAIN_TABLE, TABLE_FQN, PAGE_SIZE,
    SELECT_COLUMNS, DML_COLUMNS, SCHEMA_DTYPE, UPLOAD_DTYPE,
    FILTER_COLUMNS, DROPDOWN_COLS, EXCEL_TEMPLATE,
    SHARED_CSS, APP_LOGO_IMAGE, APP_LOGO_ICON, APP_LOGO_LINK,
    MANDATORY_UPDATE_COLS,
    to_bold, is_na, validate_mandatory_cols,
    get_user_token, get_connection, run_query,
    get_user_identity, ensure_session_id,
    compute_seq, generate_test_id,
    create_audit_table, write_audit_events,
    build_audit_events, log_edit_start_once,
    build_column_config, dropdown_options,
    bulk_insert, bulk_delete, bulk_update,
    get_disabled_columns,
    get_disabled_columns_by_group,
    EDITOR_USERS,
    is_user_in_editor_group,
)

# Columns kept in data for DML but hidden from display and filters
_HIDDEN_DISPLAY_COLS = {"ingestion_timestamp", "Unnamed__64", "Unnamed__65", "Unnamed__66", "Unnamed__71", "Unnamed__72", "Unnamed__73"}


# ═════════════════════════════════════════════════════════════
#  DISCARD CHANGES  (page-specific session keys)
# ═════════════════════════════════════════════════════════════

def discard_all_changes():
    """Reset editable data to original without touching widget keys."""
    try:
        if "edit_data" not in st.session_state:
            st.warning("No data loaded. Nothing to discard.")
            return
        st.session_state["edit_sfe_df"] = st.session_state["edit_data"].copy()
        st.session_state["edit_key_counter"] = st.session_state.get("edit_key_counter", 0) + 1
        if "edit_page" in st.session_state:
            start = (st.session_state["edit_page"] - 1) * PAGE_SIZE
            st.session_state["edit_start_index"] = start
            st.session_state["edit_end_index"] = start + PAGE_SIZE
        for flag in ("edit_start_logged", "edit_filter_triggered",
                     "save_pending", "changes_made"):
            st.session_state[flag] = False
        st.toast("All changes discarded", icon="\u267b\ufe0f")
        st.rerun()
    except Exception as ex:
        st.error(f"Failed to discard changes: {ex}")


# ═════════════════════════════════════════════════════════════
#  STREAMLIT PAGE CONFIG & STYLES
# ═════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="SN UX Table Editor",
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
#  ACCESS CONTROL  (group-based: edit vs read-only)
# ═════════════════════════════════════════════════════════════

try:
    _current_user = get_user_identity()
except Exception as _ex:
    st.error(f"Failed to identify current user: {_ex}")
    _current_user = "unknown"

try:
    _can_edit = is_user_in_editor_group(_current_user)
except Exception as _ex:
    st.warning(f"Could not determine editor permissions: {_ex}")
    _can_edit = False

# ── Hide Admin page from sidebar for non-admin users ─────────
_admin_users = [u.strip().lower() for u in __import__("os").environ.get("ADMIN_USERS", "").split(",") if u.strip()]
_approver_emails = [e.strip().lower() for e in __import__("os").environ.get("TEST_STATUS_APPROVERS", "").split(",") if e.strip()]
_hide_css = []
if _current_user.lower() not in _admin_users:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"] { display: none !important; }')
if _current_user.lower() not in _approver_emails:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Approval_Dashboard"] { display: none !important; }')
if _hide_css:
    st.markdown(f'<style>{"".join(_hide_css)}</style>', unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════
#  PAGE CONTENT  (if/else: shows editor OR fetch form)
# ═════════════════════════════════════════════════════════════

if "edit_data" in st.session_state:

    # ── Pagination init ────────────────────────────────────────
    if "edit_page" not in st.session_state:
        st.session_state["edit_page"] = 1
        st.session_state["edit_start_index"] = 0
        st.session_state["edit_end_index"] = PAGE_SIZE
    if "edit_key_counter" not in st.session_state:
        st.session_state["edit_key_counter"] = 0

    # ── Sidebar filters (fixed + dynamic column selection) ──────
    with st.sidebar:
        if st.session_state.get("edit_init_data_fetch"):
            st.header("Filters")

            def _edit_filter_toggle(col_key):
                """ALL / specific-value mutual exclusion."""
                def _on_change():
                    sel = st.session_state[col_key]
                    if "ALL" in sel and len(sel) > 1 and sel[0] == "ALL":
                        st.session_state[col_key] = [v for v in sel if v != "ALL"]
                    elif ("ALL" in sel and sel[-1] == "ALL") or len(sel) == 0:
                        st.session_state[col_key] = ["ALL"]
                return _on_change

            _edit_raw = st.session_state["edit_data"]
            edit_filters: dict[str, list] = {}

            # ── Fixed filters (always visible from FILTER_COLUMNS) ───
            for col in FILTER_COLUMNS:
                if col not in _edit_raw.columns or col in _HIDDEN_DISPLAY_COLS:
                    continue
                raw_vals = _edit_raw[col].dropna().unique()
                if pd.api.types.is_numeric_dtype(_edit_raw[col]):
                    unique_vals = [str(v) for v in sorted(raw_vals)]
                else:
                    unique_vals = sorted([str(v) for v in raw_vals])
                selected_vals = st.multiselect(
                    f"Select {col}:",
                    options=["ALL"] + unique_vals,
                    default=["ALL"],
                    key=f"edit_flt_{col}",
                    on_change=_edit_filter_toggle(f"edit_flt_{col}"),
                )
                if selected_vals and "ALL" not in selected_vals:
                    edit_filters[col] = selected_vals

            # ── Dynamic filters (additional columns) ─────────────────
            extra_cols = [c for c in _edit_raw.columns if c not in FILTER_COLUMNS and c not in _HIDDEN_DISPLAY_COLS]
            selected_filter_cols = st.multiselect(
                "Filter by columns:",
                options=extra_cols,
                default=[],
                key="edit_filter_cols",
            )

            for col in selected_filter_cols:
                raw_vals = _edit_raw[col].dropna().unique()
                if pd.api.types.is_numeric_dtype(_edit_raw[col]):
                    unique_vals = [str(v) for v in sorted(raw_vals)]
                else:
                    unique_vals = sorted([str(v) for v in raw_vals])
                selected_vals = st.multiselect(
                    f"Select {col}:",
                    options=["ALL"] + unique_vals,
                    default=["ALL"],
                    key=f"edit_flt_{col}",
                    on_change=_edit_filter_toggle(f"edit_flt_{col}"),
                )
                if selected_vals and "ALL" not in selected_vals:
                    edit_filters[col] = selected_vals

            # ── Apply / Clear buttons (always visible) ───────────────
            fc1, fc2 = st.columns(2)
            with fc1:
                filter_btn = st.button(
                    "Apply Filters", type="primary",
                    use_container_width=True, key="edit_apply_filters",
                )
            with fc2:
                clear_btn = st.button(
                    "Clear Filters", use_container_width=True,
                    key="edit_clear_filters",
                )

            if filter_btn:
                st.session_state["edit_active_filters"] = edit_filters
                st.session_state["edit_page"] = 1
                st.session_state["edit_start_index"] = 0
                st.session_state["edit_end_index"] = PAGE_SIZE
                st.rerun()

            if clear_btn:
                st.session_state["edit_active_filters"] = {}
                st.session_state["edit_sfe_df"] = st.session_state["edit_data"].copy()
                st.session_state["edit_key_counter"] = st.session_state.get("edit_key_counter", 0) + 1
                st.session_state["edit_page"] = 1
                st.session_state["edit_start_index"] = 0
                st.session_state["edit_end_index"] = PAGE_SIZE
                st.session_state["edit_total_pages"] = max(
                    1, math.ceil(len(st.session_state["edit_data"]) / PAGE_SIZE),
                )
                st.rerun()

    # ── Title bar ────────────────────────────────────────────
    rc1, rc2, rc3 = st.columns([4, 1, 1], vertical_alignment="bottom")
    with rc1:
        st.markdown(
            "<h2 style='font-size:28px; font-weight:700; margin-bottom:5px;'>"
            "[EDIT] SN UX CONSO Master Table</h2>",
            unsafe_allow_html=True,
        )
        if not _can_edit:
            st.caption("\U0001f512 Read-only mode \u2013 you are not in the editor group")
    with rc2:
        if _can_edit:
            st.button("Save changes", type="primary", key="save_change_btn",
                      use_container_width=True)
    with rc3:
        if _can_edit:
            st.button("Bulk INSERT", type="primary", key="bulk_upload_btn",
                      use_container_width=True)

    # ── Sort form ────────────────────────────────────────────
    with st.form("sort_form", clear_on_submit=False):
        sc1, sc2, sc3 = st.columns([2.5, 2.5, 1], vertical_alignment="bottom")
        with sc1:
            sort_col = st.selectbox(
                "Column name", options=[c for c in st.session_state["edit_data"].columns if c not in _HIDDEN_DISPLAY_COLS],
            )
        with sc2:
            sort_ord = st.selectbox(
                "Sort order", options=["ascending", "descending"],
            )
        with sc3:
            sort_submitted = st.form_submit_button(
                "Sort table", type="primary", use_container_width=True,
            )

    # ── Apply active filters ───────────────────────────────────
    active_filters = st.session_state.get("edit_active_filters", {})
    if active_filters:
        try:
            df_filtered = st.session_state["edit_data"].copy()
            for col, vals in active_filters.items():
                if col in df_filtered.columns:
                    df_filtered = df_filtered[df_filtered[col].astype(str).isin(vals)]
            st.session_state["edit_sfe_df"] = df_filtered.reset_index(drop=True)
            st.session_state["edit_total_pages"] = max(
                1, math.ceil(len(st.session_state["edit_sfe_df"]) / PAGE_SIZE),
            )
        except Exception as _ex:
            st.warning(f"Filter application failed: {_ex}")

    # ── Apply sort ───────────────────────────────────────────
    if sort_submitted:
        try:
            st.session_state["edit_sfe_df"] = (
                st.session_state["edit_sfe_df"]
                .sort_values(by=sort_col, ascending=(sort_ord == "ascending"))
                .reset_index(drop=True)
            )
        except Exception as _ex:
            st.warning(f"Sort failed: {_ex}")

    # ── Hide completed rows (test_status != 'complete') ─────
    sfe = st.session_state["edit_sfe_df"]
    try:
        sfe = sfe[
            sfe["test_status"].astype(str).str.strip().str.lower() != "complete"
        ].reset_index(drop=True)
    except Exception:
        pass  # keep sfe as-is if filter fails
    st.session_state["edit_sfe_df"] = sfe
    st.session_state["edit_total_pages"] = max(
        1, math.ceil(len(sfe) / PAGE_SIZE),
    )

    # ── Prepare sfe for slicing ──────────────────────────────

    # ── Slice page ───────────────────────────────────────────
    si = st.session_state["edit_start_index"]
    ei = st.session_state["edit_end_index"]

    original_data = (
        sfe.iloc[si:ei]
        .loc[lambda d: d["row_id"].notna()]
        .sort_values("row_id")
        .reset_index(drop=True)
        .set_index("row_id")
    )

    # ── Insert row button (copy below) ─────────────────────
    page_slice = sfe.iloc[si:ei].copy()
    page_slice.insert(0, "_select", False)
    page_slice.insert(1, "_clr", page_slice["row_id"].apply(
        lambda x: "\U0001f7e1" if pd.isna(x) else ""
    ))

    col_cfg = build_column_config(
        st.session_state["edit_data"], dropdown_required=True,
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
        num_rows="dynamic" if _can_edit else "fixed",
        key=f"edit_grid_{st.session_state.get('edit_key_counter', 0)}",
        disabled=(
            get_disabled_columns_by_group(_current_user, list(page_slice.columns)) + ["_clr"]
            if _can_edit
            else True  # all columns read-only
        ),
        column_config=col_cfg,
        column_order=["_select", "_clr"] + [
            c for c in page_slice.columns if c not in ("_select", "_clr") and c not in _HIDDEN_DISPLAY_COLS
        ],
    )

    # ── Handle copy-row-below action ──────────────────────────
    selected_rows = edited_data[edited_data["_select"] == True]

    if not selected_rows.empty and _can_edit:
        if st.button(
            f"\u2795 Copy {len(selected_rows)} row(s) below",
            type="primary", key="apply_copy_btn",
        ):
            try:
                current_sfe = st.session_state["edit_sfe_df"]
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
                    bot = result.iloc[abs_pos + 1 :]
                    result = pd.concat([top, copied, bot], ignore_index=True)

                st.session_state["edit_sfe_df"] = result
                st.session_state["edit_total_pages"] = max(
                    1, math.ceil(len(result) / PAGE_SIZE),
                )
                st.toast(f"Copied {len(positions)} row(s) below", icon="\U0001f4cb")
                st.rerun()
            except Exception as _ex:
                st.error(f"Copy row failed: {_ex}")

    # Strip transient columns before further processing
    edited_data = edited_data.drop(columns=["_select", "_clr"], errors="ignore")

    edited_data = (
        edited_data.sort_values("row_id")
        .reset_index(drop=True)
        .set_index("row_id")
    )

    # ── Audit: log first edit ──────────────────────────────────
    try:
        token = get_user_token()
        if token:
            conn = get_connection(token)
            create_audit_table(conn)
            log_edit_start_once(
                conn, original_data, edited_data,
                st.session_state["edit_page"],
            )
    except Exception:
        pass

    # ── Save modal ───────────────────────────────────────────
    @st.dialog("Change Summary", width="medium")
    def save_change_modal():
        try:
            # ── Merge current page edits into full working copy ──────
            _full_sfe = st.session_state["edit_sfe_df"].copy()
            _page_edited = edited_data.reset_index()  # row_id back as column
            _before = _full_sfe.iloc[:si]
            _after = _full_sfe.iloc[ei:]
            _merged = pd.concat([_before, _page_edited, _after], ignore_index=True)

            # ── Full original (filtered same as edit_sfe_df) ─────────
            _full_orig = st.session_state["edit_data"].copy()
            if "test_status" in _full_orig.columns:
                _full_orig = _full_orig[
                    _full_orig["test_status"].astype(str).str.strip().str.lower()
                    != "complete"
                ].reset_index(drop=True)

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
            total_rows = len(_merged)
            st.info(
                f"No changes found across all pages "
                f"({total_rows} row{'s' if total_rows != 1 else ''} reviewed). "
                f"Edit cell values, add new rows, or tick \U0001f5d1 to mark "
                f"rows for deletion."
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
            st.write("Added Rows:")
            st.dataframe(insert_rows_df)
        if delete_rows_df is not None:
            st.write("Deleted Rows:")
            st.dataframe(delete_rows_df)
        if update_rows_df is not None:
            st.write("Updated Rows:")
            st.dataframe(update_rows_df)
            st.write("Updated Cells:")
            st.dataframe(diff)

        # ── Mandatory column validation (shown before save) ─────
        _mandatory_errors: list[str] = []
        if update_rows_df is not None:
            _mandatory_errors += validate_mandatory_cols(
                update_rows_df.reset_index(), MANDATORY_UPDATE_COLS,
            )
        if insert_rows_df is not None:
            _mandatory_errors += validate_mandatory_cols(
                insert_rows_df.reset_index(), MANDATORY_UPDATE_COLS,
            )

        if _mandatory_errors:
            st.error(
                f"\u26a0\ufe0f Cannot save: {len(_mandatory_errors)} row(s) have "
                f"missing mandatory columns "
                f"(**{', '.join(MANDATORY_UPDATE_COLS)}**):"
            )
            for _e in _mandatory_errors:
                st.warning(_e)

        scr1, scr2, _ = st.columns([1.5, 1.5, 3])
        with scr1:
            st.button("Confirm & Save", type="primary", key="confirm_save_btn",
                       disabled=bool(_mandatory_errors))
        with scr2:
            st.button("Discard Changes", type="primary", key="discard_changes_btn")

        if st.session_state.get("discard_changes_btn"):
            discard_all_changes()

        if st.session_state.get("confirm_save_btn"):
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
                            conn, update_rows_df, diff,
                        )
                        if err == 0:
                            st.success(f"[UPDATE] {update_rows_df.shape[0]} rows updated.")
                        else:
                            st.error(f"[UPDATE] failed: {err_list}")
            else:
                if added_count > 0 or deleted_count > 0:
                    st.info("No cell edits detected — processing inserts/deletes only.")
                else:
                    st.info("No row updates required on this page.")

            # 2) INSERT
            if added_count > 0:
                with st.spinner("Inserting rows..."):
                    ok, err, err_list, rid_list = bulk_insert(
                        conn, insert_rows_df,
                    )
                    if err == 0:
                        inserted_ids = rid_list
                        st.success(f"[INSERT] {ok} rows inserted (IDs: {rid_list}).")
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
                    page_no=st.session_state["edit_page"],
                    inserted_row_ids=inserted_ids,
                    deleted_row_ids=deleted_ids,
                )
                write_audit_events(conn, events)
                st.toast("Audit written", icon="\U0001f4dd")
            except Exception as ex:
                st.warning(f"Audit write skipped: {ex}")

            # 5) Re-fetch fresh data from DB and refresh page
            try:
                refresh_sql = (
                    f"SELECT * "
                    f"FROM {TABLE_FQN} ORDER BY row_id "
                    f"-- cache_buster: {uuid.uuid4()}"
                )
                raw = run_query(refresh_sql, tk)
                _safe_dtype = {k: v for k, v in SCHEMA_DTYPE.items() if k in raw.columns}
                edit_data = raw.astype(_safe_dtype)
                st.session_state["edit_data"] = edit_data
                st.session_state["edit_sfe_df"] = edit_data.copy()
                st.session_state["df_all"] = edit_data.copy()
                st.session_state["edit_total_pages"] = max(
                    1, math.ceil(len(edit_data) / PAGE_SIZE),
                )
            except Exception as ex:
                st.warning(f"Auto-refresh failed: {ex}")
            st.session_state["edit_key_counter"] = st.session_state.get("edit_key_counter", 0) + 1
            st.session_state["edit_active_filters"] = {}
            st.toast("Changes saved successfully!", icon="\u2705")
            time.sleep(1)
            st.rerun()

    if _can_edit and st.session_state.get("save_change_btn"):
        save_change_modal()

    # ── Bulk upload modal ──────────────────────────────────────
    @st.dialog("Bulk rows INSERT", width="medium")
    def bulk_upload_modal():
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
                              key="confirm_insert_btn")
                with bc2:
                    st.button("Discard upload", type="primary",
                              key="discard_upload_btn")

                if st.session_state.get("confirm_insert_btn"):
                    try:
                        with st.spinner("Inserting rows..."):
                            tk = get_user_token()
                            ok, err, err_list, rid_list = bulk_insert(
                                get_connection(tk), df_up,
                            )
                            if len(rid_list) == len(df_up):
                                st.success(
                                    f"[INSERT] {ok} rows inserted (IDs: {rid_list})."
                                )
                            if err:
                                st.error(f"Failed to insert {err} rows")
                                with st.expander("View Errors"):
                                    for e in err_list:
                                        st.write(e)
                    except Exception as _ex:
                        st.error(f"Bulk insert failed: {_ex}")
                if st.session_state.get("discard_upload_btn"):
                    st.rerun()

        with tab_copy:
            if "new_bulk_edit_df" not in st.session_state:
                empty = pd.DataFrame(
                    columns=list(UPLOAD_DTYPE.keys()),
                ).astype(UPLOAD_DTYPE)
                empty_row = pd.DataFrame(
                    [{c: None for c in UPLOAD_DTYPE}],
                ).astype(UPLOAD_DTYPE)
                st.session_state["new_bulk_edit_df"] = pd.concat(
                    [empty, empty_row], ignore_index=True,
                )

            b_edited = st.data_editor(
                data=st.session_state["new_bulk_edit_df"],
                height=70, use_container_width=True, hide_index=True,
                num_rows="fixed",
                disabled=["sp_test_id", "cl_test_id"],
                column_config=col_cfg,
            )

            bc1, bc2, _ = st.columns(3, vertical_alignment="bottom")
            with bc1:
                st.number_input(
                    "Copy row (xTimes)", min_value=1, max_value=999,
                    step=1, key="bei_nu",
                )
            with bc2:
                st.button(
                    "Insert duplicate rows", type="primary", key="bei_btn",
                )

    if _can_edit and st.session_state.get("bulk_upload_btn"):
        bulk_upload_modal()

    # ── Pagination ───────────────────────────────────────────
    def _go_page(delta: int):
        st.session_state["edit_page"] += delta
        st.session_state["edit_start_index"] = (
            (st.session_state["edit_page"] - 1) * PAGE_SIZE
        )
        st.session_state["edit_end_index"] = (
            st.session_state["edit_start_index"] + PAGE_SIZE
        )
        st.rerun()

    _total_rows = len(st.session_state.get("edit_sfe_df", []))
    _pg = st.session_state["edit_page"]
    _tp = st.session_state["edit_total_pages"]

    pg_prev, pg_lbl, pg_input, pg_of, pg_next = st.columns(
        [1, 0.5, 0.6, 2, 1], vertical_alignment="center",
    )
    with pg_prev:
        if st.button("Previous", use_container_width=True, disabled=_pg <= 1):
            _go_page(-1)
    with pg_lbl:
        st.markdown(
            "<div style='text-align:right; font-weight:600; white-space:nowrap;'>Page</div>",
            unsafe_allow_html=True,
        )
    with pg_input:
        go_page_num = st.number_input(
            "Go to page", min_value=1, max_value=_tp,
            value=_pg, step=1, key="edit_go_page_input",
            label_visibility="collapsed",
        )
        if go_page_num != _pg:
            st.session_state["edit_page"] = go_page_num
            st.session_state["edit_start_index"] = (go_page_num - 1) * PAGE_SIZE
            st.session_state["edit_end_index"] = st.session_state["edit_start_index"] + PAGE_SIZE
            st.rerun()
    with pg_of:
        st.markdown(
            f"<div style='font-weight:600; white-space:nowrap;'>"
            f"of {_tp} &nbsp;|&nbsp; {_total_rows:,} rows total</div>",
            unsafe_allow_html=True,
        )
    with pg_next:
        if st.button("Next", use_container_width=True, disabled=_pg >= _tp):
            _go_page(1)

else:
    # ── Auto-fetch on page load ──────────────────────────────
    try:
        with st.spinner(f"Loading {TABLE_FQN} from Databricks..."):
            fetch_sql = (
                f"SELECT * "
                f"FROM {TABLE_FQN} ORDER BY row_id "
                f"-- cache_buster: {uuid.uuid4()}"
            )
            token = get_user_token()
            raw = run_query(fetch_sql, token)
            _safe_dtype = {k: v for k, v in SCHEMA_DTYPE.items() if k in raw.columns}
            edit_data = raw.astype(_safe_dtype)

            st.session_state["edit_data"] = edit_data
            st.session_state["edit_sfe_df"] = edit_data.copy()
            st.session_state["df_all"] = edit_data.copy()
            st.session_state["edit_page_size"] = PAGE_SIZE
            st.session_state["edit_total_pages"] = max(
                1, math.ceil(len(edit_data) / PAGE_SIZE),
            )

        if edit_data.empty:
            st.info("Table loaded successfully but returned **0 rows**.")
        else:
            st.session_state["edit_init_data_fetch"] = True
            st.rerun()
    except Exception as ex:
        st.error(f"Failed to load table: {ex}")
