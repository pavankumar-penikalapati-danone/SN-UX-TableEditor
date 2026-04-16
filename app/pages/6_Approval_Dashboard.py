# ============================================================
# 6_Approval_Dashboard.py  –  Approve / Reject test_status
# ============================================================
# Approver-only page. Shows PENDING requests with full row
# context (JOINed with master table) so approver can make
# informed decisions. Checkbox select → bulk approve/reject.
# ============================================================

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from config import (
    CATALOG, SCHEMA, TABLE_FQN,
    SHARED_CSS, APP_LOGO_IMAGE, APP_LOGO_ICON, APP_LOGO_LINK,
    to_bold, is_na,
    get_user_token, run_query, run_statement,
    get_user_identity, ensure_session_id,
    create_audit_table, write_audit_events, get_connection,
    is_user_in_editor_group,
)

# ── Constants ────────────────────────────────────────────────
APPROVAL_TABLE = os.getenv(
    "APPROVAL_TABLE_FQN",
    f"{CATALOG}.{SCHEMA}.ux_sn_test_status_approvals",
)
APPROVER_EMAILS = [
    e.strip() for e in os.getenv("TEST_STATUS_APPROVERS", "").split(",") if e.strip()
]

# Columns from master table to show alongside approval request
CONTEXT_COLS = [
    "test_country", "brand_L0", "test_year", "prod_catL1",
    "zone", "CBU", "brand_L1", "prod_type", "flavour_pack",
    "pack", "prod_name_pack", "prod_status",
]


# ═════════════════════════════════════════════════════════════
#  PAGE CONFIG
# ═════════════════════════════════════════════════════════════

st.set_page_config(page_title="Approval Dashboard", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(SHARED_CSS, unsafe_allow_html=True)
st.logo(APP_LOGO_IMAGE, size="large", icon_image=APP_LOGO_ICON, link=APP_LOGO_LINK)

try:
    current_user = get_user_identity()
except Exception:
    current_user = "unknown"

_admin_users = [u.strip().lower() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()]
_approver_emails_lower = [e.lower() for e in APPROVER_EMAILS]
is_approver = current_user.lower() in _approver_emails_lower

# Hide sidebar links
_hide_css = []
if current_user.lower() not in _admin_users:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"] { display: none !important; }')
if not is_approver:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Approval_Dashboard"] { display: none !important; }')
if _hide_css:
    st.markdown(f'<style>{"".join(_hide_css)}</style>', unsafe_allow_html=True)

# Block non-approvers
if not is_approver:
    st.error(
        f"**Access Denied.** This page is restricted to designated approvers only.\n\n"
        f"Your account: `{current_user}`"
    )
    st.stop()


# ═════════════════════════════════════════════════════════════
#  DATA HELPERS
# ═════════════════════════════════════════════════════════════

def fetch_pending_with_context(tk):
    """Fetch PENDING approvals JOINed with master table for full row context."""
    ctx_cols_sql = ", ".join([f"m.{c}" for c in CONTEXT_COLS])
    q = (
        f"SELECT a.approval_id, a.row_id, a.sp_test_id, "
        f"a.old_status, a.new_status, "
        f"{ctx_cols_sql}, "
        f"a.requested_by, a.requested_at "
        f"FROM {APPROVAL_TABLE} a "
        f"LEFT JOIN {TABLE_FQN} m ON a.row_id = m.row_id "
        f"WHERE a.status = 'PENDING' "
        f"ORDER BY a.requested_at DESC "
        f"-- cb: {uuid.uuid4()}"
    )
    return run_query(q, tk)


def fetch_history(tk, limit=200):
    ctx_cols_sql = ", ".join([f"m.{c}" for c in CONTEXT_COLS])
    q = (
        f"SELECT a.*, {ctx_cols_sql} "
        f"FROM {APPROVAL_TABLE} a "
        f"LEFT JOIN {TABLE_FQN} m ON a.row_id = m.row_id "
        f"WHERE a.status != 'PENDING' "
        f"ORDER BY a.reviewed_at DESC LIMIT {limit} "
        f"-- cb: {uuid.uuid4()}"
    )
    return run_query(q, tk)


def approve_rows(approval_ids, reviewer, comment, tk):
    approved = 0
    for aid in approval_ids:
        req = run_query(
            f"SELECT row_id, new_status FROM {APPROVAL_TABLE} "
            f"WHERE approval_id = {aid}", tk)
        if req.empty:
            continue
        row_id = int(req.iloc[0]["row_id"])
        new_status = req.iloc[0]["new_status"]

        # Commit to master table
        run_statement(
            f"UPDATE {TABLE_FQN} SET test_status = ? WHERE row_id = ?",
            [new_status, row_id], tk)

        # Mark APPROVED
        run_statement(
            f"UPDATE {APPROVAL_TABLE} SET status = 'APPROVED', "
            f"reviewed_by = ?, reviewed_at = ?, review_comment = ? "
            f"WHERE approval_id = ?",
            [reviewer, datetime.now(timezone.utc).replace(tzinfo=None),
             comment, aid], tk)
        approved += 1
    return approved


def reject_rows(approval_ids, reviewer, comment, tk):
    rejected = 0
    for aid in approval_ids:
        run_statement(
            f"UPDATE {APPROVAL_TABLE} SET status = 'REJECTED', "
            f"reviewed_by = ?, reviewed_at = ?, review_comment = ? "
            f"WHERE approval_id = ?",
            [reviewer, datetime.now(timezone.utc).replace(tzinfo=None),
             comment, aid], tk)
        rejected += 1
    return rejected


# ═════════════════════════════════════════════════════════════
#  SIDEBAR
# ═════════════════════════════════════════════════════════════

ensure_session_id("appr_session_id")

with st.sidebar:
    st.header("Approval Dashboard")
    st.caption(f"Approver: **{current_user}**")

    if st.button("Refresh", type="primary", use_container_width=True, key="appr_refresh"):
        for k in list(st.session_state.keys()):
            if k.startswith("appr_") or k.startswith("ts_"):
                del st.session_state[k]
        st.rerun()


# ═════════════════════════════════════════════════════════════
#  MAIN CONTENT
# ═════════════════════════════════════════════════════════════

st.markdown(
    "<h2 style='font-size:28px; font-weight:700; margin-bottom:5px;'>"
    "Approval Dashboard</h2>",
    unsafe_allow_html=True,
)

tab_pending, tab_history = st.tabs(["Pending Approvals", "Approval History"])


# ─── TAB 1: Pending Approvals ────────────────────────────────
with tab_pending:
    try:
        tk = get_user_token()
        pending = fetch_pending_with_context(tk)

        if pending.empty:
            st.success("No pending approval requests. All clear!")
        else:
            st.warning(f"**{len(pending)}** request(s) awaiting your review")

            # Add _select checkbox
            pending_display = pending.copy()
            pending_display.insert(0, "_select", False)

            # Build column order: checkbox, key fields, context, meta
            key_cols = ["_select", "approval_id", "row_id", "sp_test_id",
                        "old_status", "new_status"]
            context_cols = [c for c in CONTEXT_COLS if c in pending_display.columns]
            meta_cols = ["requested_by", "requested_at"]
            all_display_cols = key_cols + context_cols + meta_cols
            available_cols = [c for c in all_display_cols if c in pending_display.columns]

            col_cfg = {
                "_select": st.column_config.CheckboxColumn(
                    to_bold("Select"), pinned=True, width="small"),
                "approval_id": st.column_config.NumberColumn(
                    to_bold("ID"), disabled=True, width="small"),
                "row_id": st.column_config.NumberColumn(
                    to_bold("row_id"), disabled=True),
                "sp_test_id": st.column_config.TextColumn(
                    to_bold("sp_test_id"), disabled=True),
                "old_status": st.column_config.TextColumn(
                    to_bold("old_status"), disabled=True),
                "new_status": st.column_config.TextColumn(
                    to_bold("new_status"), disabled=True),
                "requested_by": st.column_config.TextColumn(
                    to_bold("requested_by"), disabled=True),
                "requested_at": st.column_config.DatetimeColumn(
                    to_bold("requested_at"), disabled=True),
            }
            # Add context columns config
            for c in context_cols:
                col_cfg[c] = st.column_config.TextColumn(
                    to_bold(c), disabled=True)

            edited_pending = st.data_editor(
                pending_display[available_cols],
                use_container_width=True,
                hide_index=True,
                disabled=[c for c in available_cols if c != "_select"],
                column_config=col_cfg,
                key="appr_pending_editor",
            )

            selected = edited_pending[edited_pending["_select"] == True]

            if len(selected) > 0:
                st.info(f"**{len(selected)}** request(s) selected")

                comment = st.text_input(
                    "Review comment (optional):",
                    key="appr_review_comment",
                )

                col1, col2, _ = st.columns([1.5, 1.5, 4])
                with col1:
                    approve_btn = st.button(
                        f"Approve {len(selected)} Selected",
                        type="primary", key="appr_approve_btn",
                    )
                with col2:
                    reject_btn = st.button(
                        f"Reject {len(selected)} Selected",
                        key="appr_reject_btn",
                    )

                if approve_btn:
                    aids = selected["approval_id"].tolist()
                    with st.spinner("Approving and committing changes..."):
                        count = approve_rows(aids, current_user, comment, tk)
                    if count > 0:
                        st.success(
                            f"Approved **{count}** request(s). "
                            f"Changes committed to master table."
                        )
                        # Clear page 5 cached data so it refreshes
                        for k in list(st.session_state.keys()):
                            if k.startswith("ts_"):
                                del st.session_state[k]
                        st.balloons()
                        time.sleep(1.5)
                        st.rerun()

                if reject_btn:
                    aids = selected["approval_id"].tolist()
                    with st.spinner("Rejecting requests..."):
                        count = reject_rows(aids, current_user, comment, tk)
                    if count > 0:
                        st.warning(
                            f"Rejected **{count}** request(s). "
                            f"No changes made to master table."
                        )
                        time.sleep(1)
                        st.rerun()
            else:
                st.caption(
                    "Select rows using the checkbox column, "
                    "then click Approve or Reject."
                )

    except Exception as ex:
        st.error(f"Failed to load pending approvals: {ex}")


# ─── TAB 2: Approval History ─────────────────────────────────
with tab_history:
    try:
        tk = get_user_token()
        history = fetch_history(tk)

        if history.empty:
            st.info("No approval history yet.")
        else:
            st.caption(f"Showing last **{len(history)}** decisions")

            # Column order for history
            hist_key = ["approval_id", "row_id", "sp_test_id",
                        "old_status", "new_status"]
            hist_ctx = [c for c in CONTEXT_COLS if c in history.columns]
            hist_meta = ["status", "requested_by", "requested_at",
                         "reviewed_by", "reviewed_at", "review_comment"]
            show_cols = [c for c in hist_key + hist_ctx + hist_meta
                         if c in history.columns]

            def _status_color(val):
                if val == "APPROVED":
                    return "background-color: #d4edda; color: #155724;"
                elif val == "REJECTED":
                    return "background-color: #f8d7da; color: #721c24;"
                return ""

            styled = history[show_cols].style.applymap(
                _status_color, subset=["status"])
            st.dataframe(styled, use_container_width=True, hide_index=True)

            # Summary metrics
            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.metric("Total Decisions", len(history))
            with mc2:
                st.metric("Approved", len(history[history["status"] == "APPROVED"]))
            with mc3:
                st.metric("Rejected", len(history[history["status"] == "REJECTED"]))

    except Exception as ex:
        st.error(f"Failed to load history: {ex}")
