# ============================================================
# config.py  –  Shared configuration & utilities for all pages
# ============================================================
# All configurable constants are read from app.yaml env vars.
# Pages 1 (Reader), 2 (Editor) and 5 (Admin) import from here,
# ensuring ZERO duplication of constants, helpers, and CSS.
#
# To change settings: edit app.yaml → redeploy the app.
# ============================================================

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import certifi
import pandas as pd
import streamlit as st
from databricks import sql
from databricks.sdk.core import Config
from dotenv import load_dotenv

# ── Environment & SSL ────────────────────────────────────────
if not os.getenv("DATABRICKS_RUNTIME_VERSION"):
    load_dotenv()

CA_FILE = os.getenv("DATABRICKS_CA_FILE", certifi.where())
os.environ["SSL_CERT_FILE"] = CA_FILE
os.environ["REQUESTS_CA_BUNDLE"] = CA_FILE

assert os.getenv("DATABRICKS_WAREHOUSE_ID"), (
    "DATABRICKS_WAREHOUSE_ID must be set in app.yaml."
)

cfg = Config()


# ── Helper: parse comma-separated env var ────────────────────
def _csv_env(key: str, default: str) -> list[str]:
    raw = os.getenv(key, default)
    return [c.strip() for c in raw.split(",") if c.strip()]


# ── Catalog / Schema / Tables ────────────────────────────────
CATALOG = os.getenv("CATALOG", "onesource_eu_dev_rni")
SCHEMA = os.getenv("SCHEMA", "ux_sn_global")
MAIN_TABLE = os.getenv("MAIN_TABLE", "ux_sn_conso_master_table_dbapp")
TABLE_FQN = os.getenv(
    "UC_TABLE_FQN", f"{CATALOG}.{SCHEMA}.{MAIN_TABLE}"
)
AUDIT_TABLE = os.getenv(
    "AUDIT_TABLE_FQN",
    f"{CATALOG}.{SCHEMA}.{MAIN_TABLE}_audit",
)
MAPPING_TABLE = os.getenv(
    "MAPPING_TABLE_FQN",
    f"{CATALOG}.{SCHEMA}.mapping_codes",
)
DROPDOWN_TABLE = os.getenv(
    "DROPDOWN_TABLE_FQN",
    f"{CATALOG}.{SCHEMA}.ux_sn_dropdown_options",
)

# ── Connection constants ─────────────────────────────────────
HOST = (cfg.host or "").replace("https://", "").replace("http://", "").rstrip("/")
HTTP_PATH = f"/sql/1.0/warehouses/{cfg.warehouse_id}"

# ── Page / UI settings ───────────────────────────────────────
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "100"))
APP_TITLE = os.getenv("APP_TITLE", "SN UX Table Editor")

EXCEL_TEMPLATE = os.getenv("BULK_TEMPLATE_PATH", "app/assets/Book.xlsx")
EXCEL_DROPDOWN_FILE = os.getenv("EXCEL_DROPDOWN_FILE", "app/assets/Book2.xlsx")

APP_LOGO_IMAGE = os.getenv(
    "APP_LOGO_IMAGE", "app/assets/DANONE_LOGO_HORIZONTAL.png"
)
APP_LOGO_ICON = os.getenv(
    "APP_LOGO_ICON_IMAGE", "app/assets/DANONE_LOGO_HORIZONTAL.png"
)
APP_LOGO_LINK = os.getenv(
    "APP_LOGO_LINK",
    "https://commons.wikimedia.org/wiki/File:DANONE_LOGO_HORIZONTAL.png",
)

USE_MOCK_DROPDOWNS = os.getenv("USE_MOCK_DROPDOWNS", "false").lower() == "true"
MOCK_API_URL = os.getenv("MOCK_API_URL", "http://localhost:9000")

# ── Column lists (configurable via app.yaml) ─────────────────
SELECT_COLUMNS = _csv_env("DISPLAY_COLUMNS",
    "row_id,sp_test_id,cl_test_id,match_type,brand_L0,brand_L1,"
    "prod_type,flavour_pack,test_country,test_year,zone,CBU,"
    "competitor1,competitor2,prod_catL1,test_area,prod_status,"
    "score_main,cl_pct,test_environment,test_status,"
    "cl_pyramid_level,cl_questionnaire,cl_substantiated,"
    "pack,prod_name_pack,test_danoneprod,ingestion_timestamp"
)

DML_COLUMNS = _csv_env("DML_COLUMNS",
    "sp_test_id,cl_test_id,match_type,brand_L0,brand_L1,"
    "prod_type,flavour_pack,test_country,test_year,zone,CBU,"
    "competitor1,competitor2,prod_catL1,retest_year,test_area,"
    "prod_status,score_main,cl_pct,test_environment,test_status,"
    "cl_pyramid_level,cl_questionnaire,cl_substantiated,"
    "pack,prod_name_pack,test_danoneprod,ingestion_timestamp"
)

DROPDOWN_COLS = _csv_env("DROPDOWN_COLS",
    "match_type,brand_L1,branded_flag,flavour_pack,prod_type,"
    "test_country,test_year,zone,prod_catL4,stage,CBU,brand_L0,"
    "prod_catL1,prod_catL2,prod_catL3"
)

FILTER_COLUMNS = _csv_env("FILTER_COLUMNS",
    "test_year,test_country,brand_L0"
)

SEQ_GROUP_COLS = _csv_env("SEQ_GROUP_COLS",
    "test_year,test_country,prod_catL1,brand_L0,"
    "competitor1,competitor2,pack,prod_name_pack,"
    "prod_status,test_danoneprod"
)

MANDATORY_UPDATE_COLS = _csv_env("MANDATORY_UPDATE_COLS",
    "test_year,test_country,brand_L0"
)

PINNED_COLUMNS = _csv_env("PINNED_COLUMNS", "sp_test_id,cl_test_id")
DISABLED_COLUMNS = _csv_env("DISABLED_COLUMNS",
    "sp_test_id,cl_test_id,ingestion_timestamp"
)

# Page 1 – Insights View profiling columns
INSIGHTS_MANDATORY_COLUMNS = _csv_env("INSIGHTS_MANDATORY_COLUMNS",
    "brand_L1,prod_catL1,branded_flag,flavour_pack,prod_type,prod_catL4,"
    "test_country,test_year,competitor1,competitor2,pack,prod_name_pack,"
    "prod_status,test_danoneprod"
)

# ── Schema dtype map ─────────────────────────────────────────
SCHEMA_DTYPE: dict[str, Any] = {
    "row_id":               pd.Int64Dtype(),
    "sp_test_id":           pd.StringDtype(),
    "cl_test_id":           pd.StringDtype(),
    "match_type":           pd.StringDtype(),
    "brand_L0":             pd.StringDtype(),
    "brand_L1":             pd.StringDtype(),
    "prod_type":            pd.StringDtype(),
    "flavour_pack":         pd.StringDtype(),
    "test_country":         pd.StringDtype(),
    "test_year":            pd.Int64Dtype(),
    "zone":                 pd.StringDtype(),
    "CBU":                  pd.StringDtype(),
    "competitor1":          pd.StringDtype(),
    "competitor2":          pd.StringDtype(),
    "prod_catL1":           pd.StringDtype(),
    "test_area":            pd.StringDtype(),
    "prod_status":          pd.StringDtype(),
    "score_main":           pd.StringDtype(),
    "cl_pct":               pd.StringDtype(),
    "test_environment":     pd.StringDtype(),
    "test_status":          pd.StringDtype(),
    "cl_pyramid_level":     pd.StringDtype(),
    "cl_questionnaire":     pd.StringDtype(),
    "cl_substantiated":     pd.StringDtype(),
    "pack":                 pd.StringDtype(),
    "prod_name_pack":       pd.StringDtype(),
    "test_danoneprod":      pd.StringDtype(),
    "ingestion_timestamp":  pd.DatetimeTZDtype(unit="us", tz="Etc/UTC"),
}

UPLOAD_DTYPE = {
    k: v for k, v in SCHEMA_DTYPE.items()
    if k not in ("row_id", "ingestion_timestamp")
}

# ── Admin page settings ──────────────────────────────────────
ADMIN_USERS = [
    u.strip().lower()
    for u in os.getenv("ADMIN_USERS", "").split(",") if u.strip()
]

# ── Editor page access control ───────────────────────────────
# Comma-separated emails. If set, ONLY these users can access page 2.
# If empty/unset, everyone can edit (backward compatible).
EDITOR_USERS = [
    u.strip().lower()
    for u in os.getenv("EDITOR_USERS", "").split(",") if u.strip()
]

try:
    ADMIN_COLUMN_ACCESS: dict[str, dict[str, list[str]]] = json.loads(
        os.getenv("ADMIN_COLUMN_ACCESS", "{}")
    )
except (json.JSONDecodeError, TypeError):
    ADMIN_COLUMN_ACCESS = {}

ADMIN_PAGE_SIZE = int(os.getenv("ADMIN_PAGE_SIZE", str(PAGE_SIZE)))

ADMIN_TABLES = {
    "Master Table": TABLE_FQN,
    "Mapping Codes": MAPPING_TABLE,
    "Dropdown Options": DROPDOWN_TABLE,
}

# ── Per-user editable columns (Editor page 2) ────────────────
# JSON env var: {"email": ["col1","col2"]}
# Listed columns are REMOVED from the disabled list for that user,
# allowing them to edit normally-locked columns (e.g. cl_test_id).
try:
    EDITOR_COLUMN_ACCESS: dict[str, list[str]] = json.loads(
        os.getenv("EDITOR_COLUMN_ACCESS", "{}")
    )
except (json.JSONDecodeError, TypeError):
    EDITOR_COLUMN_ACCESS = {}

# ── Per-group editable columns (Editor page 2) ───────────────
# JSON env var: { "group_name": ["*"] | ["col1","col2",...] }
#   ["*"]        = can edit ALL columns (standard DISABLED_COLUMNS apply)
#   ["col1",...]  = can ONLY edit those listed columns; all others disabled
try:
    EDITOR_GROUP_COLUMN_ACCESS: dict[str, list[str]] = json.loads(
        os.getenv("EDITOR_GROUP_COLUMN_ACCESS", "{}")
    )
except (json.JSONDecodeError, TypeError):
    EDITOR_GROUP_COLUMN_ACCESS = {}


def get_disabled_columns(user_email: str) -> list[str]:
    """Return disabled columns for the given user.

    Starts with DISABLED_COLUMNS, then removes any columns the user
    is explicitly allowed to edit via EDITOR_COLUMN_ACCESS.
    """
    base = list(DISABLED_COLUMNS)
    editable = EDITOR_COLUMN_ACCESS.get(user_email.lower(), [])
    return [c for c in base if c not in editable]


# ── Editor group access control (AAD / Databricks group) ─────
# If set, only members of this group can edit on page 2.
# Non-members see a read-only view.  If empty, everyone can edit.
_raw_groups = os.getenv("EDITOR_GROUP", "").strip()
EDITOR_GROUPS = [g.strip() for g in _raw_groups.split(",") if g.strip()]


@st.cache_data(ttl=300, show_spinner=False)
def _get_user_groups(user_email: str) -> set[str] | None:
    """Fetch the AAD / Databricks groups the user belongs to via SCIM.

    Returns a set of group names on success (may be empty if user has
    no groups or is not found).  Returns *None* when SCIM itself fails
    (exception), so the caller can choose to fail-open.
    Cached for 5 minutes.
    """
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()

        user_list = list(w.users.list(
            filter=f'userName eq "{user_email}"',
            attributes="groups",
        ))
        if not user_list:
            return set()

        return {g.display for g in (user_list[0].groups or [])}
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def is_user_in_editor_group(user_email: str) -> bool:
    """Check if user has edit access via EDITOR_USERS or EDITOR_GROUPS.

    Priority order:
      1. EDITOR_USERS – direct email list (always checked first)
      2. EDITOR_GROUPS – AAD/Databricks group membership via SCIM
      3. If neither configured → everyone can edit
    """
    # 1) Direct user list – always takes priority
    if EDITOR_USERS and user_email.lower() in EDITOR_USERS:
        return True

    # 2) Group-based check via SCIM
    if EDITOR_GROUPS:
        user_groups = _get_user_groups(user_email)
        if user_groups is None:
            # SCIM itself failed (exception) — fail open
            return True
        return bool(user_groups & set(EDITOR_GROUPS))

    # 3) Nothing configured → everyone can edit
    if not EDITOR_USERS and not EDITOR_GROUPS:
        return True

    return False


def get_disabled_columns_by_group(
    user_email: str,
    all_columns: list[str],
) -> list[str]:
    """Return disabled columns based on the user's AAD group membership.

    Logic:
      1. Fetch user's groups via SCIM (_get_user_groups).
      2. For each group present in EDITOR_GROUP_COLUMN_ACCESS:
         - ["*"] → full access (only base DISABLED_COLUMNS apply)
         - ["col1",...] → only those columns are editable
      3. Merge allowed columns from ALL matching groups (union).
      4. If ANY matching group has ["*"], grant full access.
      5. If no matching group found, fall back to get_disabled_columns().
    """
    if not EDITOR_GROUP_COLUMN_ACCESS:
        # No group-level column access configured → use default
        return get_disabled_columns(user_email)

    user_groups = _get_user_groups(user_email)
    if user_groups is None or not user_groups:
        # Could not determine groups → fall back to default
        return get_disabled_columns(user_email)

    # Collect allowed columns from all matching groups
    matched_any = False
    has_wildcard = False
    allowed_cols: set[str] = set()

    for group_name, cols in EDITOR_GROUP_COLUMN_ACCESS.items():
        if group_name in user_groups:
            matched_any = True
            if cols == ["*"]:
                has_wildcard = True
                break
            allowed_cols.update(cols)

    if not matched_any:
        # User is not in any configured group → use default
        return get_disabled_columns(user_email)

    if has_wildcard:
        # At least one group grants full access → standard disabled only
        return get_disabled_columns(user_email)

    # Restricted access: disable everything NOT in the allowed set
    # Always keep base DISABLED_COLUMNS disabled too
    base_disabled = set(DISABLED_COLUMNS)
    disabled = [
        c for c in all_columns
        if c not in allowed_cols or c in base_disabled
    ]
    return disabled


# ═════════════════════════════════════════════════════════════
#  SHARED CSS (injected by every page via st.markdown)
# ═════════════════════════════════════════════════════════════

SHARED_CSS = """
<style>
  [data-testid="stAppHeader"] {
    height: 0.5rem !important; min-height: 1rem !important;
    padding: 0.2rem 1rem !important; box-shadow: none !important;
    border-bottom: 1px solid #ddd !important;
  }
  [data-testid="stAppHeader"] h1,
  [data-testid="stAppHeader"] h2,
  [data-testid="stAppHeader"] h3 {
    font-size: 16px !important; margin: 0 !important;
    padding: 0 !important; line-height: 1 !important;
  }
  [data-testid="stAppHeader"] button {
    transform: scale(0.85); padding: 2px 4px !important; font-size: 12px !important;
  }
  [data-testid="stSidebar"] {
    width: 12rem !important; min-width: 12rem !important; max-width: 12rem !important;
  }
  [data-testid="stSidebar"] > div:first-child { padding: 0.5rem 0.8rem !important; }
  [data-testid="stSidebar"] * { font-size: 13px !important; }
  .block-container {
    padding-top: 3rem !important;
    padding-left: 2rem !important;
    padding-right: 2rem !important;
  }
  html, body, [class*="css"] { font-size: 14px !important; }
  h1 { font-size: 28px !important; font-weight: 700 !important;
       margin-top: 1rem !important; margin-bottom: 0.5rem !important; }
  h2 { font-size: 26px !important; font-weight: 650 !important; }
  h3 { font-size: 18px !important; font-weight: 600 !important; }
  h4 { font-size: 16px !important; }
  .stTabs [data-baseweb="tab"] { font-size: 14px !important; }
  .streamlit-expanderHeader { font-size: 12px !important; }
  .stButton > button { font-size: 13px !important; padding: 4px 10px !important; }
  .dataframe td, .dataframe th { font-size: 13px !important; }
  .stDataFrame tbody tr td { padding-top: 4px !important; padding-bottom: 4px !important; }
  /* Hide column header context menu (Format / Autosize / Unpin / Hide) */
  [data-testid="stDataFrame"] .gdg-header-menu,
  [data-testid="stDataFrame"] [data-testid="column-header-menu"],
  .gdg-header-menu { display: none !important; }

  /* ── Responsive: dataframes & editors fill available viewport ── */
  [data-testid="stDataFrame"] > div { width: 100% !important; }
  .block-container { max-width: 100% !important; }

  /* Responsive sidebar for narrow screens */
  @media (max-width: 768px) {
    [data-testid="stSidebar"] {
      width: 100% !important; min-width: 100% !important; max-width: 100% !important;
    }
    .block-container {
      padding-left: 0.5rem !important; padding-right: 0.5rem !important;
    }
  }
</style>
"""


# ═════════════════════════════════════════════════════════════
#  SHARED UTILITY FUNCTIONS
# ═════════════════════════════════════════════════════════════

def to_bold(text: str) -> str:
    """Convert text to Unicode Mathematical Bold Sans-Serif
    (st.dataframe renders headers on a canvas where CSS font-weight
    has no effect, so we use Unicode bold chars instead)."""
    result = []
    for ch in str(text):
        if 'A' <= ch <= 'Z':
            result.append(chr(0x1D5D4 + ord(ch) - ord('A')))
        elif 'a' <= ch <= 'z':
            result.append(chr(0x1D5EE + ord(ch) - ord('a')))
        elif '0' <= ch <= '9':
            result.append(chr(0x1D7EC + ord(ch) - ord('0')))
        else:
            result.append(ch)
    return ''.join(result)


def is_na(v) -> bool:
    """Check if a value is any flavour of missing (None, pd.NA, NaN, empty str)."""
    if v is None or v is pd.NA:
        return True
    try:
        if pd.isna(v):
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def validate_mandatory_cols(df: pd.DataFrame, cols: list[str]) -> list[str]:
    """Return list of error messages for rows missing mandatory column values."""
    errors: list[str] = []
    for col in cols:
        if col not in df.columns:
            continue
        for idx, val in df[col].items():
            if is_na(val):
                row_id = df.at[idx, "row_id"] if "row_id" in df.columns else idx
                errors.append(f'Row {row_id}: mandatory column "{col}" is empty')
    return errors


# ═════════════════════════════════════════════════════════════
#  CONNECTION HELPERS
# ═════════════════════════════════════════════════════════════

def get_user_token() -> str | None:
    """Extract OBO token from Databricks Apps headers."""
    return st.context.headers.get("X-Forwarded-Access-Token")


def sp_connection():
    """Service-principal connection (local dev / no user token)."""
    return sql.connect(
        server_hostname=cfg.host,
        http_path=HTTP_PATH,
        credentials_provider=lambda: cfg.authenticate,
    )


@st.cache_resource(show_spinner=False)
def get_connection(user_token: str):
    """Cached SQL Warehouse connection using user OBO token."""
    return sql.connect(
        server_hostname=HOST,
        http_path=HTTP_PATH,
        access_token=user_token,
        _tls_trusted_ca_file=CA_FILE,
        _tls_no_verify=False,
    )


def run_query(query: str, user_token: str | None = None) -> pd.DataFrame:
    """Execute *query* and return a DataFrame, using user token when available."""
    if user_token:
        with sql.connect(
            server_hostname=HOST, http_path=HTTP_PATH,
            access_token=user_token,
            _tls_trusted_ca_file=CA_FILE, _tls_no_verify=False,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                return cur.fetchall_arrow().to_pandas()
    else:
        with sp_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                return cur.fetchall_arrow().to_pandas()


def run_statement(statement: str, params: list | None = None,
                  user_token: str | None = None):
    """Execute a DML statement (INSERT/UPDATE/DELETE) and commit."""
    if user_token:
        with sql.connect(
            server_hostname=HOST, http_path=HTTP_PATH,
            access_token=user_token,
            _tls_trusted_ca_file=CA_FILE, _tls_no_verify=False,
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(statement, params)
            conn.commit()
    else:
        with sp_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(statement, params)
            conn.commit()


def get_user_identity() -> str:
    """Robust identity resolver for Databricks Apps / local dev."""
    try:
        headers = st.context.headers or {}
        for key in (
            "X-Forwarded-Email", "X-Forwarded-User",
            "X-MS-CLIENT-PRINCIPAL-NAME", "X-MS-CLIENT-PRINCIPAL-ID",
        ):
            val = headers.get(key)
            if val:
                return val
    except Exception:
        pass
    try:
        if getattr(cfg, "client_id", None):
            return f"app:{cfg.client_id}"
    except Exception:
        pass
    return os.getenv("USERNAME") or os.getenv("USER") or "unknown_user"


def ensure_session_id(key: str = "session_id"):
    """Ensure a session ID exists under *key* in session state."""
    if key not in st.session_state:
        st.session_state[key] = str(uuid.uuid4())


# ═════════════════════════════════════════════════════════════
#  MAPPING-CODE LOOKUP (DRY – one generic function)
# ═════════════════════════════════════════════════════════════

def lookup_mapping_code(value_col: str, code_col: str, value: str) -> str | None:
    """Return *code_col* from MAPPING_TABLE where lower(*value_col*) = lower(value)."""
    if not value or not str(value).strip():
        return None
    try:
        with sp_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {code_col} FROM {MAPPING_TABLE} "
                    f"WHERE lower({value_col}) = lower(?)",
                    (str(value).strip(),),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as exc:
        st.error(f"Mapping lookup failed ({value_col}={value}): {exc}")
        return None


def get_prod_catL1_code(v: str) -> str | None:
    return lookup_mapping_code("prod_catL1", "prod_catL1_code", v)


def get_brand_L0_code(v: str) -> str | None:
    return lookup_mapping_code("brand_L0", "brand_L0_code", v)


# ═════════════════════════════════════════════════════════════
#  SEQ & TEST-ID GENERATION
# ═════════════════════════════════════════════════════════════

def norm_value(v, col_name=None) -> str:
    """Normalize a value for grouping (None/NaN -> empty string)."""
    if v is None or v is pd.NA:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    if col_name == "test_year":
        try:
            return str(int(float(v)))
        except Exception:
            return ""
    return str(v).strip()


def safe_tuple_eq(a: tuple, b: tuple) -> bool:
    """Element-wise comparison treating any NA as empty string."""
    if len(a) != len(b):
        return False
    for x, y in zip(a, b):
        sx = "" if is_na(x) else str(x).strip()
        sy = "" if is_na(y) else str(y).strip()
        if sx != sy:
            return False
    return True


def compute_seq(df_row) -> int:
    """Compute the SEQ number for a given row."""
    needed_cols = list(dict.fromkeys(["sp_test_id"] + SEQ_GROUP_COLS))
    cols_sql = ", ".join(needed_cols)
    order_sql = ", ".join(SEQ_GROUP_COLS)
    query = f"SELECT {cols_sql} FROM {TABLE_FQN} ORDER BY {order_sql}"

    try:
        df_all = run_query(query, get_user_token())
    except Exception:
        return 1
    if df_all.empty:
        return 1

    # Extract ALL sequences globally
    global_seq_set: set[int] = set()
    for sid in df_all["sp_test_id"]:
        if isinstance(sid, str):
            parts = sid.split("_")
            if len(parts) > 5:
                try:
                    global_seq_set.add(int(parts[5]))
                except ValueError:
                    pass

    # Build special key for current row
    special_key = tuple(norm_value(df_row.get(c), c) for c in SEQ_GROUP_COLS)

    def _row_key(r):
        return tuple(norm_value(r.get(c), c) for c in SEQ_GROUP_COLS)

    # Match rows (safe comparison)
    special_mask = df_all.apply(
        lambda r: safe_tuple_eq(_row_key(r), special_key), axis=1,
    )
    special_df = df_all[special_mask]
    if not special_df.empty:
        sid = special_df.iloc[0]["sp_test_id"]
        if isinstance(sid, str):
            parts = sid.split("_")
            if len(parts) > 5:
                try:
                    return int(parts[5])
                except Exception:
                    pass

    if not global_seq_set:
        return 1

    # Build group key
    group_key = tuple(norm_value(df_row.get(c), c) for c in SEQ_GROUP_COLS)
    mask = df_all.apply(
        lambda r: safe_tuple_eq(_row_key(r), group_key), axis=1,
    )
    group_df = df_all[mask]
    if group_df.empty:
        return max(global_seq_set) + 1

    # Extract seq within group
    group_seq_set: set[int] = set()
    for sid in group_df["sp_test_id"]:
        if isinstance(sid, str):
            parts = sid.split("_")
            if len(parts) > 5:
                try:
                    group_seq_set.add(int(parts[5]))
                except ValueError:
                    pass
    if not group_seq_set:
        return max(global_seq_set) + 1

    # Check if current row already has sp_test_id
    current_sid = df_row.get("sp_test_id")
    if isinstance(current_sid, str) and current_sid.strip():
        parts = current_sid.split("_")
        if len(parts) > 5:
            try:
                current_seq = int(parts[5])
                if current_seq in group_seq_set:
                    return current_seq
            except ValueError:
                pass

    if len(group_seq_set) == 1:
        return next(iter(group_seq_set))
    return max(group_seq_set)


def generate_test_id(
    match_type, test_year, test_country, prod_cat, brand_L0, retest_year, seq,
) -> str:
    """Build SP_<year>_<country>_<catCode>_<brandCode>_<seq>_<Y/N> test id."""

    def _fna(v) -> str:
        if v is None or pd.isna(v) or str(v).strip() == "":
            return "NA"
        return str(v).strip()

    try:
        year_clean = str(int(float(test_year)))
    except (ValueError, TypeError):
        year_clean = _fna(test_year)

    cat_code = get_prod_catL1_code(prod_cat) or "NA"
    brand_code = get_brand_L0_code(brand_L0) or "NA"

    yn = "N" if (retest_year is None or pd.isna(retest_year)
                 or str(retest_year).strip() == "") else "Y"

    return f"SP_{year_clean}_{_fna(test_country)}_{cat_code}_{brand_code}_{seq}_{yn}"


# ═════════════════════════════════════════════════════════════
#  AUDIT HELPERS
# ═════════════════════════════════════════════════════════════

def to_audit_str(v) -> str | None:
    """Normalize a value to string for audit storage."""
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if v is pd.NA:
        return None
    if isinstance(v, (pd.Timestamp, datetime)):
        try:
            return v.isoformat()
        except Exception:
            return str(v)
    return str(v)


def create_audit_table(conn):
    """Create audit table if it doesn't exist."""
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {AUDIT_TABLE} (
      audit_id   BIGINT NOT NULL GENERATED ALWAYS AS IDENTITY (START WITH 1),
      event_ts   TIMESTAMP,
      event_type STRING,
      user_name  STRING,
      session_id STRING,
      page_no    INT,
      table_fqn  STRING,
      row_id     BIGINT,
      col_name   STRING,
      old_value  STRING,
      new_value  STRING,
      notes      STRING
    ) USING DELTA
    """
    with conn.cursor() as c:
        c.execute(ddl)
    conn.commit()


def write_audit_events(conn, events: list[dict]):
    """Bulk-insert audit events."""
    if not events:
        return
    cols = [
        "event_ts", "event_type", "user_name", "session_id", "page_no",
        "table_fqn", "row_id", "col_name", "old_value", "new_value", "notes",
    ]
    placeholders = ", ".join(
        ["(" + ",".join(["?"] * len(cols)) + ")"] * len(events)
    )
    insert_sql = (
        f"INSERT INTO {AUDIT_TABLE} ({', '.join(cols)}) VALUES {placeholders}"
    )
    params: list = []
    for e in events:
        params.extend(e.get(c) for c in cols)
    with conn.cursor() as c:
        c.execute(insert_sql, params)
    conn.commit()


def build_audit_events(
    *,
    original_df: pd.DataFrame,
    edited_df: pd.DataFrame,
    added_rows_df: pd.DataFrame | None,
    deleted_rows_df: pd.DataFrame | None,
    diff_df: pd.DataFrame | None,
    page_no: int,
    table_fqn: str = TABLE_FQN,
    session_key: str = "session_id",
    source: str = "UI",
    inserted_row_ids: list[int] | None = None,
    deleted_row_ids: list[int] | None = None,
) -> list[dict]:
    """Create audit events for INSERT / DELETE / UPDATE + a SAVE summary.

    Parameters
    ----------
    source : str
        Label used in audit notes (e.g. "UI" or "Admin UI").
    session_key : str
        Session-state key holding the session ID.
    """
    ensure_session_id(session_key)
    user = get_user_identity()
    ts = datetime.now(timezone.utc).replace(tzinfo=None)
    events: list[dict] = []

    def _evt(**kw) -> dict:
        base = {
            "event_ts": ts, "event_type": "", "user_name": user,
            "session_id": st.session_state[session_key],
            "page_no": int(page_no), "table_fqn": table_fqn,
            "row_id": None, "col_name": None,
            "old_value": None, "new_value": None, "notes": None,
        }
        base.update(kw)
        return base

    # INSERTs
    if added_rows_df is not None and len(added_rows_df):
        rid_list = inserted_row_ids or [None] * len(added_rows_df)
        for idx, (_, row) in enumerate(added_rows_df.reset_index(drop=False).iterrows()):
            events.append(_evt(
                event_type="INSERT_ROW",
                row_id=rid_list[idx] if idx < len(rid_list) else None,
                new_value=json.dumps(
                    {k: to_audit_str(v) for k, v in row.to_dict().items()}
                ),
                notes=f"Row inserted from {source}",
            ))

    # DELETEs
    if deleted_rows_df is not None and len(deleted_rows_df):
        rid_list = deleted_row_ids or list(deleted_rows_df.index)
        for rid in rid_list:
            old_row = None
            try:
                if rid in original_df.index:
                    old_row = json.dumps(
                        {k: to_audit_str(v) for k, v in original_df.loc[rid].to_dict().items()}
                    )
            except Exception:
                pass
            events.append(_evt(
                event_type="DELETE_ROW",
                row_id=int(rid) if rid is not None else None,
                old_value=old_row, notes=f"Row deleted from {source}",
            ))

    # UPDATEs
    if diff_df is not None and len(diff_df):
        for (rid, col_name), vals in diff_df.iterrows():
            events.append(_evt(
                event_type="UPDATE_CELL",
                row_id=int(rid) if rid is not None else None,
                col_name=str(col_name),
                old_value=to_audit_str(vals.get("self")),
                new_value=to_audit_str(vals.get("other")),
                notes=f"Cell updated from {source}",
            ))

    # Summary
    events.append(_evt(
        event_type="SAVE",
        notes=(
            f"{source} save. Added={0 if added_rows_df is None else len(added_rows_df)}, "
            f"Deleted={0 if deleted_rows_df is None else len(deleted_rows_df)}, "
            f"UpdatedCells={0 if diff_df is None else len(diff_df)}"
        ),
    ))
    return events


def log_edit_start_once(conn, original_df, edited_df, page_no: int,
                        session_key: str = "session_id",
                        logged_key: str = "edit_start_logged"):
    """Log EDIT_START exactly once per session on first grid change."""
    ensure_session_id(session_key)
    if st.session_state.get(logged_key):
        return
    try:
        if not original_df.equals(edited_df):
            write_audit_events(conn, [{
                "event_ts": datetime.now(timezone.utc).replace(tzinfo=None),
                "event_type": "EDIT_START",
                "user_name": get_user_identity(),
                "session_id": st.session_state[session_key],
                "page_no": int(page_no), "table_fqn": TABLE_FQN,
                "row_id": None, "col_name": None,
                "old_value": None, "new_value": None,
                "notes": "User started editing grid",
            }])
            st.session_state[logged_key] = True
    except Exception:
        pass  # never block UI for audit


# ═════════════════════════════════════════════════════════════
#  DROPDOWN HELPERS
# ═════════════════════════════════════════════════════════════

@st.cache_data(show_spinner=False)
def load_dropdown_cache() -> dict[str, list[str]]:
    """Load dropdown option lists from the dedicated dropdown reference table."""
    result: dict[str, list[str]] = {col: [] for col in DROPDOWN_COLS}
    try:
        q = (
            f"SELECT column_name, option_value "
            f"FROM {DROPDOWN_TABLE} "
            f"WHERE option_value IS NOT NULL "
            f"ORDER BY column_name, option_value"
        )
        df = run_query(q, get_user_token())
        for col_name, group in df.groupby("column_name"):
            if col_name in result:
                result[col_name] = group["option_value"].dropna().astype(str).tolist()
    except Exception:
        pass  # fall back to empty lists; dropdown_options() will use db_values
    return result


_dropdown_cache = load_dropdown_cache()


def dropdown_options(col: str, db_values: list) -> list[str]:
    """Return table distinct values + 'TBC' for dropdown columns."""
    opts = _dropdown_cache.get(col, [])
    if not opts:
        opts = [str(x) for x in db_values if x is not None]
    if "TBC" not in opts:
        opts.append("TBC")
    return sorted(set(opts))


# ═════════════════════════════════════════════════════════════
#  REUSABLE COLUMN CONFIG BUILDER
# ═════════════════════════════════════════════════════════════

def build_column_config(
    source_df: pd.DataFrame,
    is_master: bool = True,
    dropdown_required: bool = False,
) -> dict:
    """Build st.column_config dict for the data editor.

    Parameters
    ----------
    is_master : bool
        True for the master table (adds pinned row_id / timestamp
        and SelectboxColumn for dropdown cols).
    dropdown_required : bool
        True makes dropdown SelectboxColumn required=True.
    """
    def _dd(col):
        db_vals = (
            source_df[col].dropna().unique().tolist()
            if col in source_df.columns else []
        )
        return dropdown_options(col, db_vals)

    config: dict[str, Any] = {}

    if is_master:
        config["row_id"] = st.column_config.NumberColumn(
            label=to_bold("row_id"), disabled=True, pinned=True,
        )
        config["ingestion_timestamp"] = st.column_config.DatetimeColumn(
            label=to_bold("ingestion_timestamp"),
            format="localized", disabled=True, pinned=True,
        )
        for col in DROPDOWN_COLS:
            if col in source_df.columns:
                config[col] = st.column_config.SelectboxColumn(
                    label=to_bold(col), options=_dd(col),
                    required=dropdown_required,
                )

    # Bold labels for all remaining columns
    for col in source_df.columns:
        if col not in config:
            config[col] = st.column_config.Column(label=to_bold(col))

    return config


# ═════════════════════════════════════════════════════════════
#  ADMIN-SPECIFIC HELPERS
# ═════════════════════════════════════════════════════════════

def admin_get_visible_columns(
    user_email: str, table_label: str, all_columns: list[str],
) -> list[str]:
    """Return visible columns for admin user based on ADMIN_COLUMN_ACCESS."""
    email_lower = user_email.lower()
    user_cfg = ADMIN_COLUMN_ACCESS.get(email_lower)
    if user_cfg is None:
        return all_columns
    table_cols = user_cfg.get(table_label)
    if table_cols is None or table_cols == ["*"]:
        return all_columns
    return [c for c in all_columns if c in table_cols]


def build_composite_where(
    row: pd.Series, columns: list[str],
) -> tuple[str, list]:
    """Build composite WHERE clause for tables without unique PK."""
    parts: list[str] = []
    params: list = []
    for col in columns:
        val = row[col]
        if pd.isna(val):
            parts.append(f"{col} IS NULL")
        else:
            parts.append(f"{col} = ?")
            params.append(
                val.to_pydatetime() if isinstance(val, pd.Timestamp) else val
            )
    return " AND ".join(parts), params


def build_composite_set(
    row: pd.Series, columns: list[str],
) -> tuple[str, list]:
    """Build SET clause for UPDATE on non-PK tables."""
    parts: list[str] = []
    params: list = []
    for col in columns:
        val = row[col]
        if pd.isna(val):
            parts.append(f"{col} = NULL")
        else:
            parts.append(f"{col} = ?")
            params.append(
                val.to_pydatetime() if isinstance(val, pd.Timestamp) else val
            )
    return ", ".join(parts), params


# ═════════════════════════════════════════════════════════════
#  BULK DML OPERATIONS
# ═════════════════════════════════════════════════════════════

def bulk_insert(
    connection, df_new_rows: pd.DataFrame, table_fqn: str = TABLE_FQN,
):
    """Insert rows -> return (success, errors, error_list, row_id_list)."""
    batch_ts = datetime.now()
    df_new_rows = df_new_rows.copy()
    df_new_rows["ingestion_timestamp"] = batch_ts
    df_new_rows = df_new_rows.where(pd.notna(df_new_rows), None).replace({pd.NA: None})

    num_rows = len(df_new_rows)
    num_cols = len(DML_COLUMNS)
    placeholders = ", ".join(
        [f"({', '.join(['?'] * num_cols)})" for _ in range(num_rows)]
    )
    insert_sql = (
        f"INSERT INTO {table_fqn} ({', '.join(DML_COLUMNS)}) VALUES {placeholders}"
    )

    all_params: list = []
    for _, row in df_new_rows.iterrows():
        for col in DML_COLUMNS:
            val = row.get(col)
            all_params.append(
                val.to_pydatetime() if isinstance(val, pd.Timestamp) else val
            )

    try:
        with connection.cursor() as cur:
            # Get current max row_id before insert
            cur.execute(f"SELECT COALESCE(MAX(row_id), 0) AS max_id FROM {table_fqn}")
            _max_id = int(cur.fetchall_arrow().to_pandas()["max_id"].iloc[0])

            cur.execute(insert_sql, all_params)
            time.sleep(0.1)

            # Assign sequential row_ids to newly inserted rows (NULL row_id)
            cur.execute(
                f"SELECT ingestion_timestamp FROM {table_fqn} "
                f"WHERE row_id IS NULL AND ingestion_timestamp = :ts LIMIT 1",
                {"ts": batch_ts},
            )
            _null_check = cur.fetchall()
            if _null_check:
                # Rows were inserted without row_id — assign them
                cur.execute(f"""
                    MERGE INTO {table_fqn} AS target
                    USING (
                        SELECT *, ({_max_id} + ROW_NUMBER() OVER (ORDER BY ingestion_timestamp)) AS _new_rid
                        FROM {table_fqn}
                        WHERE row_id IS NULL AND ingestion_timestamp = :ts
                    ) AS source
                    ON target.row_id IS NULL
                       AND target.ingestion_timestamp <=> source.ingestion_timestamp
                       AND target.match_type <=> source.match_type
                       AND target.brand_L0 <=> source.brand_L0
                       AND target.test_country <=> source.test_country
                       AND target.test_year <=> source.test_year
                       AND target.flavour_pack <=> source.flavour_pack
                    WHEN MATCHED THEN UPDATE SET row_id = source._new_rid
                """, {"ts": batch_ts})
                time.sleep(0.1)

            cur.execute(
                f"SELECT row_id FROM {table_fqn} "
                f"WHERE ingestion_timestamp = :ts ORDER BY row_id",
                {"ts": batch_ts},
            )
            row_ids = cur.fetchall_arrow().to_pandas()["row_id"].tolist()

            # Auto-generate sp_test_id for rows that don't have one
            for idx, rid in enumerate(row_ids):
                row = df_new_rows.iloc[idx]
                existing_sid = row.get("sp_test_id")
                has_sid = (
                    existing_sid is not None
                    and existing_sid is not pd.NA
                    and not (isinstance(existing_sid, float) and pd.isna(existing_sid))
                    and str(existing_sid).strip() != ""
                )
                if has_sid:
                    test_id = str(existing_sid).strip()
                else:
                    seq = compute_seq(row)
                    test_id = generate_test_id(
                        row.get("match_type"), row.get("test_year"),
                        row.get("test_country"), row.get("prod_catL1"),
                        row.get("brand_L0"), row.get("retest_year"), seq,
                    )
                with connection.cursor() as cur2:
                    cur2.execute(
                        f"UPDATE {table_fqn} SET sp_test_id = ? WHERE row_id = ?",
                        (test_id, rid),
                    )
        connection.commit()
        return (num_rows, 0, [], row_ids)
    except Exception as e:
        return (0, num_rows, [str(e)], [])


def bulk_delete(
    connection, df_del_rows: pd.DataFrame, table_fqn: str = TABLE_FQN,
):
    """Delete rows by row_id -> return (success, errors, error_list, deleted_ids)."""
    row_ids = df_del_rows.index.tolist()
    num_rows = len(row_ids)
    ph = ", ".join(["?"] * num_rows)

    try:
        with connection.cursor() as cur:
            cur.execute(
                f"DELETE FROM {table_fqn} WHERE row_id IN ({ph})", row_ids,
            )
            time.sleep(0.1)
            cur.execute(
                f"SELECT row_id FROM {table_fqn} WHERE row_id IN ({ph})", row_ids,
            )
            remaining = [r["row_id"] for r in cur.fetchall()]

        if remaining:
            ok = [r for r in row_ids if r not in remaining]
            return (len(ok), len(remaining),
                    [f"Failed to delete: {remaining}"], ok)
        return (num_rows, 0, [], row_ids)
    except Exception as e:
        return (0, num_rows, [str(e)], [])


def bulk_update(
    connection, update_rows_df: pd.DataFrame, diff,
    table_fqn: str = TABLE_FQN, verify: bool = True,
):
    """MERGE-based bulk update -> return (success, errors, error_list, verified).

    Parameters
    ----------
    verify : bool
        When True (default), runs post-merge verification checks.
        Set False for simplified admin-table updates.
    """
    if update_rows_df.empty:
        return (0, 0, ["No rows provided"], [])
    if diff.empty:
        return (0, 0, ["No changes detected"], [])

    batch_ts = datetime.now()
    update_df = update_rows_df.reset_index()

    # Validate mandatory columns
    val_errors = validate_mandatory_cols(update_df, MANDATORY_UPDATE_COLS)
    if val_errors:
        return (0, len(val_errors), val_errors, [])

    # Regenerate sp_test_id for every updated row
    for i in update_df.index:
        ry = update_df.at[i, "retest_year"] if "retest_year" in update_df.columns else None
        seq = compute_seq(update_df.iloc[i])
        update_df.at[i, "sp_test_id"] = generate_test_id(
            update_df.at[i, "match_type"], update_df.at[i, "test_year"],
            update_df.at[i, "test_country"], update_df.at[i, "prod_catL1"],
            update_df.at[i, "brand_L0"], ry, seq,
        )

    if "row_id" not in update_df.columns:
        return (0, 0, ["update_rows_df must have row_id as index"], [])

    update_df["ingestion_timestamp"] = batch_ts
    update_df = update_df.where(pd.notna(update_df), None).replace({pd.NA: None})

    all_columns = [c for c in update_df.columns if c != "row_id"]
    num_rows = len(update_df)

    # Build MERGE
    value_rows, all_params = [], []
    for _, row in update_df.iterrows():
        for col in ["row_id"] + all_columns:
            val = row[col]
            all_params.append(
                val.to_pydatetime() if isinstance(val, pd.Timestamp) else val
            )
        value_rows.append(f"({', '.join(['?'] * (1 + len(all_columns)))})")

    src_cols = ", ".join(["row_id"] + all_columns)
    set_clause = ", ".join(f"target.{c} = source.{c}" for c in all_columns)

    merge_sql = f"""
    MERGE INTO {table_fqn} AS target
    USING (
        SELECT {src_cols}
        FROM VALUES {', '.join(value_rows)}
        AS t({src_cols})
    ) AS source
    ON target.row_id = source.row_id
    WHEN MATCHED THEN UPDATE SET {set_clause}
    """

    try:
        with connection.cursor() as cur:
            cur.execute(merge_sql, all_params)
            connection.commit()

            if not verify:
                return (num_rows, 0, [], [])

            time.sleep(0.1)

            # Verification checks
            checks = [
                {"row_id": rid, "col_name": cn, "expected": vals["other"]}
                for (rid, cn), vals in diff.iterrows()
            ]
            updated_row_ids = update_df["row_id"].tolist()

            verified, mismatches = [], []
            for chk in checks:
                cur.execute(
                    f"SELECT {chk['col_name']} FROM {table_fqn} WHERE row_id = ?",
                    [chk["row_id"]],
                )
                result = cur.fetchall()
                if not result:
                    mismatches.append(f"row_id {chk['row_id']} not found")
                    continue
                actual = result[0][chk["col_name"]]
                ok = (
                    (chk["expected"] is None and actual is None)
                    or str(chk["expected"]) == str(actual)
                )
                verified.append({
                    "row_id": chk["row_id"], "col_name": chk["col_name"],
                    "expected": chk["expected"], "actual": actual, "verified": ok,
                })
                if not ok:
                    mismatches.append(
                        f"Mismatch row_id={chk['row_id']}, col={chk['col_name']}: "
                        f"expected '{chk['expected']}', got '{actual}'"
                    )

            # Verify ingestion_timestamp
            ph = ", ".join(["?"] * len(updated_row_ids))
            cur.execute(
                f"SELECT row_id, ingestion_timestamp FROM {table_fqn} "
                f"WHERE row_id IN ({ph})",
                updated_row_ids,
            )
            for row in cur.fetchall():
                rid = row["row_id"]
                actual_ts = row["ingestion_timestamp"]
                ts_ok = False
                if isinstance(actual_ts, datetime):
                    a = actual_ts.replace(tzinfo=None) if actual_ts.tzinfo else actual_ts
                    b = batch_ts.replace(tzinfo=None) if batch_ts.tzinfo else batch_ts
                    ts_ok = abs((a - b).total_seconds()) < 5
                verified.append({
                    "row_id": rid, "col_name": "ingestion_timestamp",
                    "expected": batch_ts, "actual": actual_ts, "verified": ts_ok,
                })
                if not ts_ok:
                    mismatches.append(f"Timestamp not updated for row_id={rid}")

            if mismatches:
                ok_count = sum(1 for v in verified if v["verified"])
                return (ok_count, len(mismatches), mismatches, verified)
            return (len(verified), 0, [], verified)
    except Exception as e:
        return (0, num_rows, [str(e)], [])
