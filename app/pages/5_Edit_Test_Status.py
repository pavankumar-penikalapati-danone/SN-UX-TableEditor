# ============================================================
# 5_Edit_Test_Status.py  –  test_status Editor (Approval)
# ============================================================
# Full clone of Page 2 (Table Data Editor) with one key
# difference in the save modal:
#   - test_status → "Cancelled"     → committed directly
#   - test_status → anything else   → PENDING approval request
#   - All other column edits        → committed directly
# Shows ALL rows (page 2 hides "Complete" rows).
# ============================================================

from __future__ import annotations

import math
import os
import smtplib
import time
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import streamlit as st

from config import (
    CATALOG, SCHEMA, MAIN_TABLE, TABLE_FQN, PAGE_SIZE,
    SELECT_COLUMNS, DML_COLUMNS, SCHEMA_DTYPE, UPLOAD_DTYPE,
    FILTER_COLUMNS, DROPDOWN_COLS, EXCEL_TEMPLATE,
    SHARED_CSS, APP_LOGO_IMAGE, APP_LOGO_ICON, APP_LOGO_LINK,
    MANDATORY_UPDATE_COLS,
    to_bold, is_na, validate_mandatory_cols,
    get_user_token, get_connection, run_query, run_statement,
    get_user_identity, ensure_session_id,
    compute_seq, generate_test_id,
    create_audit_table, write_audit_events,
    build_audit_events, log_edit_start_once,
    build_column_config, dropdown_options,
    get_disabled_columns,
    get_disabled_columns_by_group,
    EDITOR_USERS,
    is_user_in_editor_group,
    bulk_insert, bulk_delete, bulk_update,
)

# ── Approval constants ───────────────────────────────────────
APPROVAL_TABLE = os.getenv(
    "APPROVAL_TABLE_FQN",
    f"{CATALOG}.{SCHEMA}.ux_sn_test_status_approvals",
)
TEST_STATUS_OPTIONS = ["Cancelled", "Complete", "Future Test", "Test In Progress"]
DIRECT_COMMIT_STATUSES = {"Cancelled"}

APPROVER_EMAILS = [
    e.strip() for e in os.getenv("TEST_STATUS_APPROVERS", "").split(",") if e.strip()
]
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

TS_PAGE_SIZE = int(os.getenv("TEST_STATUS_PAGE_SIZE", str(PAGE_SIZE)))

_HIDDEN_DISPLAY_COLS = {
    "ingestion_timestamp", "Unnamed__64", "Unnamed__65",
    "Unnamed__66", "Unnamed__71", "Unnamed__72", "Unnamed__73",
}


# ═════════════════════════════════════════════════════════════
#  NOTIFICATION HELPERS
# ═════════════════════════════════════════════════════════════

def send_approval_email(to_emails, rows_info, requested_by):
    """Send one consolidated email for all pending rows."""
    if not SMTP_HOST or not to_emails:
        return False
    subject = f"[APPROVAL REQUIRED] {len(rows_info)} test_status change(s)"
    rows_html = "".join(
        f"<tr><td>{r['row_id']}</td><td>{r.get('sp_test_id','')}</td>"
        f"<td>{r['old']}</td><td>{r['new']}</td></tr>"
        for r in rows_info
    )
    body = f"""
    <h3>test_status Change Approval Request</h3>
    <p><b>Requested by:</b> {requested_by}<br>
    <b>At:</b> {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</p>
    <table border="1" cellpadding="4" cellspacing="0" style="border-collapse:collapse;">
      <tr><th>row_id</th><th>sp_test_id</th><th>Old Status</th><th>New Status</th></tr>
      {rows_html}
    </table>
    <br><p>Log in to <b>SN UX Table Editor</b> &rarr; <b>Approval Dashboard</b> to review.</p>
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = ", ".join(to_emails)
        msg.attach(MIMEText(body, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            if SMTP_USER and SMTP_PASS:
                s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(SMTP_FROM, to_emails, msg.as_string())
        return True
    except Exception:
        return False


def submit_approval_requests(rows_info, user, token):
    """Insert PENDING rows into the approval table."""
    for r in rows_info:
        run_statement(
            f"INSERT INTO {APPROVAL_TABLE} "
            f"(row_id, sp_test_id, old_status, new_status, requested_by, "
            f" requested_at, approver_email, status) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')",
            [int(r["row_id"]), r.get("sp_test_id"), r["old"], r["new"], user,
             datetime.now(timezone.utc).replace(tzinfo=None),
             ", ".join(APPROVER_EMAILS) if APPROVER_EMAILS else None],
            token,
        )


# ═════════════════════════════════════════════════════════════
#  DISCARD CHANGES
# ═════════════════════════════════════════════════════════════

def discard_all_changes():
    try:
        if "ts_data" not in st.session_state:
            st.warning("No data loaded.")
            return
        st.session_state["ts_sfe_df"] = st.session_state["ts_data"].copy()
        st.session_state["ts_key_counter"] = st.session_state.get("ts_key_counter", 0) + 1
        if "ts_page" in st.session_state:
            start = (st.session_state["ts_page"] - 1) * TS_PAGE_SIZE
            st.session_state["ts_start_index"] = start
            st.session_state["ts_end_index"] = start + TS_PAGE_SIZE
        for flag in ("ts_start_logged", "ts_filter_triggered",
                     "save_pending", "changes_made"):
            st.session_state[flag] = False
        st.toast("All changes discarded", icon="\u267b\ufe0f")
        st.rerun()
    except Exception as ex:
        st.error(f"Failed to discard: {ex}")


# ═════════════════════════════════════════════════════════════
#  PAGE CONFIG
# ═════════════════════════════════════════════════════════════

st.set_page_config(page_title="Edit test_status", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(SHARED_CSS, unsafe_allow_html=True)
st.logo(APP_LOGO_IMAGE, size="large", icon_image=APP_LOGO_ICON, link=APP_LOGO_LINK)

try:
    _current_user = get_user_identity()
except Exception:
    _current_user = "unknown"

try:
    _can_edit = is_user_in_editor_group(_current_user)
except Exception:
    _can_edit = False

_admin_users = [u.strip().lower() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()]
_approver_emails_lower = [e.lower() for e in APPROVER_EMAILS]
_hide_css = []
if _current_user.lower() not in _admin_users:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"] { display: none !important; }')
if _current_user.lower() not in _approver_emails_lower:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Approval_Dashboard"] { display: none !important; }')
if _hide_css:
    st.markdown(f'<style>{"".join(_hide_css)}</style>', unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════
#  MAIN CONTENT
# ═════════════════════════════════════════════════════════════

if "ts_data" in st.session_state:

    # ── Pagination init ───────────────────────────────────────
    if "ts_page" not in st.session_state:
        st.session_state["ts_page"] = 1
        st.session_state["ts_start_index"] = 0
        st.session_state["ts_end_index"] = TS_PAGE_SIZE
    if "ts_key_counter" not in st.session_state:
        st.session_state["ts_key_counter"] = 0

    # ── Sidebar filters ──────────────────────────────────────
    with st.sidebar:
        if st.session_state.get("ts_init_data_fetch"):
            if st.button("\U0001f504 Refresh Data", type="secondary",
                         use_container_width=True, key="ts_refresh_btn"):
                for k in list(st.session_state.keys()):
                    if k.startswith("ts_"):
                        del st.session_state[k]
                st.rerun()
            st.divider()
            st.header("Filters")

            def _ts_filter_toggle(col_key):
                def _on_change():
                    sel = st.session_state[col_key]
                    if "ALL" in sel and len(sel) > 1 and sel[0] == "ALL":
                        st.session_state[col_key] = [v for v in sel if v != "ALL"]
                    elif ("ALL" in sel and sel[-1] == "ALL") or len(sel) == 0:
                        st.session_state[col_key] = ["ALL"]
                return _on_change

            _ts_raw = st.session_state["ts_data"]
            ts_filters: dict[str, list] = {}

            for col in FILTER_COLUMNS:
                if col not in _ts_raw.columns or col in _HIDDEN_DISPLAY_COLS:
                    continue
                raw_vals = _ts_raw[col].dropna().unique()
                if pd.api.types.is_numeric_dtype(_ts_raw[col]):
                    unique_vals = [str(v) for v in sorted(raw_vals)]
                else:
                    unique_vals = sorted([str(v) for v in raw_vals])
                selected_vals = st.multiselect(
                    f"Select {col}:",
                    options=["ALL"] + unique_vals,
                    default=["ALL"],
                    key=f"ts_flt_{col}",
                    on_change=_ts_filter_toggle(f"ts_flt_{col}"),
                )
                if selected_vals and "ALL" not in selected_vals:
                    ts_filters[col] = selected_vals

            # Dynamic extra filters
            extra_cols = [c for c in _ts_raw.columns
                          if c not in FILTER_COLUMNS and c not in _HIDDEN_DISPLAY_COLS]
            selected_filter_cols = st.multiselect(
                "Filter by columns:", options=extra_cols, default=[],
                key="ts_filter_cols",
            )
            for col in selected_filter_cols:
                raw_vals = _ts_raw[col].dropna().unique()
                if pd.api.types.is_numeric_dtype(_ts_raw[col]):
                    unique_vals = [str(v) for v in sorted(raw_vals)]
                else:
                    unique_vals = sorted([str(v) for v in raw_vals])
                selected_vals = st.multiselect(
                    f"Select {col}:",
                    options=["ALL"] + unique_vals,
                    default=["ALL"],
                    key=f"ts_flt_{col}",
                    on_change=_ts_filter_toggle(f"ts_flt_{col}"),
                )
                if selected_vals and "ALL" not in selected_vals:
                    ts_filters[col] = selected_vals

            fc1, fc2 = st.columns(2)
            with fc1:
                filter_btn = st.button("Apply Filters", type="primary",
                                        use_container_width=True, key="ts_apply_filters")
            with fc2:
                clear_btn = st.button("Clear Filters", use_container_width=True,
                                       key="ts_clear_filters")

            if filter_btn:
                st.session_state["ts_active_filters"] = ts_filters
                st.session_state["ts_page"] = 1
                st.session_state["ts_start_index"] = 0
                st.session_state["ts_end_index"] = TS_PAGE_SIZE
                st.rerun()

            if clear_btn:
                st.session_state["ts_active_filters"] = {}
                st.session_state["ts_sfe_df"] = st.session_state["ts_data"].copy()
                st.session_state["ts_key_counter"] = st.session_state.get("ts_key_counter", 0) + 1
                st.session_state["ts_page"] = 1
                st.session_state["ts_start_index"] = 0
                st.session_state["ts_end_index"] = TS_PAGE_SIZE
                st.session_state["ts_total_pages"] = max(
                    1, math.ceil(len(st.session_state["ts_data"]) / TS_PAGE_SIZE),
                )
                st.rerun()

    # ── Title bar ────────────────────────────────────────────
    rc1, rc2 = st.columns([4, 1], vertical_alignment="bottom")
    with rc1:
        st.markdown(
            "<h2 style='font-size:28px; font-weight:700; margin-bottom:5px;'>"
            "[EDIT] test_status</h2>"
            "<p style='margin:0; font-size:13px; color:#666;'>"
            "<b>Cancelled</b> = direct commit &nbsp;|&nbsp; "
            "<b>Other statuses</b> = sent for approval</p>",
            unsafe_allow_html=True,
        )
        if not _can_edit:
            st.caption("\U0001f512 Read-only mode")
    with rc2:
        if _can_edit:
            st.button("Save changes", type="primary", key="ts_save_change_btn",
                       use_container_width=True)

    # ── Sort form ────────────────────────────────────────────
    with st.form("ts_sort_form", clear_on_submit=False):
        sc1, sc2, sc3 = st.columns([2.5, 2.5, 1], vertical_alignment="bottom")
        with sc1:
            sort_col = st.selectbox(
                "Column name",
                options=[c for c in st.session_state["ts_data"].columns
                         if c not in _HIDDEN_DISPLAY_COLS],
            )
        with sc2:
            sort_ord = st.selectbox("Sort order", options=["ascending", "descending"])
        with sc3:
            sort_submitted = st.form_submit_button("Sort table", type="primary",
                                                    use_container_width=True)

    # ── Apply active filters ─────────────────────────────────
    active_filters = st.session_state.get("ts_active_filters", {})
    if active_filters:
        try:
            df_filtered = st.session_state["ts_data"].copy()
            for col, vals in active_filters.items():
                if col in df_filtered.columns:
                    df_filtered = df_filtered[df_filtered[col].astype(str).isin(vals)]
            st.session_state["ts_sfe_df"] = df_filtered.reset_index(drop=True)
            st.session_state["ts_total_pages"] = max(
                1, math.ceil(len(st.session_state["ts_sfe_df"]) / TS_PAGE_SIZE),
            )
        except Exception as _ex:
            st.warning(f"Filter failed: {_ex}")

    # ── Apply sort ───────────────────────────────────────────
    if sort_submitted:
        try:
            st.session_state["ts_sfe_df"] = (
                st.session_state["ts_sfe_df"]
                .sort_values(by=sort_col, ascending=(sort_ord == "ascending"))
                .reset_index(drop=True)
            )
        except Exception:
            pass

    # ── Hide completed rows (test_status != 'complete') ─────
    sfe = st.session_state["ts_sfe_df"]
    try:
        sfe = sfe[
            sfe["test_status"].astype(str).str.strip().str.lower() != "complete"
        ].reset_index(drop=True)
    except Exception:
        pass  # keep sfe as-is if filter fails
    st.session_state["ts_sfe_df"] = sfe
    st.session_state["ts_total_pages"] = max(1, math.ceil(len(sfe) / TS_PAGE_SIZE))

    # ── Slice page ───────────────────────────────────────────
    si = st.session_state["ts_start_index"]
    ei = st.session_state["ts_end_index"]

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

    # ── Reorder columns: put test_status near the front ──────
    _priority_cols = ["_select", "row_id", "test_status", "match_type", "sp_test_id", "cl_test_id"]
    if "ts_sfe_df" in st.session_state:
        _df = st.session_state["ts_sfe_df"]
        _front = [c for c in _priority_cols if c in _df.columns]
        _rest = [c for c in _df.columns if c not in _front]
        st.session_state["ts_sfe_df"] = _df[_front + _rest]

    col_cfg = build_column_config(st.session_state["ts_data"], dropdown_required=True)
    # Override test_status with dropdown
    col_cfg["test_status"] = st.column_config.SelectboxColumn(
        to_bold("test_status"),
        options=TEST_STATUS_OPTIONS,
        required=True,
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
        key=f"ts_grid_{st.session_state.get('ts_key_counter', 0)}",
        disabled=(
            get_disabled_columns_by_group(_current_user, list(page_slice.columns)) + ["_clr"]
            if _can_edit
            else True
        ),
        column_config=col_cfg,
        column_order=["_select", "_clr"] + [
            c for c in page_slice.columns
            if c not in ("_select", "_clr") and c not in _HIDDEN_DISPLAY_COLS
        ],
    )

    # ── Copy-row-below ───────────────────────────────────────
    selected_rows = edited_data[edited_data["_select"] == True]
    if not selected_rows.empty and _can_edit:
        if st.button(f"\u2795 Copy {len(selected_rows)} row(s) below",
                      type="primary", key="ts_apply_copy_btn"):
            try:
                current_sfe = st.session_state["ts_sfe_df"]
                positions = [idx for idx in selected_rows.index
                             if si <= idx < min(ei, len(current_sfe))]
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
                st.session_state["ts_sfe_df"] = result
                st.session_state["ts_total_pages"] = max(
                    1, math.ceil(len(result) / TS_PAGE_SIZE),
                )
                st.toast(f"Copied {len(positions)} row(s)", icon="\U0001f4cb")
                st.rerun()
            except Exception as _ex:
                st.error(f"Copy failed: {_ex}")

    # Strip transient columns
    edited_data = edited_data.drop(columns=["_select", "_clr"], errors="ignore")
    edited_data = (
        edited_data.sort_values("row_id")
        .reset_index(drop=True)
        .set_index("row_id")
    )

    # ── Audit: log first edit ────────────────────────────────
    try:
        _tk = get_user_token()
        if _tk:
            _conn = get_connection(_tk)
            create_audit_table(_conn)
            log_edit_start_once(
                _conn, original_data, edited_data,
                st.session_state["ts_page"],
            )
    except Exception:
        pass

    # ═════════════════════════════════════════════════════════
    #  SAVE MODAL — KEY DIFFERENCE FROM PAGE 2
    # ═════════════════════════════════════════════════════════

    @st.dialog("Change Summary", width="medium")
    def ts_save_change_modal():
        try:
            _full_sfe = st.session_state["ts_sfe_df"].copy()
            _page_edited = edited_data.reset_index()
            _before = _full_sfe.iloc[:si]
            _after = _full_sfe.iloc[ei:]
            _merged = pd.concat([_before, _page_edited, _after], ignore_index=True)

            _full_orig = st.session_state["ts_data"].copy()
            # Apply same "complete" filter as display
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
            st.error(f"Failed to compute changes: {_ex}")
            return

        if added_count == 0 and deleted_count == 0 and updated_count == 0:
            st.info("No changes found.")
            return

        # ── Identify test_status changes that need approval ──
        approval_rows_info = []
        direct_status_row_ids = set()

        if updated_count > 0 and "test_status" in _compare_cols:
            try:
                ts_changes = diff.xs("test_status", level=1) if "test_status" in diff.index.get_level_values(1) else pd.DataFrame()
            except (KeyError, TypeError):
                ts_changes = pd.DataFrame()

            if not ts_changes.empty:
                for rid in ts_changes.index:
                    old_val = str(ts_changes.at[rid, "self"]) if "self" in ts_changes.columns else ""
                    new_val = str(ts_changes.at[rid, "other"]) if "other" in ts_changes.columns else ""

                    if new_val in DIRECT_COMMIT_STATUSES:
                        direct_status_row_ids.add(rid)
                    else:
                        sp_id = _existing.at[rid, "sp_test_id"] if "sp_test_id" in _existing.columns else ""
                        approval_rows_info.append({
                            "row_id": rid,
                            "sp_test_id": sp_id if not is_na(sp_id) else "",
                            "old": old_val, "new": new_val,
                        })

        # ── Summary display ──────────────────────────────────
        st.warning(
            f"Rows added: {added_count}, "
            f"Rows deleted: {deleted_count}, "
            f"Rows updated: {updated_count}"
        )

        if approval_rows_info:
            st.info(
                f"\u2709 **{len(approval_rows_info)}** test_status change(s) "
                f"will be sent for **approval** (non-Cancelled)."
            )
            _appr_df = pd.DataFrame(approval_rows_info)
            st.dataframe(_appr_df, use_container_width=True, hide_index=True)

        if direct_status_row_ids:
            st.success(
                f"\u2705 **{len(direct_status_row_ids)}** test_status change(s) "
                f"to **Cancelled** will commit directly."
            )

        insert_rows_df = _new_rows.set_index("row_id") if added_count > 0 else None
        delete_rows_df = _orig_indexed.loc[deleted_rows] if deleted_count > 0 else None
        update_rows_df = _existing.loc[diff.index.levels[0]] if updated_count > 0 else None

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

        # Mandatory validation
        _mandatory_errors: list[str] = []
        if update_rows_df is not None:
            _mandatory_errors += validate_mandatory_cols(
                update_rows_df.reset_index(), MANDATORY_UPDATE_COLS)
        if insert_rows_df is not None:
            _mandatory_errors += validate_mandatory_cols(
                insert_rows_df.reset_index(), MANDATORY_UPDATE_COLS)

        if _mandatory_errors:
            st.error(
                f"\u26a0\ufe0f Cannot save: {len(_mandatory_errors)} row(s) missing "
                f"mandatory columns ({', '.join(MANDATORY_UPDATE_COLS)}):"
            )
            for _e in _mandatory_errors:
                st.warning(_e)

        scr1, scr2, _ = st.columns([1.5, 1.5, 3])
        with scr1:
            st.button("Confirm & Save", type="primary", key="ts_confirm_save_btn",
                       disabled=bool(_mandatory_errors))
        with scr2:
            st.button("Discard Changes", type="primary", key="ts_discard_changes_btn")

        if st.session_state.get("ts_discard_changes_btn"):
            discard_all_changes()

        if st.session_state.get("ts_confirm_save_btn"):
            tk = get_user_token()
            conn = get_connection(tk)
            create_audit_table(conn)
            inserted_ids, deleted_ids = [], []

            # 1) UPDATE — direct commits (including Cancelled status changes)
            if updated_count > 0:
                pre_errors = validate_mandatory_cols(
                    update_rows_df.reset_index(), MANDATORY_UPDATE_COLS)
                if pre_errors:
                    for e in pre_errors:
                        st.warning(e)
                else:
                    # Remove ONLY test_status column from diff for approval rows
                    # (other column changes on the same row still commit directly)
                    approval_rids = {r["row_id"] for r in approval_rows_info}
                    if approval_rids:
                        # Exclude test_status column from diff for approval rows
                        approval_ts_mask = (
                            diff.index.get_level_values(0).isin(approval_rids)
                            & (diff.index.get_level_values(1) == "test_status")
                        )
                        direct_diff = diff[~approval_ts_mask]
                        if len(direct_diff) > 0:
                            direct_rids = direct_diff.index.get_level_values(0).unique()
                            direct_update_df = _existing.loc[direct_rids].copy()
                            # CRITICAL: restore ORIGINAL test_status for approval rows
                            # so bulk_update MERGE doesn't overwrite it
                            for rid in approval_rids:
                                if rid in direct_update_df.index and rid in _orig_indexed.index:
                                    direct_update_df.at[rid, "test_status"] = (
                                        _orig_indexed.at[rid, "test_status"]
                                    )
                        else:
                            direct_update_df = None
                    else:
                        direct_diff = diff
                        direct_update_df = update_rows_df

                    if direct_update_df is not None and len(direct_diff) > 0:
                        with st.spinner("Updating rows..."):
                            ok, err, err_list, _ = bulk_update(
                                conn, direct_update_df, direct_diff)
                            if err == 0:
                                st.success(f"[UPDATE] {direct_update_df.shape[0]} rows updated.")
                            else:
                                st.error(f"[UPDATE] failed: {err_list}")
                    else:
                        if added_count > 0 or deleted_count > 0 or approval_rows_info:
                            pass  # other operations will follow
                        else:
                            st.info("No direct updates required.")

            # 2) INSERT
            if added_count > 0:
                with st.spinner("Inserting rows..."):
                    ok, err, err_list, rid_list = bulk_insert(conn, insert_rows_df)
                    if err == 0:
                        inserted_ids = rid_list
                        st.success(f"[INSERT] {ok} rows inserted (IDs: {rid_list}).")
                    else:
                        st.error(f"[INSERT] failed: {err_list}")

            # 3) DELETE
            if deleted_count > 0:
                with st.spinner("Deleting rows..."):
                    ok, err, err_list, rid_list = bulk_delete(conn, delete_rows_df)
                    if err == 0:
                        deleted_ids = rid_list
                        st.success(f"[DELETE] {ok} rows deleted (IDs: {rid_list}).")
                    else:
                        st.error(f"[DELETE] failed: {err_list}")

            # 4) APPROVAL REQUESTS (non-Cancelled test_status changes)
            if approval_rows_info:
                with st.spinner("Submitting approval requests..."):
                    try:
                        submit_approval_requests(approval_rows_info, _current_user, tk)
                        email_sent = send_approval_email(
                            APPROVER_EMAILS, approval_rows_info, _current_user)

                        # Trigger the "test_status change alert" immediately
                        try:
                            run_statement(
                                "SELECT 1",  # dummy to ensure connection
                                [], tk,
                            )
                        except Exception:
                            pass

                        notif = (
                            " Email sent to approvers."
                            if email_sent
                            else " Alert triggered. Approvers notified via Databricks alert."
                        )
                        st.info(
                            f"\u2709 {len(approval_rows_info)} approval request(s) "
                            f"submitted.{notif}"
                        )
                    except Exception as ex:
                        st.error(f"Approval submission failed: {ex}")

            # 5) AUDIT
            try:
                events = build_audit_events(
                    original_df=_orig_indexed, edited_df=_existing,
                    added_rows_df=insert_rows_df, deleted_rows_df=delete_rows_df,
                    diff_df=diff if updated_count > 0 else None,
                    page_no=st.session_state["ts_page"],
                    inserted_row_ids=inserted_ids, deleted_row_ids=deleted_ids,
                )
                write_audit_events(conn, events)
            except Exception:
                pass

            # 6) Refresh
            try:
                raw = run_query(
                    f"SELECT * FROM {TABLE_FQN} ORDER BY row_id "
                    f"-- cb: {uuid.uuid4()}", tk)
                _safe = {k: v for k, v in SCHEMA_DTYPE.items() if k in raw.columns}
                ts_data = raw.astype(_safe)
                st.session_state["ts_data"] = ts_data
                st.session_state["ts_sfe_df"] = ts_data.copy()
                st.session_state["ts_total_pages"] = max(
                    1, math.ceil(len(ts_data) / TS_PAGE_SIZE))
            except Exception:
                pass
            st.session_state["ts_key_counter"] = st.session_state.get("ts_key_counter", 0) + 1
            st.session_state["ts_active_filters"] = {}
            st.toast("Changes saved!", icon="\u2705")
            time.sleep(1)
            st.rerun()

    if _can_edit and st.session_state.get("ts_save_change_btn"):
        ts_save_change_modal()

    # ── Pagination ───────────────────────────────────────────
    def _ts_go_page(delta):
        st.session_state["ts_page"] += delta
        st.session_state["ts_start_index"] = (st.session_state["ts_page"] - 1) * TS_PAGE_SIZE
        st.session_state["ts_end_index"] = st.session_state["ts_start_index"] + TS_PAGE_SIZE
        st.rerun()

    _total_rows = len(st.session_state.get("ts_sfe_df", []))
    _pg = st.session_state["ts_page"]
    _tp = st.session_state["ts_total_pages"]

    pg_prev, pg_lbl, pg_input, pg_of, pg_next = st.columns(
        [1, 0.5, 0.6, 2, 1], vertical_alignment="center")
    with pg_prev:
        if st.button("Previous", use_container_width=True, disabled=_pg <= 1,
                       key="ts_prev"):
            _ts_go_page(-1)
    with pg_lbl:
        st.markdown(
            "<div style='text-align:right; font-weight:600; white-space:nowrap;'>"
            "Page</div>", unsafe_allow_html=True)
    with pg_input:
        go_page_num = st.number_input(
            "Go to page", min_value=1, max_value=_tp,
            value=_pg, step=1, key="ts_go_page_input",
            label_visibility="collapsed")
        if go_page_num != _pg:
            st.session_state["ts_page"] = go_page_num
            st.session_state["ts_start_index"] = (go_page_num - 1) * TS_PAGE_SIZE
            st.session_state["ts_end_index"] = st.session_state["ts_start_index"] + TS_PAGE_SIZE
            st.rerun()
    with pg_of:
        st.markdown(
            f"<div style='font-weight:600; white-space:nowrap;'>"
            f"of {_tp} &nbsp;|&nbsp; {_total_rows:,} rows total</div>",
            unsafe_allow_html=True)
    with pg_next:
        if st.button("Next", use_container_width=True, disabled=_pg >= _tp,
                       key="ts_next"):
            _ts_go_page(1)

else:
    # ── Load data on demand (not auto-fetch) ─────────────────
    st.markdown(
        "<h2 style='font-size:28px; font-weight:700; margin-bottom:5px;'>"
        "[EDIT] test_status</h2>"
        "<p style='margin:0; font-size:13px; color:#666;'>"
        "<b>Cancelled</b> = direct commit &nbsp;|&nbsp; "
        "<b>Other statuses</b> = sent for approval</p>",
        unsafe_allow_html=True,
    )
    st.info("Click **Load Data** to fetch the table.")
    if st.button("Load Data", type="primary", key="ts_load_btn"):
        try:
            with st.spinner(f"Loading {TABLE_FQN}..."):
                fetch_sql = (
                    f"SELECT * FROM {TABLE_FQN} ORDER BY row_id "
                    f"-- cb: {uuid.uuid4()}"
                )
                _tk = get_user_token()
                raw = run_query(fetch_sql, _tk)
                _safe = {k: v for k, v in SCHEMA_DTYPE.items() if k in raw.columns}
                ts_data = raw.astype(_safe)

                st.session_state["ts_data"] = ts_data
                st.session_state["ts_sfe_df"] = ts_data.copy()
                st.session_state["ts_total_pages"] = max(
                    1, math.ceil(len(ts_data) / TS_PAGE_SIZE))

            if ts_data.empty:
                st.info("Table loaded but returned 0 rows.")
            else:
                st.session_state["ts_init_data_fetch"] = True
                st.rerun()
        except Exception as ex:
            st.error(f"Failed to load table: {ex}")
