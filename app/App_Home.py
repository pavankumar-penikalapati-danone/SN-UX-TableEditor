import streamlit as st
from config import (
    get_user_identity, ADMIN_USERS,
    APP_LOGO_IMAGE, APP_LOGO_ICON, APP_LOGO_LINK, APP_TITLE,
    SHARED_CSS,
)

st.set_page_config(page_title=APP_TITLE, layout='wide', initial_sidebar_state='expanded')
st.markdown(SHARED_CSS, unsafe_allow_html=True)

st.logo(
    APP_LOGO_IMAGE, size="large",
    icon_image=APP_LOGO_ICON,
    link=APP_LOGO_LINK,
)

# ═════════════════════════════════════════════════════════════
#  ACCESS CONTROL — resolve user identity + authorizations
# ═════════════════════════════════════════════════════════════
try:
    _current_user = get_user_identity()
except Exception:
    _current_user = ""

_is_admin = _current_user.lower() in ADMIN_USERS if _current_user else False

# ═════════════════════════════════════════════════════════════
#  HIDE UNAUTHORIZED PAGES FROM SIDEBAR
# ═════════════════════════════════════════════════════════════
# Streamlit auto-discovers pages/ and renders them as sidebar
# nav links.  We inject CSS to hide links the user should not
# see.  Each restricted page ALSO performs its own server-side
# check, so this is defence-in-depth (UI only).
_approver_emails = [e.strip().lower() for e in __import__("os").environ.get("TEST_STATUS_APPROVERS", "").split(",") if e.strip()]
_hide_css = []
if not _is_admin:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Admin_Table_Editor"] { display: none !important; }')
if _current_user.lower() not in _approver_emails:
    _hide_css.append('[data-testid="stSidebarNav"] a[href*="Approval_Dashboard"] { display: none !important; }')
if _hide_css:
    st.markdown(f'<style>{"".join(_hide_css)}</style>', unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════
#  HOME PAGE CONTENT
# ═════════════════════════════════════════════════════════════

# ── Page 1: Reader (always visible) ─────────────────────────
st.title("Table READ view & Insights")
st.write("A page with two tabs. One for Table statistics & charts. Another for Table READ view with filter, sort and pagination.")
if st.button("Go to Table READ view page"):
    st.switch_page("pages/1_Table_Data_Reader.py")

# ── Page 2: Editor ───────────────────────────────────────────
st.title("Table EDIT view")
st.write("Table EDIT view with filter, sort, pagination and bulk file upload")
if st.button("Go to Table Edit view Page"):
    st.switch_page("pages/2_Table_Data_Editor.py")

# ── Page 5: Admin (only for admin users) ────────────────────
if _is_admin:
    st.title("🔒 Admin Table Editor")
    st.write("Admin-only access. Edit Master Table or Mapping Codes with per-user column visibility.")
    if st.button("Go to Admin Table Editor"):
        st.switch_page("pages/4_Admin_Table_Editor.py")
