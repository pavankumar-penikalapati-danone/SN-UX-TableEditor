# ============================================================
# 5_Editor_Dummy.py  –  SN UX Editor Dummy (Page 5)
# ============================================================
# Similar to Page 2, with cascading zone → CBU dropdown
# powered by macro_zone_cbu_bu_ru_2025 lookup table.
# Zone context selector controls CBU options in the grid
# for BOTH new rows and updates to existing rows.
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
_HIDDEN_DISPLAY_COLS = {"ingestion_timestamp", "Unnamed__64", "Unnamed__65",
                        "Unnamed__66", "Unnamed__71", "Unnamed__72", "Unnamed__73"}

# ── Macro Zone / CBU lookup table ────────────────────────────
MACRO_ZONE_TABLE = f"{CATALOG}.{SCHEMA}.macro_zone_cbu_bu_ru_2025"


@st.cache_data(ttl=600, show_spinner=False)
def load_macro_zone_lookup() -> pd.DataFrame:
    """Load the zone -> CBU -> BU -> RU mapping table (cached 10 min)."""
    try:
        token = get_user_token()
        df = run_query(
            f"SELECT macro_zone_new, cbu, bu, ru "
            f"FROM {MACRO_ZONE_TABLE} ORDER BY macro_zone_new, cbu",
            token,
        )
        return df
    except Exception as ex:
        st.warning(f"Could not load macro-zone lookup: {ex}")
        return pd.DataFrame(columns=["macro_zone_new", "cbu", "bu", "ru"])


# ═════════════════════════════════════════════════════════════
#  DISCARD CHANGES  (page-specific session keys)
# ═════════════════════════════════════════════════════════════

def discard_all_changes():
    """Reset editable data to original without touching widget keys."""
    try:
        if "dum_data" not in st.session_state:
            st.warning("No data loaded. Nothing to discard.")
            return
        st.session_state["dum_sfe_df"] = st.session_state["dum_data"].copy()
        st.session_state["dum_key_counter"] = st.session_state.get("dum_key_counter", 0) + 1
        if "dum_page" in st.session_state:
            start = (st.session_state["dum_page"] - 1) * PAGE_SIZE
            st.session_state["dum_start_index"] = start
            st.session_state["dum_end_index"] = start + PAGE_SIZE
        for flag in ("dum_start_logged", "dum_filter_triggered",
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
    page_title="SN UX Editor Dummy",
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
_admin_users = [u.strip().lower() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()]
if _current_user.lower() not in _admin_users:
    st.markdown(
        '<style>[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"] '
        "{ display: none !important; }</style>",
        unsafe_allow_html=True,
    )


# ═════════════════════════════════════════════════════════════
#  LOAD MACRO-ZONE LOOKUP (for cascading dropdowns)
# ═════════════════════════════════════════════════════════════

_macro_zone_df = load_macro_zone_lookup()
_zone_list = sorted(_macro_zone_df["macro_zone_new"].dropna().unique().tolist()) if not _macro_zone_df.empty else []
_all_cbu_list = sorted(_macro_zone_df["cbu"].dropna().unique().tolist()) if not _macro_zone_df.empty else []


def _get_cbu_for_zone(zone: str) -> list[str]:
    """Return CBU options filtered by the given zone."""
    if not zone or _macro_zone_df.empty:
        return _all_cbu_list
    rows = _macro_zone_df[_macro_zone_df["macro_zone_new"] == zone]
    return sorted(rows["cbu"].dropna().unique().tolist())


def _get_bu_for_zone_cbu(zone: str, cbu: str) -> list[str]:
    """Return BU options filtered by zone + CBU."""
    if not zone or not cbu or _macro_zone_df.empty:
        return []
    rows = _macro_zone_df[
        (_macro_zone_df["macro_zone_new"] == zone) &
        (_macro_zone_df["cbu"] == cbu)
    ]
    return sorted(rows["bu"].dropna().unique().tolist())


def _get_ru_for_zone_cbu_bu(zone: str, cbu: str, bu: str) -> list[str]:
    """Return RU options filtered by zone + CBU + BU."""
    if not zone or not cbu or not bu or _macro_zone_df.empty:
        return []
    rows = _macro_zone_df[
        (_macro_zone_df["macro_zone_new"] == zone) &
        (_macro_zone_df["cbu"] == cbu) &
        (_macro_zone_df["bu"] == bu)
    ]
    return sorted(rows["ru"].dropna().unique().tolist())


# ═════════════════════════════════════════════════════════════
#  PAGE CONTENT
# ═════════════════════════════════════════════════════════════

if "dum_data" in st.session_state:

    # ── Pagination init ────────────────────────────────────────
    if "dum_page" not in st.session_state:
        st.session_state["dum_page"] = 1
        st.session_state["dum_start_index"] = 0
        st.session_state["dum_end_index"] = PAGE_SIZE
    if "dum_key_counter" not in st.session_state:
        st.session_state["dum_key_counter"] = 0

    # ── Sidebar filters (fixed + dynamic column selection) ──────
    with st.sidebar:
        if st.session_state.get("dum_init_data_fetch"):
            st.header("Filters")

            def _dum_filter_toggle(col_key):
                """ALL / specific-value mutual exclusion."""
                def _on_change():
                    sel = st.session_state[col_key]
                    if "ALL" in sel and len(sel) > 1 and sel[0] == "ALL":
                        st.session_state[col_key] = [v for v in sel if v != "ALL"]
                    elif ("ALL" in sel and sel[-1] == "ALL") or len(sel) == 0:
                        st.session_state[col_key] = ["ALL"]
                return _on_change

            _dum_raw = st.session_state["dum_data"]
            dum_filters: dict[str, list] = {}

            # ── Fixed filters (always visible from FILTER_COLUMNS) ───
            for col in FILTER_COLUMNS:
                if col not in _dum_raw.columns or col in _HIDDEN_DISPLAY_COLS:
                    continue
                raw_vals = _dum_raw[col].dropna().unique()
                if pd.api.types.is_numeric_dtype(_dum_raw[col]):
                    unique_vals = [str(v) for v in sorted(raw_vals)]
                else:
                    unique_vals = sorted([str(v) for v in raw_vals])
                selected_vals = st.multiselect(
                    f"Select {col}:",
                    options=["ALL"] + unique_vals,
                    default=["ALL"],
                    key=f"dum_flt_{col}",
                    on_change=_dum_filter_toggle(f"dum_flt_{col}"),
                )
                if selected_vals and "ALL" not in selected_vals:
                    dum_filters[col] = selected_vals

            # ── Dynamic filters (additional columns) ─────────────────
            extra_cols = [c for c in _dum_raw.columns if c not in FILTER_COLUMNS and c not in _HIDDEN_DISPLAY_COLS]
            selected_filter_cols = st.multiselect(
                "Filter by columns:",
                options=extra_cols,
                default=[],
                key="dum_filter_cols",
            )

            for col in selected_filter_cols:
                raw_vals = _dum_raw[col].dropna().unique()
                if pd.api.types.is_numeric_dtype(_dum_raw[col]):
                    unique_vals = [str(v) for v in sorted(raw_vals)]
                else:
                    unique_vals = sorted([str(v) for v in raw_vals])
                selected_vals = st.multiselect(
                    f"Select {col}:",
                    options=["ALL"] + unique_vals,
                    default=["ALL"],
                    key=f"dum_flt_{col}",
                    on_change=_dum_filter_toggle(f"dum_flt_{col}"),
                )
                if selected_vals and "ALL" not in selected_vals:
                    dum_filters[col] = selected_vals

            # ── Apply / Clear buttons ────────────────────────────────
            fc1, fc2 = st.columns(2)
            with fc1:
                filter_btn = st.button(
                    "Apply Filters", type="primary",
                    use_container_width=True, key="dum_apply_filters",
                )
            with fc2:
                clear_btn = st.button(
                    "Clear Filters", use_container_width=True,
                    key="dum_clear_filters",
                )

            if filter_btn:
                st.session_state["dum_active_filters"] = dum_filters
                st.session_state["dum_page"] = 1
                st.session_state["dum_start_index"] = 0
                st.session_state["dum_end_index"] = PAGE_SIZE
                st.rerun()

            if clear_btn:
                st.session_state["dum_active_filters"] = {}
                st.session_state["dum_sfe_df"] = st.session_state["dum_data"].copy()
                st.session_state["dum_key_counter"] = st.session_state.get("dum_key_counter", 0) + 1
                st.session_state["dum_page"] = 1
                st.session_state["dum_start_index"] = 0
                st.session_state["dum_end_index"] = PAGE_SIZE
                st.session_state["dum_total_pages"] = max(
                    1, math.ceil(len(st.session_state["dum_data"]) / PAGE_SIZE),
                )
                st.rerun()

    # ── Title bar ────────────────────────────────────────────
    rc1, rc2, rc3 = st.columns([4, 1, 1], vertical_alignment="bottom")
    with rc1:
        st.markdown(
            "<h2 style='font-size:28px; font-weight:700; margin-bottom:5px;'>"
            "[EDIT DUMMY] SN UX CONSO Master Table</h2>",
            unsafe_allow_html=True,
        )
        if not _can_edit:
            st.caption("\U0001f512 Read-only mode \u2013 you are not in the editor group")
    with rc2:
        if _can_edit:
            st.button("Save changes", type="primary", key="dum_save_change_btn",
                      use_container_width=True)
    with rc3:
        if _can_edit:
            st.button("\u2795 Add Row", type="primary", key="dum_add_row_btn",
                      use_container_width=True)

    # ═════════════════════════════════════════════════════════
    #  ZONE CONTEXT SELECTOR  –  controls CBU dropdown in grid
    # ═════════════════════════════════════════════════════════
    st.markdown(
        "<div style='background:#f0f2f6; padding:10px 16px; border-radius:8px; "
        "margin-bottom:8px;'>",
        unsafe_allow_html=True,
    )
    zc1, zc2, zc3 = st.columns([2, 2, 2], vertical_alignment="bottom")
    with zc1:
        _ctx_zone = st.selectbox(
            "\U0001f30d **Select Zone** (filters CBU dropdown in grid):",
            options=["-- All Zones --"] + _zone_list,
            index=0,
            key="dum_ctx_zone",
            help="Choose a Macro Zone to narrow the CBU dropdown options "
                 "in the table below. Applies to both new and existing rows.",
        )
    # Derive CBU options based on zone context
    if _ctx_zone and _ctx_zone != "-- All Zones --":
        _ctx_cbu_options = _get_cbu_for_zone(_ctx_zone)
    else:
        _ctx_cbu_options = _all_cbu_list

    with zc2:
        st.markdown(
            f"<div style='padding-top:8px;'>"
            f"\U0001f4cb <b>CBU options available:</b> {len(_ctx_cbu_options)}"
            f"</div>",
            unsafe_allow_html=True,
        )
    with zc3:
        if _ctx_zone and _ctx_zone != "-- All Zones --":
            st.caption(f"CBUs: {', '.join(_ctx_cbu_options[:8])}"
                       + (f" ... +{len(_ctx_cbu_options)-8} more" if len(_ctx_cbu_options) > 8 else ""))
        else:
            st.caption("Showing all CBUs from lookup table")
    st.markdown("</div>", unsafe_allow_html=True)

    # ── Sort form ────────────────────────────────────────────
    with st.form("dum_sort_form", clear_on_submit=False):
        sc1, sc2, sc3 = st.columns([2.5, 2.5, 1], vertical_alignment="bottom")
        with sc1:
            sort_col = st.selectbox(
                "Column name", options=[c for c in st.session_state["dum_data"].columns if c not in _HIDDEN_DISPLAY_COLS],
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
    active_filters = st.session_state.get("dum_active_filters", {})
    if active_filters:
        try:
            df_filtered = st.session_state["dum_data"].copy()
            for col, vals in active_filters.items():
                if col in df_filtered.columns:
                    df_filtered = df_filtered[df_filtered[col].astype(str).isin(vals)]
            st.session_state["dum_sfe_df"] = df_filtered.reset_index(drop=True)
            st.session_state["dum_total_pages"] = max(
                1, math.ceil(len(st.session_state["dum_sfe_df"]) / PAGE_SIZE),
            )
        except Exception as _ex:
            st.warning(f"Filter application failed: {_ex}")

    # ── Apply sort ───────────────────────────────────────────
    if sort_submitted:
        try:
            st.session_state["dum_sfe_df"] = (
                st.session_state["dum_sfe_df"]
                .sort_values(by=sort_col, ascending=(sort_ord == "ascending"))
                .reset_index(drop=True)
            )
        except Exception as _ex:
            st.warning(f"Sort failed: {_ex}")

    # ── Hide completed rows (test_status != 'complete') ─────
    sfe = st.session_state["dum_sfe_df"]
    try:
        sfe = sfe[
            sfe["test_status"].astype(str).str.strip().str.lower() != "complete"
        ].reset_index(drop=True)
    except Exception:
        pass
    st.session_state["dum_sfe_df"] = sfe
    st.session_state["dum_total_pages"] = max(
        1, math.ceil(len(sfe) / PAGE_SIZE),
    )

    # ── Slice page ───────────────────────────────────────────
    si = st.session_state["dum_start_index"]
    ei = st.session_state["dum_end_index"]

    original_data = (
        sfe.iloc[si:ei]
        .loc[lambda d: d["row_id"].notna()]
        .sort_values("row_id")
        .reset_index(drop=True)
        .set_index("row_id")
    )

    # ── Page slice with select column ─────────────────────────
    page_slice = sfe.iloc[si:ei].copy()
    page_slice.insert(0, "_select", False)
    page_slice.insert(1, "_clr", page_slice["row_id"].apply(
        lambda x: "\U0001f7e1" if pd.isna(x) else ""
    ))

    col_cfg = build_column_config(
        st.session_state["dum_data"], dropdown_required=True,
    )
    col_cfg["_select"] = st.column_config.CheckboxColumn(
        "\u2795", width="small", pinned=True,
        help="Tick rows to copy below",
    )
    col_cfg["_clr"] = st.column_config.TextColumn(
        " ", width=35, pinned=True, disabled=True,
    )

    # ── Override zone & CBU column configs with lookup values ──
    # zone column: always shows all macro_zone_new values
    if _zone_list:
        col_cfg["zone"] = st.column_config.SelectboxColumn(
            "zone", options=_zone_list, required=False,
        )
    # CBU column: filtered by the zone context selector above
    if _ctx_cbu_options:
        col_cfg["CBU"] = st.column_config.SelectboxColumn(
            "CBU", options=_ctx_cbu_options, required=False,
        )

    edited_data = st.data_editor(
        data=page_slice,
        use_container_width=True, hide_index=True,
        num_rows="dynamic" if _can_edit else "fixed",
        key=f"dum_grid_{st.session_state.get('dum_key_counter', 0)}",
        disabled=(
            get_disabled_columns_by_group(_current_user, list(page_slice.columns)) + ["_clr"]
            if _can_edit
            else True
        ),
        column_config=col_cfg,
        column_order=["_select", "_clr"] + [
            c for c in page_slice.columns if c not in ("_select", "_clr") and c not in _HIDDEN_DISPLAY_COLS
        ],
    )

    # ── Handle copy-row-below action ──────────────────────────
    selected_rows = edited_data[edited_data["_select"] == True]

    if not selected_rows.empty and _can_edit:
        cr1, cr2, _ = st.columns([1.5, 1.5, 3])
        with cr1:
            copy_btn = st.button(
                f"\u2795 Copy {len(selected_rows)} row(s) below",
                type="primary", key="dum_apply_copy_btn",
            )
        with cr2:
            edit_btn = st.button(
                f"\u270f\ufe0f Edit selected row",
                type="secondary", key="dum_edit_row_btn",
            )

        if copy_btn:
            try:
                current_sfe = st.session_state["dum_sfe_df"]
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
                st.session_state["dum_sfe_df"] = result
                st.session_state["dum_total_pages"] = max(
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
                st.session_state["dum_page"],
            )
    except Exception:
        pass

    # ── Save modal ───────────────────────────────────────────
    @st.dialog("Change Summary", width="medium")
    def dum_save_change_modal():
        try:
            _full_sfe = st.session_state["dum_sfe_df"].copy()
            _page_edited = edited_data.reset_index()
            _before = _full_sfe.iloc[:si]
            _after = _full_sfe.iloc[ei:]
            _merged = pd.concat([_before, _page_edited, _after], ignore_index=True)

            _full_orig = st.session_state["dum_data"].copy()
            if "test_status" in _full_orig.columns:
                _full_orig = _full_orig[
                    _full_orig["test_status"].astype(str).str.strip().str.lower()
                    != "complete"
                ].reset_index(drop=True)

            _new_rows = _merged[_merged["row_id"].isna()]
            _existing = (
                _merged[_merged["row_id"].notna()]
                .sort_values("row_id").reset_index(drop=True).set_index("row_id")
            )
            _orig_indexed = (
                _full_orig[_full_orig["row_id"].notna()]
                .sort_values("row_id").reset_index(drop=True).set_index("row_id")
            )

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
            st.button("Confirm & Save", type="primary", key="dum_confirm_save_btn",
                       disabled=bool(_mandatory_errors))
        with scr2:
            st.button("Discard Changes", type="primary", key="dum_discard_changes_btn")

        if st.session_state.get("dum_discard_changes_btn"):
            discard_all_changes()

        if st.session_state.get("dum_confirm_save_btn"):
            tk = get_user_token()
            conn = get_connection(tk)
            create_audit_table(conn)

            inserted_ids, deleted_ids = [], []

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
                    st.info("No cell edits detected \u2014 processing inserts/deletes only.")
                else:
                    st.info("No row updates required on this page.")

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

            try:
                events = build_audit_events(
                    original_df=_orig_indexed,
                    edited_df=_existing,
                    added_rows_df=insert_rows_df,
                    deleted_rows_df=delete_rows_df,
                    diff_df=diff if updated_count > 0 else None,
                    page_no=st.session_state["dum_page"],
                    inserted_row_ids=inserted_ids,
                    deleted_row_ids=deleted_ids,
                )
                write_audit_events(conn, events)
                st.toast("Audit written", icon="\U0001f4dd")
            except Exception as ex:
                st.warning(f"Audit write skipped: {ex}")

            try:
                refresh_sql = (
                    f"SELECT * "
                    f"FROM {TABLE_FQN} ORDER BY row_id "
                    f"-- cache_buster: {uuid.uuid4()}"
                )
                raw = run_query(refresh_sql, tk)
                _safe_dtype = {k: v for k, v in SCHEMA_DTYPE.items() if k in raw.columns}
                dum_data = raw.astype(_safe_dtype)
                st.session_state["dum_data"] = dum_data
                st.session_state["dum_sfe_df"] = dum_data.copy()
                st.session_state["dum_total_pages"] = max(
                    1, math.ceil(len(dum_data) / PAGE_SIZE),
                )
            except Exception as ex:
                st.warning(f"Auto-refresh failed: {ex}")
            st.session_state["dum_key_counter"] = st.session_state.get("dum_key_counter", 0) + 1
            st.session_state["dum_active_filters"] = {}
            st.toast("Changes saved successfully!", icon="\u2705")
            time.sleep(1)
            st.rerun()

    if _can_edit and st.session_state.get("dum_save_change_btn"):
        dum_save_change_modal()

    # ═════════════════════════════════════════════════════════
    #  ADD ROW MODAL  –  cascading zone -> CBU -> BU -> RU
    # ═════════════════════════════════════════════════════════

    @st.dialog("\u2795 Add New Row", width="large")
    def add_row_modal():
        st.markdown("#### Zone / CBU Mapping")
        st.caption("Select a Macro Zone first \u2014 CBU, BU, RU options update automatically.")

        az1, az2 = st.columns(2)

        with az1:
            selected_zone = st.selectbox(
                "New Zone (Macro Zone):",
                options=[""] + _zone_list,
                index=0,
                key="dum_add_zone",
                help="Maps to macro_zone_new in lookup table",
            )

        _add_cbu_options = _get_cbu_for_zone(selected_zone) if selected_zone else []

        with az2:
            selected_cbu = st.selectbox(
                "CBU:",
                options=[""] + _add_cbu_options,
                index=0,
                key="dum_add_cbu",
                help="Filtered by selected zone",
            )

        az3, az4 = st.columns(2)

        _add_bu_options = _get_bu_for_zone_cbu(selected_zone, selected_cbu) if selected_zone and selected_cbu else []

        with az3:
            selected_bu = st.selectbox(
                "BU:",
                options=[""] + _add_bu_options,
                index=0,
                key="dum_add_bu",
                help="Filtered by selected CBU",
            )

        _add_ru_options = _get_ru_for_zone_cbu_bu(selected_zone, selected_cbu, selected_bu) if selected_zone and selected_cbu and selected_bu else []

        with az4:
            selected_ru = st.selectbox(
                "RU:",
                options=[""] + _add_ru_options,
                index=0,
                key="dum_add_ru",
                help="Filtered by selected BU",
            )

        st.divider()
        st.markdown("#### Other Fields")

        _skip_cols = {"row_id", "ingestion_timestamp", "zone", "CBU",
                      "sp_test_id", "cl_test_id"} | _HIDDEN_DISPLAY_COLS
        _other_cols = [c for c in DML_COLUMNS if c not in _skip_cols]

        field_values: dict[str, str | None] = {}
        cols_per_row = 3
        for i in range(0, len(_other_cols), cols_per_row):
            row_cols = st.columns(cols_per_row)
            for j, col_name in enumerate(_other_cols[i : i + cols_per_row]):
                with row_cols[j]:
                    if col_name in DROPDOWN_COLS:
                        raw_vals = st.session_state["dum_data"][col_name].dropna().unique() if col_name in st.session_state["dum_data"].columns else []
                        opts = dropdown_options(col_name, list(raw_vals))
                        field_values[col_name] = st.selectbox(
                            f"{col_name}:", options=[""] + opts,
                            key=f"dum_add_{col_name}",
                        )
                    else:
                        field_values[col_name] = st.text_input(
                            f"{col_name}:", value="",
                            key=f"dum_add_{col_name}",
                        )

        st.divider()

        ac1, ac2, _ = st.columns([1.5, 1.5, 3])
        with ac1:
            add_confirm = st.button("Add Row", type="primary", key="dum_add_confirm")
        with ac2:
            add_cancel = st.button("Cancel", key="dum_add_cancel")

        if add_confirm:
            if not selected_zone or not selected_cbu:
                st.error("Please select at least a Zone and CBU.")
                return

            new_row = {c: pd.NA for c in st.session_state["dum_data"].columns}
            new_row["row_id"] = pd.NA
            new_row["zone"] = selected_zone
            new_row["CBU"] = selected_cbu
            for col_name, val in field_values.items():
                if val:
                    new_row[col_name] = val

            new_df = pd.DataFrame([new_row])
            _safe_dtype = {k: v for k, v in SCHEMA_DTYPE.items() if k in new_df.columns}
            new_df = new_df.astype(_safe_dtype)

            st.session_state["dum_sfe_df"] = pd.concat(
                [st.session_state["dum_sfe_df"], new_df], ignore_index=True,
            )
            st.session_state["dum_total_pages"] = max(
                1, math.ceil(len(st.session_state["dum_sfe_df"]) / PAGE_SIZE),
            )
            st.session_state["dum_page"] = st.session_state["dum_total_pages"]
            st.session_state["dum_start_index"] = (st.session_state["dum_page"] - 1) * PAGE_SIZE
            st.session_state["dum_end_index"] = st.session_state["dum_start_index"] + PAGE_SIZE
            st.session_state["dum_key_counter"] = st.session_state.get("dum_key_counter", 0) + 1
            st.toast("New row added \u2014 review and save when ready", icon="\u2795")
            st.rerun()

        if add_cancel:
            st.rerun()

    if _can_edit and st.session_state.get("dum_add_row_btn"):
        add_row_modal()

    # ═════════════════════════════════════════════════════════
    #  EDIT ROW MODAL  –  cascading zone -> CBU -> BU -> RU
    # ═════════════════════════════════════════════════════════

    @st.dialog("\u270f\ufe0f Edit Selected Row", width="large")
    def edit_row_modal():
        _checked_idxs = [
            i for i, row in page_slice.iterrows()
            if row.get("_select", False)
        ]

        if len(_checked_idxs) == 0:
            st.warning("No row selected. Please check the \u2795 box next to a row first.")
            return
        if len(_checked_idxs) > 1:
            st.warning("Please select only ONE row to edit.")
            return

        _row_idx = _checked_idxs[0]
        _row_data = sfe.iloc[_row_idx].to_dict()
        _row_id = _row_data.get("row_id")

        st.info(
            f"Editing row at index {_row_idx}"
            + (f" (row_id: {int(_row_id)})" if pd.notna(_row_id) else " (new row)")
        )

        st.markdown("#### Zone / CBU Mapping")
        st.caption("Select a Macro Zone \u2014 CBU, BU, RU options update automatically.")

        # ── Pre-fill current values ──────────────────────────
        current_zone = str(_row_data.get("zone", "")) if pd.notna(_row_data.get("zone")) else ""
        current_cbu = str(_row_data.get("CBU", "")) if pd.notna(_row_data.get("CBU")) else ""

        ez1, ez2 = st.columns(2)

        with ez1:
            zone_idx = 0
            if current_zone and current_zone in _zone_list:
                zone_idx = _zone_list.index(current_zone) + 1
            selected_zone = st.selectbox(
                "Zone (Macro Zone):",
                options=[""] + _zone_list,
                index=zone_idx,
                key="dum_edit_zone",
                help="Maps to macro_zone_new in lookup table",
            )

        _edit_cbu_options = _get_cbu_for_zone(selected_zone) if selected_zone else _all_cbu_list

        with ez2:
            cbu_idx = 0
            if current_cbu and current_cbu in _edit_cbu_options:
                cbu_idx = _edit_cbu_options.index(current_cbu) + 1
            selected_cbu = st.selectbox(
                "CBU:",
                options=[""] + _edit_cbu_options,
                index=cbu_idx,
                key="dum_edit_cbu",
                help="Filtered by selected zone",
            )

        ez3, ez4 = st.columns(2)

        _edit_bu_options = _get_bu_for_zone_cbu(selected_zone, selected_cbu) if selected_zone and selected_cbu else []

        with ez3:
            selected_bu = st.selectbox(
                "BU:",
                options=[""] + _edit_bu_options,
                index=0,
                key="dum_edit_bu",
                help="Filtered by selected CBU",
            )

        _edit_ru_options = _get_ru_for_zone_cbu_bu(selected_zone, selected_cbu, selected_bu) if selected_zone and selected_cbu and selected_bu else []

        with ez4:
            selected_ru = st.selectbox(
                "RU:",
                options=[""] + _edit_ru_options,
                index=0,
                key="dum_edit_ru",
                help="Filtered by selected BU",
            )

        st.divider()
        st.markdown("#### Other Fields")

        _skip_cols = {"row_id", "ingestion_timestamp", "zone", "CBU",
                      "sp_test_id", "cl_test_id"} | _HIDDEN_DISPLAY_COLS
        _other_cols = [c for c in DML_COLUMNS if c not in _skip_cols]

        field_values: dict[str, str | None] = {}
        cols_per_row = 3
        for i in range(0, len(_other_cols), cols_per_row):
            row_cols = st.columns(cols_per_row)
            for j, col_name in enumerate(_other_cols[i : i + cols_per_row]):
                with row_cols[j]:
                    current_val = _row_data.get(col_name)
                    current_str = str(current_val) if pd.notna(current_val) else ""

                    if col_name in DROPDOWN_COLS:
                        raw_vals = st.session_state["dum_data"][col_name].dropna().unique() if col_name in st.session_state["dum_data"].columns else []
                        opts = dropdown_options(col_name, list(raw_vals))
                        opt_idx = 0
                        if current_str and current_str in opts:
                            opt_idx = opts.index(current_str) + 1
                        field_values[col_name] = st.selectbox(
                            f"{col_name}:", options=[""] + opts,
                            index=opt_idx,
                            key=f"dum_edit_{col_name}",
                        )
                    else:
                        field_values[col_name] = st.text_input(
                            f"{col_name}:", value=current_str,
                            key=f"dum_edit_{col_name}",
                        )

        st.divider()

        ec1, ec2, _ = st.columns([1.5, 1.5, 3])
        with ec1:
            edit_confirm = st.button("Apply Changes", type="primary", key="dum_edit_confirm")
        with ec2:
            edit_cancel = st.button("Cancel", key="dum_edit_cancel")

        if edit_confirm:
            if not selected_zone or not selected_cbu:
                st.error("Please select at least a Zone and CBU.")
                return

            current_sfe = st.session_state["dum_sfe_df"].copy()
            current_sfe.at[_row_idx, "zone"] = selected_zone
            current_sfe.at[_row_idx, "CBU"] = selected_cbu

            for col_name, val in field_values.items():
                if val:
                    current_sfe.at[_row_idx, col_name] = val
                else:
                    current_sfe.at[_row_idx, col_name] = pd.NA

            st.session_state["dum_sfe_df"] = current_sfe
            st.session_state["dum_key_counter"] = st.session_state.get("dum_key_counter", 0) + 1
            st.toast("Row updated \u2014 review and save when ready", icon="\u270f\ufe0f")
            st.rerun()

        if edit_cancel:
            st.rerun()

    if _can_edit and st.session_state.get("dum_edit_row_btn"):
        edit_row_modal()

    # ── Pagination ───────────────────────────────────────────
    def _dum_go_page(delta: int):
        st.session_state["dum_page"] += delta
        st.session_state["dum_start_index"] = (
            (st.session_state["dum_page"] - 1) * PAGE_SIZE
        )
        st.session_state["dum_end_index"] = (
            st.session_state["dum_start_index"] + PAGE_SIZE
        )
        st.rerun()

    _total_rows = len(st.session_state.get("dum_sfe_df", []))
    _pg = st.session_state["dum_page"]
    _tp = st.session_state["dum_total_pages"]

    pg_prev, pg_lbl, pg_input, pg_of, pg_next = st.columns(
        [1, 0.5, 0.6, 2, 1], vertical_alignment="center",
    )
    with pg_prev:
        if st.button("Previous", use_container_width=True, disabled=_pg <= 1):
            _dum_go_page(-1)
    with pg_lbl:
        st.markdown(
            "<div style='text-align:right; font-weight:600; white-space:nowrap;'>Page</div>",
            unsafe_allow_html=True,
        )
    with pg_input:
        go_page_num = st.number_input(
            "Go to page", min_value=1, max_value=_tp,
            value=_pg, step=1, key="dum_go_page_input",
            label_visibility="collapsed",
        )
        if go_page_num != _pg:
            st.session_state["dum_page"] = go_page_num
            st.session_state["dum_start_index"] = (go_page_num - 1) * PAGE_SIZE
            st.session_state["dum_end_index"] = st.session_state["dum_start_index"] + PAGE_SIZE
            st.rerun()
    with pg_of:
        st.markdown(
            f"<div style='font-weight:600; white-space:nowrap;'>"
            f"of {_tp} &nbsp;|&nbsp; {_total_rows:,} rows total</div>",
            unsafe_allow_html=True,
        )
    with pg_next:
        if st.button("Next", use_container_width=True, disabled=_pg >= _tp):
            _dum_go_page(1)

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
            dum_data = raw.astype(_safe_dtype)

            st.session_state["dum_data"] = dum_data
            st.session_state["dum_sfe_df"] = dum_data.copy()
            st.session_state["dum_page_size"] = PAGE_SIZE
            st.session_state["dum_total_pages"] = max(
                1, math.ceil(len(dum_data) / PAGE_SIZE),
            )

        if dum_data.empty:
            st.info("Table loaded successfully but returned **0 rows**.")
        else:
            st.session_state["dum_init_data_fetch"] = True
            st.rerun()
    except Exception as ex:
        st.error(f"Failed to load table: {ex}")
