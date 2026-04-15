# ============================================================
# 5_edit_test_status.py  –  test_status Edit with Approval
# ============================================================
# When a user edits test_status, the change is NOT applied
# directly. Instead it creates a PENDING approval request.
# An approver can then approve (commits to DB) or reject.
# Email notification is sent to the configured approver.
# ============================================================

from __future__ import annotations

import math
import os
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import pandas as pd
import streamlit as st

from config import (
    CATALOG, SCHEMA, TABLE_FQN, PAGE_SIZE,
    SHARED_CSS, APP_LOGO_IMAGE, APP_LOGO_ICON, APP_LOGO_LINK,
    FILTER_COLUMNS, DROPDOWN_COLS,
    to_bold, is_na,
    get_user_token, get_connection, run_query, run_statement,
    get_user_identity, ensure_session_id,
    create_audit_table, write_audit_events,
    sp_connection,
    is_user_in_editor_group,
)

# ── Constants ────────────────────────────────────────────────
APPROVAL_TABLE = os.getenv(
    "APPROVAL_TABLE_FQN",
    f"{CATALOG}.{SCHEMA}.ux_sn_test_status_approvals",
)
TEST_STATUS_OPTIONS = ["Cancelled", "Complete", "Future Test", "Test In Progress"]

# Approver email(s) – comma-separated in app.yaml
APPROVER_EMAILS = [
    e.strip() for e in os.getenv("TEST_STATUS_APPROVERS", "").split(",") if e.strip()
]

# SMTP settings (optional – if not set, notifications are skipped)
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

_HIDDEN_DISPLAY_COLS = {"ingestion_timestamp", "Unnamed__64", "Unnamed__65",
                        "Unnamed__66", "Unnamed__71", "Unnamed__72", "Unnamed__73"}

EDIT_PAGE_SIZE = int(os.getenv("TEST_STATUS_PAGE_SIZE", "50"))


# ═════════════════════════════════════════════════════════════
#  PAGE CONFIG
# ═════════════════════════════════════════════════════════════

st.set_page_config(page_title="Edit test_status", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(SHARED_CSS, unsafe_allow_html=True)
st.logo(APP_LOGO_IMAGE, size="large", icon_image=APP_LOGO_ICON, link=APP_LOGO_LINK)

try:
    current_user = get_user_identity()
except Exception:
    current_user = "unknown"

_can_edit = is_user_in_editor_group(current_user)

# Hide Admin page from sidebar for non-admin users
_admin_users = [u.strip().lower() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()]
if current_user.lower() not in _admin_users:
    st.markdown(
        '<style>[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"]'
        ' { display: none !important; }</style>',
        unsafe_allow_html=True,
    )

is_approver = current_user.lower() in [e.lower() for e in APPROVER_EMAILS]


# ═════════════════════════════════════════════════════════════
#  NOTIFICATION HELPER
# ═════════════════════════════════════════════════════════════

def send_approval_email(
    to_emails: list[str],
    row_id: int,
    sp_test_id: str,
    old_status: str,
    new_status: str,
    requested_by: str,
):
    """Send email notification for a pending approval. Silently skips if SMTP is not configured."""
    if not SMTP_HOST or not to_emails:
        return False

    subject = f"[APPROVAL REQUIRED] test_status change – Row {row_id}"
    body = f"""
    <h3>test_status Change Approval Request</h3>
    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;">
      <tr><td><b>Row ID</b></td><td>{row_id}</td></tr>
      <tr><td><b>SP Test ID</b></td><td>{sp_test_id or 'N/A'}</td></tr>
      <tr><td><b>Current Status</b></td><td>{old_status or 'N/A'}</td></tr>
      <tr><td><b>Requested Status</b></td><td>{new_status}</td></tr>
      <tr><td><b>Requested By</b></td><td>{requested_by}</td></tr>
      <tr><td><b>Requested At</b></td><td>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}</td></tr>
    </table>
    <br>
    <p>Please log in to the <b>SN UX Table Editor</b> app → <b>Edit test_status</b> page
    to approve or reject this change.</p>
    """

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = ", ".join(to_emails)
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            if SMTP_USER and SMTP_PASS:
                server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_FROM, to_emails, msg.as_string())
        return True
    except Exception as ex:
        st.warning(f"Email notification failed: {ex}")
        return False


def notify_via_audit(row_id, sp_test_id, old_status, new_status, requested_by):
    """Write an audit event as a fallback notification when SMTP is not configured."""
    try:
        tk = get_user_token()
        conn = get_connection(tk)
        create_audit_table(conn)
        write_audit_events(conn, [{
            "event_ts": datetime.now(timezone.utc).replace(tzinfo=None),
            "event_type": "TEST_STATUS_APPROVAL_REQUEST",
            "user_name": requested_by,
            "session_id": st.session_state.get("ts_session_id", ""),
            "page_no": 5,
            "table_fqn": TABLE_FQN,
            "row_id": int(row_id),
            "col_name": "test_status",
            "old_value": old_status,
            "new_value": new_status,
            "notes": f"Approval requested. Approvers: {', '.join(APPROVER_EMAILS) or 'Not configured'}",
        }])
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════
#  DATA HELPERS
# ═════════════════════════════════════════════════════════════

def fetch_data(token):
    q = (f"SELECT row_id, sp_test_id, cl_test_id, test_status, "
         f"test_country, brand_L0, test_year, prod_catL1 "
         f"FROM {TABLE_FQN} ORDER BY row_id "
         f"-- cache_buster: {uuid.uuid4()}")
    return run_query(q, token)


def fetch_pending_approvals(token):
    q = (f"SELECT * FROM {APPROVAL_TABLE} "
         f"WHERE status = 'PENDING' ORDER BY requested_at DESC "
         f"-- cache_buster: {uuid.uuid4()}")
    return run_query(q, token)


def fetch_all_approvals(token):
    q = (f"SELECT * FROM {APPROVAL_TABLE} ORDER BY requested_at DESC LIMIT 200 "
         f"-- cache_buster: {uuid.uuid4()}")
    return run_query(q, token)


def submit_approval_request(row_id, sp_test_id, old_status, new_status, user, token):
    """Insert a PENDING approval request."""
    sql = (
        f"INSERT INTO {APPROVAL_TABLE} "
        f"(row_id, sp_test_id, old_status, new_status, requested_by, "
        f" requested_at, approver_email, status) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')"
    )
    params = [
        int(row_id), sp_test_id, old_status, new_status, user,
        datetime.now(timezone.utc).replace(tzinfo=None),
        ", ".join(APPROVER_EMAILS) if APPROVER_EMAILS else None,
    ]
    run_statement(sql, params, token)


def approve_request(approval_id, reviewer, comment, token):
    """Approve: update approval table + apply the change to master table."""
    # Get request details
    q = f"SELECT row_id, new_status FROM {APPROVAL_TABLE} WHERE approval_id = {approval_id}"
    req = run_query(q, token)
    if req.empty:
        raise ValueError(f"Approval {approval_id} not found")

    row_id = int(req.iloc[0]["row_id"])
    new_status = req.iloc[0]["new_status"]

    # 1) Apply change to master table
    run_statement(
        f"UPDATE {TABLE_FQN} SET test_status = ? WHERE row_id = ?",
        [new_status, row_id], token,
    )
    # 2) Mark approval as APPROVED
    run_statement(
        f"UPDATE {APPROVAL_TABLE} SET status = 'APPROVED', "
        f"reviewed_by = ?, reviewed_at = ?, review_comment = ? "
        f"WHERE approval_id = ?",
        [reviewer, datetime.now(timezone.utc).replace(tzinfo=None), comment, approval_id],
        token,
    )


def reject_request(approval_id, reviewer, comment, token):
    """Reject: only update approval table status."""
    run_statement(
        f"UPDATE {APPROVAL_TABLE} SET status = 'REJECTED', "
        f"reviewed_by = ?, reviewed_at = ?, review_comment = ? "
        f"WHERE approval_id = ?",
        [reviewer, datetime.now(timezone.utc).replace(tzinfo=None), comment, approval_id],
        token,
    )


# ═════════════════════════════════════════════════════════════
#  SIDEBAR FILTERS
# ═════════════════════════════════════════════════════════════

ensure_session_id("ts_session_id")

with st.sidebar:
    st.header("Filters")
    st.caption(f"User: **{current_user}**")
    if is_approver:
        st.success("You are an approver")

    filter_country = st.multiselect("test_country:", options=["ALL"], default=["ALL"],
                                     key="ts_flt_country")
    filter_year = st.multiselect("test_year:", options=["ALL"], default=["ALL"],
                                  key="ts_flt_year")

    if st.button("Fetch Data", type="primary", use_container_width=True, key="ts_fetch"):
        st.session_state["ts_data_loaded"] = True
        st.rerun()


# ═════════════════════════════════════════════════════════════
#  MAIN TABS
# ═════════════════════════════════════════════════════════════

st.markdown(
    "<h2 style='font-size:28px; font-weight:700; margin-bottom:5px;'>"
    "Edit test_status (Approval Required)</h2>",
    unsafe_allow_html=True,
)

if not _can_edit:
    st.warning(
        f"You (**{current_user}**) have **read-only** access. "
        "test_status changes require editor permissions."
    )

tab_edit, tab_pending, tab_history = st.tabs([
    "📝 Request Status Change",
    f"⏳ Pending Approvals",
    "📋 Approval History",
])


# ─── TAB 1: Request Status Change ────────────────────────────
with tab_edit:
    try:
        tk = get_user_token()
        data = fetch_data(tk)

        if data.empty:
            st.info("No data found.")
        else:
            # Update sidebar filter options dynamically
            countries = sorted(data["test_country"].dropna().unique().tolist())
            years = sorted(data["test_year"].dropna().astype(str).unique().tolist())

            st.caption(f"Showing **{len(data):,}** rows. "
                       "Edit the **new_status** column, then click Submit.")

            # Prepare editor DataFrame
            edit_df = data.copy()
            edit_df["new_status"] = edit_df["test_status"]

            display_cols = ["row_id", "sp_test_id", "test_status", "new_status",
                            "test_country", "brand_L0", "test_year"]
            edit_df = edit_df[display_cols]

            col_cfg = {
                "row_id": st.column_config.NumberColumn(to_bold("row_id"), disabled=True, pinned=True),
                "sp_test_id": st.column_config.TextColumn(to_bold("sp_test_id"), disabled=True),
                "test_status": st.column_config.TextColumn(to_bold("current_status"), disabled=True),
                "new_status": st.column_config.SelectboxColumn(
                    to_bold("new_status"),
                    options=TEST_STATUS_OPTIONS,
                    required=True,
                ),
                "test_country": st.column_config.TextColumn(to_bold("test_country"), disabled=True),
                "brand_L0": st.column_config.TextColumn(to_bold("brand_L0"), disabled=True),
                "test_year": st.column_config.TextColumn(to_bold("test_year"), disabled=True),
            }

            edited = st.data_editor(
                edit_df, use_container_width=True, hide_index=True,
                disabled=["row_id", "sp_test_id", "test_status", "test_country",
                           "brand_L0", "test_year"],
                column_config=col_cfg,
                key="ts_editor",
            )

            # Detect changes
            changed_mask = edited["new_status"] != edit_df["test_status"]
            changed = edited[changed_mask]

            if len(changed) > 0:
                st.warning(f"**{len(changed)}** row(s) have status changes pending submission.")
                st.dataframe(changed[["row_id", "sp_test_id", "test_status", "new_status"]],
                             use_container_width=True, hide_index=True)

                if not _can_edit:
                    st.error("You don't have editor permissions to submit changes.")
                elif st.button("🚀 Submit for Approval", type="primary", key="ts_submit"):
                    submitted = 0
                    for _, row in changed.iterrows():
                        try:
                            submit_approval_request(
                                row["row_id"], row.get("sp_test_id"),
                                row["test_status"], row["new_status"],
                                current_user, tk,
                            )
                            # Send notification
                            email_sent = send_approval_email(
                                APPROVER_EMAILS, row["row_id"],
                                row.get("sp_test_id"), row["test_status"],
                                row["new_status"], current_user,
                            )
                            if not email_sent:
                                notify_via_audit(
                                    row["row_id"], row.get("sp_test_id"),
                                    row["test_status"], row["new_status"],
                                    current_user,
                                )
                            submitted += 1
                        except Exception as ex:
                            st.error(f"Failed to submit row {row['row_id']}: {ex}")

                    if submitted:
                        notif_msg = (
                            f" Email sent to {', '.join(APPROVER_EMAILS)}."
                            if SMTP_HOST and APPROVER_EMAILS
                            else " (Configure SMTP & TEST_STATUS_APPROVERS in app.yaml for email notifications.)"
                        )
                        st.success(
                            f"✅ {submitted} approval request(s) submitted.{notif_msg}"
                        )
                        st.balloons()
            else:
                st.info("No status changes detected. Edit the **new_status** column to request a change.")

    except Exception as ex:
        st.error(f"Failed to load data: {ex}")


# ─── TAB 2: Pending Approvals ────────────────────────────────
with tab_pending:
    try:
        tk = get_user_token()
        pending = fetch_pending_approvals(tk)

        if pending.empty:
            st.info("No pending approval requests.")
        else:
            st.warning(f"**{len(pending)}** pending request(s)")

            for idx, req in pending.iterrows():
                with st.expander(
                    f"Row {req['row_id']} | {req.get('old_status', '?')} → "
                    f"{req.get('new_status', '?')} | by {req.get('requested_by', '?')}",
                    expanded=True,
                ):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"""
                        | Field | Value |
                        |---|---|
                        | **Row ID** | {req['row_id']} |
                        | **SP Test ID** | {req.get('sp_test_id', 'N/A')} |
                        | **Old Status** | {req.get('old_status', 'N/A')} |
                        | **New Status** | `{req.get('new_status', 'N/A')}` |
                        | **Requested By** | {req.get('requested_by', '?')} |
                        | **Requested At** | {req.get('requested_at', '?')} |
                        """)

                    with c2:
                        if is_approver:
                            comment = st.text_input(
                                "Comment (optional):",
                                key=f"comment_{req['approval_id']}",
                            )
                            bc1, bc2, _ = st.columns([1, 1, 2])
                            with bc1:
                                if st.button("✅ Approve", type="primary",
                                             key=f"approve_{req['approval_id']}"):
                                    try:
                                        approve_request(
                                            req["approval_id"], current_user, comment, tk,
                                        )
                                        st.success(
                                            f"Approved! Row {req['row_id']} test_status → "
                                            f"{req['new_status']}"
                                        )
                                        st.rerun()
                                    except Exception as ex:
                                        st.error(f"Approve failed: {ex}")

                            with bc2:
                                if st.button("❌ Reject",
                                             key=f"reject_{req['approval_id']}"):
                                    try:
                                        reject_request(
                                            req["approval_id"], current_user, comment, tk,
                                        )
                                        st.warning(f"Rejected request for Row {req['row_id']}")
                                        st.rerun()
                                    except Exception as ex:
                                        st.error(f"Reject failed: {ex}")
                        else:
                            st.info("Only designated approvers can approve/reject.")

    except Exception as ex:
        st.error(f"Failed to load pending approvals: {ex}")


# ─── TAB 3: Approval History ─────────────────────────────────
with tab_history:
    try:
        tk = get_user_token()
        history = fetch_all_approvals(tk)

        if history.empty:
            st.info("No approval history yet.")
        else:
            # Color-code status
            def status_color(val):
                if val == "APPROVED":
                    return "background-color: #d4edda"
                elif val == "REJECTED":
                    return "background-color: #f8d7da"
                elif val == "PENDING":
                    return "background-color: #fff3cd"
                return ""

            display_cols = ["approval_id", "row_id", "sp_test_id", "old_status",
                            "new_status", "requested_by", "requested_at",
                            "status", "reviewed_by", "reviewed_at", "review_comment"]
            show_cols = [c for c in display_cols if c in history.columns]
            styled = history[show_cols].style.applymap(
                status_color, subset=["status"]
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)
    except Exception as ex:
        st.error(f"Failed to load history: {ex}")
