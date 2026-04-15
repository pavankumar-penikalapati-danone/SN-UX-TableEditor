# ============================================================
# 5_edit_test_status.py  –  test_status Edit with Approval
# ============================================================
# Workflow:
#   - If new status == "Cancelled"  →  commit directly (no approval)
#   - If new status != "Cancelled"  →  PENDING approval request
#   - Approver opens "Pending Approvals" tab → approve / reject
#   - Approve  →  UPDATE committed to master table
#   - Reject   →  change discarded, no DB update
# ============================================================

from __future__ import annotations

import math
import os
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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

# "Cancelled" bypasses approval – everything else needs approval
DIRECT_COMMIT_STATUSES = {"Cancelled"}

APPROVER_EMAILS = [
    e.strip() for e in os.getenv("TEST_STATUS_APPROVERS", "").split(",") if e.strip()
]

SMTP_HOST  = os.getenv("SMTP_HOST", "")
SMTP_PORT  = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER  = os.getenv("SMTP_USER", "")
SMTP_PASS  = os.getenv("SMTP_PASS", "")
SMTP_FROM  = os.getenv("SMTP_FROM", SMTP_USER)

TS_PAGE_SIZE = int(os.getenv("TEST_STATUS_PAGE_SIZE", str(PAGE_SIZE)))


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

_admin_users = [u.strip().lower() for u in os.environ.get("ADMIN_USERS", "").split(",") if u.strip()]
if current_user.lower() not in _admin_users:
    st.markdown(
        '<style>[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"]'
        ' { display: none !important; }</style>',
        unsafe_allow_html=True,
    )

is_approver = current_user.lower() in [e.lower() for e in APPROVER_EMAILS]


# ═════════════════════════════════════════════════════════════
#  NOTIFICATION HELPERS
# ═════════════════════════════════════════════════════════════

def send_approval_email(to_emails, row_id, sp_test_id, old_status, new_status, requested_by):
    if not SMTP_HOST or not to_emails:
        return False
    subject = f"[APPROVAL REQUIRED] test_status change - Row {row_id}"
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
    <br><p>Please log in to <b>SN UX Table Editor</b> &rarr; <b>Edit test_status</b> &rarr;
    <b>Pending Approvals</b> tab to approve or reject.</p>
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


def notify_via_audit(row_id, sp_test_id, old_status, new_status, requested_by, event_type="TEST_STATUS_APPROVAL_REQUEST"):
    try:
        tk = get_user_token()
        conn = get_connection(tk)
        create_audit_table(conn)
        write_audit_events(conn, [{
            "event_ts": datetime.now(timezone.utc).replace(tzinfo=None),
            "event_type": event_type,
            "user_name": requested_by,
            "session_id": st.session_state.get("ts_session_id", ""),
            "page_no": 5,
            "table_fqn": TABLE_FQN,
            "row_id": int(row_id),
            "col_name": "test_status",
            "old_value": old_status,
            "new_value": new_status,
            "notes": f"Approvers: {', '.join(APPROVER_EMAILS) or 'N/A'}",
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
         f"-- cb: {uuid.uuid4()}")
    return run_query(q, token)


def fetch_pending_approvals(token):
    q = (f"SELECT * FROM {APPROVAL_TABLE} "
         f"WHERE status = 'PENDING' ORDER BY requested_at DESC "
         f"-- cb: {uuid.uuid4()}")
    return run_query(q, token)


def fetch_all_approvals(token):
    q = (f"SELECT * FROM {APPROVAL_TABLE} ORDER BY requested_at DESC LIMIT 200 "
         f"-- cb: {uuid.uuid4()}")
    return run_query(q, token)


# ── Direct commit (for Cancelled) ────────────────────────────
def direct_commit(row_id, old_status, new_status, sp_test_id, user, token):
    """Directly update the master table (no approval needed)."""
    run_statement(
        f"UPDATE {TABLE_FQN} SET test_status = ? WHERE row_id = ?",
        [new_status, int(row_id)], token,
    )
    # Also log in the approval table with APPROVED status for audit trail
    run_statement(
        f"INSERT INTO {APPROVAL_TABLE} "
        f"(row_id, sp_test_id, old_status, new_status, requested_by, "
        f" requested_at, approver_email, status, reviewed_by, reviewed_at, review_comment) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, 'APPROVED', ?, ?, 'Auto-approved (Cancelled)')",
        [int(row_id), sp_test_id, old_status, new_status, user,
         datetime.now(timezone.utc).replace(tzinfo=None),
         "N/A",
         user,
         datetime.now(timezone.utc).replace(tzinfo=None)],
        token,
    )


# ── Submit approval request (for non-Cancelled) ──────────────
def submit_approval_request(row_id, sp_test_id, old_status, new_status, user, token):
    """Insert a PENDING row – the change is NOT applied yet."""
    run_statement(
        f"INSERT INTO {APPROVAL_TABLE} "
        f"(row_id, sp_test_id, old_status, new_status, requested_by, "
        f" requested_at, approver_email, status) "
        f"VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING')",
        [int(row_id), sp_test_id, old_status, new_status, user,
         datetime.now(timezone.utc).replace(tzinfo=None),
         ", ".join(APPROVER_EMAILS) if APPROVER_EMAILS else None],
        token,
    )


# ── Approve: commit the change to master table ───────────────
def approve_request(approval_id, reviewer, comment, token):
    req = run_query(
        f"SELECT row_id, new_status FROM {APPROVAL_TABLE} WHERE approval_id = {approval_id}",
        token,
    )
    if req.empty:
        raise ValueError(f"Approval {approval_id} not found")
    row_id = int(req.iloc[0]["row_id"])
    new_status = req.iloc[0]["new_status"]

    # 1) Commit change to master table
    run_statement(
        f"UPDATE {TABLE_FQN} SET test_status = ? WHERE row_id = ?",
        [new_status, row_id], token,
    )
    # 2) Mark as APPROVED
    run_statement(
        f"UPDATE {APPROVAL_TABLE} SET status = 'APPROVED', "
        f"reviewed_by = ?, reviewed_at = ?, review_comment = ? "
        f"WHERE approval_id = ?",
        [reviewer, datetime.now(timezone.utc).replace(tzinfo=None), comment, approval_id],
        token,
    )
    return row_id, new_status


# ── Reject: discard – no DB update ───────────────────────────
def reject_request(approval_id, reviewer, comment, token):
    run_statement(
        f"UPDATE {APPROVAL_TABLE} SET status = 'REJECTED', "
        f"reviewed_by = ?, reviewed_at = ?, review_comment = ? "
        f"WHERE approval_id = ?",
        [reviewer, datetime.now(timezone.utc).replace(tzinfo=None), comment, approval_id],
        token,
    )


# ═════════════════════════════════════════════════════════════
#  SIDEBAR FILTERS  (same pattern as edit page)
# ═════════════════════════════════════════════════════════════

ensure_session_id("ts_session_id")

with st.sidebar:
    st.header("Filters")
    st.caption(f"User: **{current_user}**")
    if is_approver:
        st.success("You are an approver")

    if "ts_data" in st.session_state:
        _raw = st.session_state["ts_data"]

        def _ts_filter_toggle(col_key):
            def _on_change():
                sel = st.session_state[col_key]
                if "ALL" in sel and len(sel) > 1 and sel[0] == "ALL":
                    st.session_state[col_key] = [v for v in sel if v != "ALL"]
                elif ("ALL" in sel and sel[-1] == "ALL") or len(sel) == 0:
                    st.session_state[col_key] = ["ALL"]
            return _on_change

        for col in ["test_year", "test_country", "brand_L0"]:
            if col in _raw.columns:
                raw_vals = _raw[col].dropna().unique()
                unique_vals = sorted([str(v) for v in raw_vals])
                st.multiselect(
                    f"Select {col}:",
                    options=["ALL"] + unique_vals,
                    default=["ALL"],
                    key=f"ts_flt_{col}",
                    on_change=_ts_filter_toggle(f"ts_flt_{col}"),
                )

        fc1, fc2 = st.columns(2)
        with fc1:
            if st.button("Apply Filters", type="primary", use_container_width=True, key="ts_apply"):
                ts_filters = {}
                for col in ["test_year", "test_country", "brand_L0"]:
                    sel = st.session_state.get(f"ts_flt_{col}", ["ALL"])
                    if sel and "ALL" not in sel:
                        ts_filters[col] = sel
                st.session_state["ts_active_filters"] = ts_filters
                st.session_state["ts_page"] = 1
                st.rerun()
        with fc2:
            if st.button("Clear Filters", use_container_width=True, key="ts_clear"):
                st.session_state["ts_active_filters"] = {}
                st.session_state["ts_page"] = 1
                st.rerun()


# ═════════════════════════════════════════════════════════════
#  MAIN CONTENT
# ═════════════════════════════════════════════════════════════

st.markdown(
    "<h2 style='font-size:28px; font-weight:700; margin-bottom:5px;'>"
    "Edit test_status</h2>",
    unsafe_allow_html=True,
)
st.markdown(
    "> **Cancelled** &rarr; committed immediately &nbsp;|&nbsp; "
    "**All other statuses** &rarr; sent for approval",
    unsafe_allow_html=True,
)

if not _can_edit:
    st.warning(f"You (**{current_user}**) have **read-only** access.")

tab_edit, tab_pending, tab_history = st.tabs([
    "Request Status Change",
    "Pending Approvals",
    "Approval History",
])


# ─── TAB 1: Request Status Change (paginated) ───────────────
with tab_edit:
    try:
        tk = get_user_token()

        # Fetch / cache
        if "ts_data" not in st.session_state:
            with st.spinner("Loading data..."):
                st.session_state["ts_data"] = fetch_data(tk)
                st.session_state["ts_page"] = 1
                st.rerun()

        data = st.session_state["ts_data"]

        # Apply sidebar filters
        active_filters = st.session_state.get("ts_active_filters", {})
        if active_filters:
            filtered = data.copy()
            for col, vals in active_filters.items():
                if col in filtered.columns:
                    filtered = filtered[filtered[col].astype(str).isin(vals)]
            data = filtered.reset_index(drop=True)

        if data.empty:
            st.info("No data found (check filters).")
        else:
            # ── Pagination ────────────────────────────────────
            total_rows = len(data)
            total_pages = max(1, math.ceil(total_rows / TS_PAGE_SIZE))
            if "ts_page" not in st.session_state:
                st.session_state["ts_page"] = 1
            page = st.session_state["ts_page"]
            si = (page - 1) * TS_PAGE_SIZE
            ei = si + TS_PAGE_SIZE

            st.caption(
                f"Showing rows **{si + 1}** to **{min(ei, total_rows)}** "
                f"of **{total_rows:,}** &nbsp;|&nbsp; Page {page}/{total_pages}"
            )

            page_df = data.iloc[si:ei].copy().reset_index(drop=True)
            page_df["new_status"] = page_df["test_status"]

            display_cols = ["row_id", "sp_test_id", "test_status", "new_status",
                            "test_country", "brand_L0", "test_year"]
            page_df = page_df[[c for c in display_cols if c in page_df.columns]]

            col_cfg = {
                "row_id": st.column_config.NumberColumn(to_bold("row_id"), disabled=True, pinned=True),
                "sp_test_id": st.column_config.TextColumn(to_bold("sp_test_id"), disabled=True),
                "test_status": st.column_config.TextColumn(to_bold("current_status"), disabled=True),
                "new_status": st.column_config.SelectboxColumn(
                    to_bold("new_status"), options=TEST_STATUS_OPTIONS, required=True,
                ),
                "test_country": st.column_config.TextColumn(to_bold("test_country"), disabled=True),
                "brand_L0": st.column_config.TextColumn(to_bold("brand_L0"), disabled=True),
                "test_year": st.column_config.TextColumn(to_bold("test_year"), disabled=True),
            }

            edited = st.data_editor(
                page_df, use_container_width=True, hide_index=True,
                disabled=["row_id", "sp_test_id", "test_status",
                           "test_country", "brand_L0", "test_year"],
                column_config=col_cfg,
                key=f"ts_editor_{page}",
            )

            # ── Detect changes ────────────────────────────────
            changed_mask = edited["new_status"] != page_df["test_status"]
            changed = edited[changed_mask]

            if len(changed) > 0:
                # Split into direct-commit vs needs-approval
                direct_rows = changed[changed["new_status"].isin(DIRECT_COMMIT_STATUSES)]
                approval_rows = changed[~changed["new_status"].isin(DIRECT_COMMIT_STATUSES)]

                st.markdown("---")

                if len(direct_rows) > 0:
                    st.success(
                        f"**{len(direct_rows)}** row(s) set to **Cancelled** "
                        f"&rarr; will be committed **immediately**"
                    )
                    st.dataframe(
                        direct_rows[["row_id", "sp_test_id", "test_status", "new_status"]],
                        use_container_width=True, hide_index=True,
                    )

                if len(approval_rows) > 0:
                    st.warning(
                        f"**{len(approval_rows)}** row(s) &rarr; "
                        f"require **approval** before committing"
                    )
                    st.dataframe(
                        approval_rows[["row_id", "sp_test_id", "test_status", "new_status"]],
                        use_container_width=True, hide_index=True,
                    )

                if not _can_edit:
                    st.error("You don't have editor permissions to submit changes.")
                elif st.button("Submit Changes", type="primary", key="ts_submit"):
                    committed = 0
                    pending = 0

                    # ── Direct commits (Cancelled) ────────────
                    for _, row in direct_rows.iterrows():
                        try:
                            direct_commit(
                                row["row_id"], row["test_status"],
                                row["new_status"], row.get("sp_test_id"),
                                current_user, tk,
                            )
                            notify_via_audit(
                                row["row_id"], row.get("sp_test_id"),
                                row["test_status"], row["new_status"],
                                current_user, "TEST_STATUS_DIRECT_COMMIT",
                            )
                            committed += 1
                        except Exception as ex:
                            st.error(f"Failed to commit row {row['row_id']}: {ex}")

                    # ── Approval requests (non-Cancelled) ─────
                    for _, row in approval_rows.iterrows():
                        try:
                            submit_approval_request(
                                row["row_id"], row.get("sp_test_id"),
                                row["test_status"], row["new_status"],
                                current_user, tk,
                            )
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
                            pending += 1
                        except Exception as ex:
                            st.error(f"Failed to submit row {row['row_id']}: {ex}")

                    # ── Result summary ────────────────────────
                    msgs = []
                    if committed:
                        msgs.append(f"**{committed}** row(s) committed directly (Cancelled)")
                    if pending:
                        notif = (
                            f"Email sent to approvers."
                            if SMTP_HOST and APPROVER_EMAILS
                            else "Check **Pending Approvals** tab."
                        )
                        msgs.append(f"**{pending}** row(s) sent for approval. {notif}")

                    if msgs:
                        st.success(" | ".join(msgs))
                        # Refresh cached data
                        st.session_state["ts_data"] = fetch_data(tk)
                        st.balloons()
            else:
                st.info("Edit the **new_status** column to request a change.")

            # ── Pagination controls ───────────────────────────
            def _ts_go_page(delta):
                st.session_state["ts_page"] += delta
                st.rerun()

            pg_prev, pg_lbl, pg_input, pg_of, pg_next = st.columns(
                [1, 0.5, 0.6, 2, 1], vertical_alignment="center",
            )
            with pg_prev:
                if st.button("Previous", use_container_width=True,
                             disabled=page <= 1, key="ts_prev_page"):
                    _ts_go_page(-1)
            with pg_lbl:
                st.markdown(
                    "<div style='text-align:right; font-weight:600; "
                    "white-space:nowrap;'>Page</div>",
                    unsafe_allow_html=True,
                )
            with pg_input:
                go_page = st.number_input(
                    "Go to page", min_value=1, max_value=total_pages,
                    value=page, step=1, key="ts_go_page_input",
                    label_visibility="collapsed",
                )
                if go_page != page:
                    st.session_state["ts_page"] = go_page
                    st.rerun()
            with pg_of:
                st.markdown(
                    f"<div style='font-weight:600; white-space:nowrap;'>"
                    f"of {total_pages} &nbsp;|&nbsp; {total_rows:,} rows total</div>",
                    unsafe_allow_html=True,
                )
            with pg_next:
                if st.button("Next", use_container_width=True,
                             disabled=page >= total_pages, key="ts_next_page"):
                    _ts_go_page(1)

            # Refresh
            if st.button("Refresh Data", key="ts_refresh"):
                st.session_state["ts_data"] = fetch_data(tk)
                st.session_state["ts_page"] = 1
                st.rerun()

    except Exception as ex:
        st.error(f"Failed to load data: {ex}")


# ─── TAB 2: Pending Approvals (approver view) ────────────────
with tab_pending:
    try:
        tk = get_user_token()
        pending_df = fetch_pending_approvals(tk)

        if pending_df.empty:
            st.info("No pending approval requests.")
        else:
            st.warning(f"**{len(pending_df)}** pending request(s) awaiting review")

            for idx, req in pending_df.iterrows():
                with st.expander(
                    f"Row {req['row_id']}  |  "
                    f"{req.get('old_status', '?')} -> {req.get('new_status', '?')}  |  "
                    f"by {req.get('requested_by', '?')}",
                    expanded=True,
                ):
                    c1, c2 = st.columns(2)
                    with c1:
                        st.markdown(f"""
| Field | Value |
|---|---|
| **Row ID** | {req['row_id']} |
| **SP Test ID** | {req.get('sp_test_id', 'N/A')} |
| **Current Status** | {req.get('old_status', 'N/A')} |
| **Requested Status** | `{req.get('new_status', 'N/A')}` |
| **Requested By** | {req.get('requested_by', '?')} |
| **Requested At** | {req.get('requested_at', '?')} |
                        """)
                    with c2:
                        if is_approver:
                            comment = st.text_input("Comment (optional):",
                                                     key=f"comment_{req['approval_id']}")
                            bc1, bc2, _ = st.columns([1, 1, 2])
                            with bc1:
                                if st.button("Approve", type="primary",
                                             key=f"approve_{req['approval_id']}"):
                                    try:
                                        rid, ns = approve_request(req["approval_id"], current_user, comment, tk)
                                        st.success(
                                            f"Approved & committed! "
                                            f"Row {rid} test_status -> {ns}"
                                        )
                                        # Refresh cached data
                                        if "ts_data" in st.session_state:
                                            del st.session_state["ts_data"]
                                        st.rerun()
                                    except Exception as ex:
                                        st.error(f"Approve failed: {ex}")
                            with bc2:
                                if st.button("Reject", key=f"reject_{req['approval_id']}"):
                                    try:
                                        reject_request(req["approval_id"], current_user, comment, tk)
                                        st.warning(
                                            f"Rejected. Row {req['row_id']} "
                                            f"change discarded (no DB update)."
                                        )
                                        st.rerun()
                                    except Exception as ex:
                                        st.error(f"Reject failed: {ex}")
                        elif current_user.lower() == req.get("requested_by", "").lower():
                            st.info("Your request is pending approver review.")
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
            display_cols = ["approval_id", "row_id", "sp_test_id", "old_status",
                            "new_status", "requested_by", "requested_at",
                            "status", "reviewed_by", "reviewed_at", "review_comment"]
            show_cols = [c for c in display_cols if c in history.columns]

            def _status_color(val):
                if val == "APPROVED":
                    return "background-color: #d4edda"
                elif val == "REJECTED":
                    return "background-color: #f8d7da"
                elif val == "PENDING":
                    return "background-color: #fff3cd"
                return ""

            styled = history[show_cols].style.applymap(_status_color, subset=["status"])
            st.dataframe(styled, use_container_width=True, hide_index=True)
    except Exception as ex:
        st.error(f"Failed to load history: {ex}")
