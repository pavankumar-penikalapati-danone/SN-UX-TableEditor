# ============================================================
# 6_Approval_Dashboard.py  –  Approve / Reject test_status
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
    generate_test_id, compute_seq,
)

# ── Constants ────────────────────────────────────────────────
APPROVAL_TABLE = os.getenv(
    "APPROVAL_TABLE_FQN",
    f"{CATALOG}.{SCHEMA}.ux_sn_test_status_approvals",
)
APPROVER_EMAILS = [
    e.strip() for e in os.getenv("TEST_STATUS_APPROVERS", "").split(",") if e.strip()
]

# All 14 mandatory context columns
CONTEXT_COLS = [
    "match_type", "sp_test_id", "cl_test_id",
    "brand_L1", "branded_flag", "flavour_pack",
    "prod_type", "test_country", "test_year",
    "zone", "CBU", "brand_L0", "prod_catL1", "test_status",
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

_hide_css = []
if current_user.lower() not in _admin_users:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"] { display: none !important; }')
if not is_approver:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Approval_Dashboard"] { display: none !important; }')
if _hide_css:
    st.markdown(f'<style>{"".join(_hide_css)}</style>', unsafe_allow_html=True)

if not is_approver:
    st.error(f"**Access Denied.** This page is restricted to designated approvers only.\n\nYour account: `{current_user}`")
    st.stop()


# ═════════════════════════════════════════════════════════════
#  DATA HELPERS
# ═════════════════════════════════════════════════════════════

def _ctx_sql(alias="m"):
    return ", ".join([f"{alias}.{c} AS {c}" for c in CONTEXT_COLS])


def fetch_pending_with_context(tk):
    q = (
        f"SELECT a.approval_id, a.row_id, "
        f"a.sp_test_id AS appr_sp_test_id, "
        f"a.old_status, a.new_status, "
        f"{_ctx_sql('m')}, "
        f"a.requested_by, a.requested_at "
        f"FROM {APPROVAL_TABLE} a "
        f"LEFT JOIN {TABLE_FQN} m ON a.row_id = m.row_id "
        f"WHERE a.status = 'PENDING' "
        f"ORDER BY a.requested_at DESC "
        f"-- cb: {uuid.uuid4()}"
    )
    df = run_query(q, tk)
    if "appr_sp_test_id" in df.columns and "sp_test_id" in df.columns:
        mask = df["sp_test_id"].isna() | (df["sp_test_id"].astype(str).str.strip() == "")
        df.loc[mask, "sp_test_id"] = df.loc[mask, "appr_sp_test_id"]
        df.drop(columns=["appr_sp_test_id"], inplace=True, errors="ignore")
    return df


def fetch_history(tk, limit=200):
    q = (
        f"SELECT a.approval_id, a.row_id, "
        f"a.sp_test_id AS appr_sp_test_id, "
        f"a.old_status, a.new_status, "
        f"{_ctx_sql('m')}, "
        f"a.status AS decision, "
        f"a.requested_by, a.requested_at, "
        f"a.reviewed_by, a.reviewed_at, a.review_comment "
        f"FROM {APPROVAL_TABLE} a "
        f"LEFT JOIN {TABLE_FQN} m ON a.row_id = m.row_id "
        f"WHERE a.status != 'PENDING' "
        f"ORDER BY a.reviewed_at DESC LIMIT {limit} "
        f"-- cb: {uuid.uuid4()}"
    )
    df = run_query(q, tk)
    if "appr_sp_test_id" in df.columns and "sp_test_id" in df.columns:
        mask = df["sp_test_id"].isna() | (df["sp_test_id"].astype(str).str.strip() == "")
        df.loc[mask, "sp_test_id"] = df.loc[mask, "appr_sp_test_id"]
        df.drop(columns=["appr_sp_test_id"], inplace=True, errors="ignore")
    return df


def _regenerate_sp_test_id(row_id, tk):
    """Re-generate sp_test_id for a row after approval (same logic as bulk_update)."""
    try:
        row_df = run_query(
            f"SELECT * FROM {TABLE_FQN} WHERE row_id = {row_id} "
            f"-- cb: {uuid.uuid4()}", tk)
        if row_df.empty:
            return
        row = row_df.iloc[0]
        seq = compute_seq(row)
        new_sid = generate_test_id(
            row.get("match_type"), row.get("test_year"),
            row.get("test_country"), row.get("prod_catL1"),
            row.get("brand_L0"), row.get("retest_year"), seq,
        )
        run_statement(
            f"UPDATE {TABLE_FQN} SET sp_test_id = '{new_sid}' "
            f"WHERE row_id = {row_id}",
            [], tk)
    except Exception:
        pass  # sp_test_id regeneration is best-effort


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

        # 1. Commit test_status to master table
        run_statement(
            f"UPDATE {TABLE_FQN} SET test_status = '{new_status}' "
            f"WHERE row_id = {row_id}",
            [], tk)

        # 2. Regenerate sp_test_id (same as bulk_update does)
        _regenerate_sp_test_id(row_id, tk)

        # 3. Mark APPROVED in approval table
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        safe_comment = (comment or "").replace("'", "")
        run_statement(
            f"UPDATE {APPROVAL_TABLE} SET status = 'APPROVED', "
            f"reviewed_by = '{reviewer}', "
            f"reviewed_at = '{now_ts}', "
            f"review_comment = '{safe_comment}' "
            f"WHERE approval_id = {aid}",
            [], tk)
        approved += 1
    return approved


def reject_rows(approval_ids, reviewer, comment, tk):
    rejected = 0
    for aid in approval_ids:
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        safe_comment = (comment or "").replace("'", "")
        run_statement(
            f"UPDATE {APPROVAL_TABLE} SET status = 'REJECTED', "
            f"reviewed_by = '{reviewer}', "
            f"reviewed_at = '{now_ts}', "
            f"review_comment = '{safe_comment}' "
            f"WHERE approval_id = {aid}",
            [], tk)
        rejected += 1
    return rejected


# ═════════════════════════════════════════════════════════════
#  SIDEBAR
# ═════════════════════════════════════════════════════════════

ensure_session_id("appr_session_id")

with st.sidebar:
    st.header("Approval Dashboard")
    st.caption(f"Approver: **{current_user}**")
    if st.button("\U0001f504 Refresh", type="primary",
                 use_container_width=True, key="appr_refresh"):
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

tab_pending, tab_history = st.tabs(["\U0001f4cb Pending Approvals", "\U0001f4dc Approval History"])


# ─── TAB 1: Pending Approvals ────────────────────────────────
with tab_pending:
    try:
        tk = get_user_token()
        pending = fetch_pending_with_context(tk)

        if pending.empty:
            st.success("No pending approval requests. All clear!")
        else:
            st.warning(f"**{len(pending)}** request(s) awaiting your review")

            pending_display = pending.copy()
            pending_display.insert(0, "_select", False)

            key_cols = ["_select", "approval_id", "row_id"]
            change_cols = ["old_status", "new_status"]
            context_cols = [c for c in CONTEXT_COLS if c in pending_display.columns]
            meta_cols = ["requested_by", "requested_at"]
            all_display_cols = key_cols + change_cols + context_cols + meta_cols
            available_cols = [c for c in all_display_cols if c in pending_display.columns]

            col_cfg = {
                "_select": st.column_config.CheckboxColumn(
                    to_bold("Select"), pinned=True, width="small"),
                "approval_id": st.column_config.NumberColumn(
                    to_bold("ID"), disabled=True, width="small"),
                "row_id": st.column_config.NumberColumn(
                    to_bold("row_id"), disabled=True, width="small"),
                "old_status": st.column_config.TextColumn(
                    to_bold("Before (old_status)"), disabled=True),
                "new_status": st.column_config.TextColumn(
                    to_bold("After (new_status)"), disabled=True),
                "requested_by": st.column_config.TextColumn(
                    to_bold("requested_by"), disabled=True),
                "requested_at": st.column_config.DatetimeColumn(
                    to_bold("requested_at"), disabled=True),
            }
            for c in context_cols:
                col_cfg[c] = st.column_config.TextColumn(to_bold(c), disabled=True)

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

                comment = st.text_input("Review comment (optional):", key="appr_review_comment")

                col1, col2, _ = st.columns([1.5, 1.5, 4])
                with col1:
                    approve_btn = st.button(
                        f"\u2705 Approve {len(selected)} Selected",
                        type="primary", key="appr_approve_btn")
                with col2:
                    reject_btn = st.button(
                        f"\u274c Reject {len(selected)} Selected",
                        key="appr_reject_btn")

                if approve_btn:
                    aids = selected["approval_id"].tolist()
                    with st.spinner("Approving, committing changes, and regenerating sp_test_id..."):
                        count = approve_rows(aids, current_user, comment, tk)
                    if count > 0:
                        st.success(
                            f"Approved **{count}** request(s). "
                            f"Changes committed to master table + sp_test_id regenerated. "
                            f"Click **Refresh Data** on Edit Test Status page to see updates.")
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
                            f"Rejected **{count}** request(s). No changes made to master table.")
                        time.sleep(1)
                        st.rerun()
            else:
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

            hist_key = ["approval_id", "row_id"]
            hist_change = ["old_status", "new_status"]
            hist_ctx = [c for c in CONTEXT_COLS if c in history.columns]
            hist_decision = ["decision"]
            hist_meta = ["requested_by", "requested_at",
                         "reviewed_by", "reviewed_at", "review_comment"]
            show_cols = [c for c in hist_key + hist_change + hist_ctx
                         + hist_decision + hist_meta if c in history.columns]

            def _decision_color(val):
                if val == "APPROVED":
                    return "background-color: #d4edda; color: #155724;"
                elif val == "REJECTED":
                    return "background-color: #f8d7da; color: #721c24;"
                return ""

            styled = history[show_cols].style.applymap(
                _decision_color, subset=["decision"] if "decision" in show_cols else [])
            st.dataframe(styled, use_container_width=True, hide_index=True)

            mc1, mc2, mc3 = st.columns(3)
            with mc1:
                st.metric("Total Decisions", len(history))
            with mc2:
                st.metric("Approved", len(history[history["decision"] == "APPROVED"]))
            with mc3:
                st.metric("Rejected", len(history[history["decision"] == "REJECTED"]))

    except Exception as ex:
        st.error(f"Failed to load history: {ex}")
