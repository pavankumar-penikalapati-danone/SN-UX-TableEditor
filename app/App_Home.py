import streamlit as st

st.set_page_config(page_title='SN UX Table Editor', layout='wide', initial_sidebar_state='expanded')
st.markdown("""
<style>

    /* --------------------------------------------- */
    /* REDUCE SIDEBAR WIDTH (WORKS IN ALL PAGES)     */
    /* --------------------------------------------- */

    /* Sidebar container */
    [data-testid="stSidebar"] {
        width: 12rem !important;       /* CHOOSE SIZE */
        min-width: 12rem !important;
        max-width: 12rem !important;
    }

    /* Reduce internal padding */
    [data-testid="stSidebar"] > div:first-child {
        padding: 0.5rem 0.8rem !important;
    }

    /* Compact sidebar text */
    [data-testid="stSidebar"] * {
        font-size: 13px !important;
    }

</style>
""", unsafe_allow_html=True)
# 🔥 Global page layout + font reducer + margins (USE ON ALL PAGES)
st.markdown("""
<style>

    /* --------------------------------------------- */
    /* PAGE MARGINS + TOP SPACING (your requirement) */
    /* --------------------------------------------- */
    .block-container {
        padding-top: 2.75rem !important;     /* top space */
        padding-left: 2rem !important;    /* left margin */
        padding-right: 2rem !important;   /* right margin */
    }

    /* --------------------------------------------- */
    /* GLOBAL FONT SIZE REDUCER (safe for all pages) */
    /* --------------------------------------------- */
    html, body, [class*="css"] {
        font-size: 14px !important;
    }

    /* Title (st.title) */
    h1 {
        font-size: 28px !important;
        font-weight: 700 !important;
        margin-top: 1rem !important;
        margin-bottom: 0.5rem !important;
    }

    /* Headers (st.header / h2 / h3 etc.) */
    h2 {
        font-size: 26px !important;
        font-weight: 650 !important;
    }
    h3 {
        font-size: 18px !important;
        font-weight: 600 !important;
    }
    h4 {
        font-size: 16px !important;
    }

    /* Tabs */
    .stTabs [data-baseweb="tab"] {
        font-size: 14px !important;
    }

    /* Expander header */
    .streamlit-expanderHeader {
        font-size: 15px !important;
    }

    /* Buttons */
    .stButton > button {
        font-size: 13px !important;
        padding: 4px 10px !important;
    }

    /* Sidebars */
    .sidebar .css-1d391kg, .sidebar .css-1n76uvr {
        font-size: 14px !important;
    }

    /* DataFrames (table fonts) */
    .dataframe td, .dataframe th {
        font-size: 13px !important;
    }

    /* Compact table rows */
    .stDataFrame tbody tr td {
        padding-top: 4px !important;
        padding-bottom: 4px !important;
    }

</style>
""", unsafe_allow_html=True)


st.logo("app/assets/DANONE_LOGO_HORIZONTAL.png", size="large",
        icon_image="app/assets/DANONE_LOGO_HORIZONTAL.png", 
        link="https://commons.wikimedia.org/wiki/File:DANONE_LOGO_HORIZONTAL.png")

st.title("Table READ view & Insights")
st.write("A page with two tabs. One for Table statistics & charts. Another for Table READ view with filter, sort and pagination.")
if st.button("Go to Table READ view page"):
    st.switch_page("pages/1_Table_Data_Reader.py") # Path must be relative to the entry point

st.title("Table EDIT view")
st.write("Table EDIT view with filter, sort, pagination and bulk file upload")
if st.button("Go to Table Edit view Page"):
    st.switch_page("pages/2_Table_Data_Editor.py") # Path must be relative to the entry point

st.title("🔒 Admin Table Editor")
st.write("Admin-only access. Edit Master Table or Mapping Codes with per-user column visibility.")
if st.button("Go to Admin Table Editor"):
    st.switch_page("pages/5_Admin_Table_Editor.py")
