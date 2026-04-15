# ============================================================
# 6_Approval_Dashboard.py  –  Approve / Reject test_status
# ============================================================
# Approver-only page. Shows PENDING approval requests with
# checkboxes. Approver selects rows → Approve or Reject.
#   Approve  →  UPDATE committed to master table
#   Reject   →  change discarded (no DB update)
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
if current_user.lower() not in _admin_users:
    st.markdown(
        '<style>[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"]'
        ' { display: none !important; }</style>',
        unsafe_allow_html=True,
    )

is_approver = current_user.lower() in [e.lower() for e in APPROVER_EMAILS]


# ═════════════════════════════════════════════════════════════
#  DATA HELPERS
# ═════════════════════════════════════════════════════════════

def fetch_pending(tk):
    return run_query(
        f"SELECT * FROM {APPROVAL_TABLE} WHERE status = 'PENDING' "
        f"ORDER BY requested_at DESC -- cb: {uuid.uuid4()}", tk)


def fetch_history(tk, limit=200):
    return run_query(
        f"SELECT * FROM {APPROVAL_TABLE} WHERE status != 'PENDING' "
        f"ORDER BY reviewed_at DESC LIMIT {limit} -- cb: {uuid.uuid4()}", tk)


def approve_rows(approval_ids, reviewer, comment, tk):
    """Approve: UPDATE master table + mark APPROVED."""
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

        # Mark as APPROVED
        run_statement(
            f"UPDATE {APPROVAL_TABLE} SET status = 'APPROVED', "
            f"reviewed_by = ?, reviewed_at = ?, review_comment = ? "
            f"WHERE approval_id = ?",
            [reviewer, datetime.now(timezone.utc).replace(tzinfo=None),
             comment, aid], tk)
        approved += 1
    return approved


def reject_rows(approval_ids, reviewer, comment, tk):
    """Reject: mark REJECTED, no DB update."""
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
    st.caption(f"User: **{current_user}**")
    if is_approver:
        st.success("You are an approver")
    else:
        st.warning("View-only (not an approver)")

    if st.button("Refresh", type="primary", use_container_width=True, key="appr_refresh"):
        for k in list(st.session_state.keys()):
            if k.startswith("appr_"):
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

if not is_approver:
    st.warning(
        f"You (**{current_user}**) are not a designated approver. "
        f"Current approvers: {', '.join(APPROVER_EMAILS) or 'None configured'}"
    )
    st.info("You can view pending requests but cannot approve or reject them.")

tab_pending, tab_history = st.tabs(["Pending Approvals", "Approval History"])


# ─── TAB 1: Pending Approvals ────────────────────────────────
with tab_pending:
    try:
        tk = get_user_token()
        pending = fetch_pending(tk)

        if pending.empty:
            st.info("No pending approval requests. All clear!")
        else:
            st.warning(f"**{len(pending)}** request(s) awaiting review")

            # Add _select checkbox column
            pending_display = pending.copy()
            pending_display.insert(0, "_select", False)

            display_cols = ["_select", "approval_id", "row_id", "sp_test_id",
                            "old_status", "new_status", "requested_by", "requested_at"]
            available_cols = [c for c in display_cols if c in pending_display.columns]

            col_cfg = {
                "_select": st.column_config.CheckboxColumn(
                    to_bold("Select"), pinned=True, width="small"),
                "approval_id": st.column_config.NumberColumn(
                    to_bold("ID"), disabled=True),
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
                        disabled=not is_approver,
                    )
                with col2:
                    reject_btn = st.button(
                        f"Reject {len(selected)} Selected",
                        key="appr_reject_btn",
                        disabled=not is_approver,
                    )

                if approve_btn and is_approver:
                    aids = selected["approval_id"].tolist()
                    with st.spinner("Approving and committing changes..."):
                        count = approve_rows(
                            aids, current_user, comment, tk)
                    if count > 0:
                        st.success(
                            f"Approved **{count}** request(s). "
                            f"Changes committed to master table."
                        )
                        # Clear page 5 cached data
                        for k in list(st.session_state.keys()):
                            if k.startswith("ts_"):
                                del st.session_state[k]
                        st.balloons()
                        time.sleep(1.5)
                        st.rerun()

                if reject_btn and is_approver:
                    aids = selected["approval_id"].tolist()
                    with st.spinner("Rejecting requests..."):
                        count = reject_rows(
                            aids, current_user, comment, tk)
                    if count > 0:
                        st.warning(
                            f"Rejected **{count}** request(s). "
                            f"No changes made to master table."
                        )
                        time.sleep(1)
                        st.rerun()
            else:
                if is_approver:
                    st.caption("Select rows using the checkbox column, then click Approve or Reject.")

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

            display_cols = [
                "approval_id", "row_id", "sp_test_id", "old_status",
                "new_status", "requested_by", "requested_at",
                "status", "reviewed_by", "reviewed_at", "review_comment",
            ]
            show_cols = [c for c in display_cols if c in history.columns]

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
            total = len(history)
            approved_count = len(history[history["status"] == "APPROVED"])
            rejected_count = len(history[history["status"] == "REJECTED"])
            with mc1:
                st.metric("Total Decisions", total)
            with mc2:
                st.metric("Approved", approved_count)
            with mc3:
                st.metric("Rejected", rejected_count)

    except Exception as ex:
        st.error(f"Failed to load history: {ex}")
