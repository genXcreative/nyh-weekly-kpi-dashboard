"""
NYH Weekly Ecommerce KPI Report — standalone script (GitHub Actions version)

Ported from nyh_weekly_kpi_report.ipynb (Cells 4-9). Pulls fresh Google Ads spend +
Shopify sales data, computes True ROAS / ROI + financial-plan attainment, and renders
the self-contained HTML dashboard to index.html at the repo root (published via
GitHub Pages) plus a dated JSON snapshot under data/ (for auditing / history).

Credentials come from environment variables (GitHub Actions Secrets), not a .env file
or google-ads.yaml — there is no Google Drive step in this version; that was Colab-only
plumbing to get credentials into an interactive session.

Required environment variables:
    SHOPIFY_STORE_DOMAIN           e.g. new-york-hardware-online.myshopify.com
    SHOPIFY_CLIENT_ID
    SHOPIFY_CLIENT_SECRET
    GOOGLE_ADS_DEVELOPER_TOKEN
    GOOGLE_ADS_CLIENT_ID
    GOOGLE_ADS_CLIENT_SECRET
    GOOGLE_ADS_REFRESH_TOKEN
    GOOGLE_ADS_LOGIN_CUSTOMER_ID   the MCC / manager account id (digits only, no dashes)
    GOOGLE_ADS_CUSTOMER_ID         the target account id being reported on

Optional environment variables:
    REPORT_AS_OF_DATE               ISO date (YYYY-MM-DD). Runs the whole report as if
                                     this were "today" — every window (prior 7 days, MTD,
                                     YTD, trend) shifts to be relative to this date instead
                                     of the real current date. Leave unset for normal runs.
    GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON   full JSON key for a Google Cloud service account
                                          with Sheets API access (optional — skipped if unset)
    GOOGLE_SHEET_ID                      target spreadsheet ID (optional — skipped if unset)
    EMAIL_SMTP_HOST                  defaults to smtp.gmail.com (optional group — skipped
    EMAIL_SMTP_PORT                  defaults to 587                if not all of
    EMAIL_FROM_ADDRESS               sending account's address       FROM_ADDRESS,
    EMAIL_APP_PASSWORD               Gmail/Workspace App Password    APP_PASSWORD, and
    EMAIL_RECIPIENTS                 comma-separated recipient list  RECIPIENTS are set)
    REPORT_LIVE_URL                  optional — if set, included as a link in the email
"""
import calendar
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from shopifyql import ShopifyQLClient

SCRIPT_DIR = Path(__file__).resolve().parent

# ─────────────────────────────────────────────────────────────────────────────
# Cell 4 equivalent — date windows, anchored to Pacific time
#
# REPORT_AS_OF_DATE lets a manual run generate the report as of any past date —
# handy for backfilling a missed week or spot-checking a prior week's numbers —
# without needing a separate code path. Every downstream window (prior 7 days,
# MTD, YTD, trend, monthly history) is computed relative to TODAY, so setting
# this one value consistently shifts the entire report.
# ─────────────────────────────────────────────────────────────────────────────
PACIFIC = ZoneInfo("America/Los_Angeles")
_as_of_override = os.getenv("REPORT_AS_OF_DATE", "").strip()
if _as_of_override:
    TODAY = date.fromisoformat(_as_of_override)
    print(f"REPORT_AS_OF_DATE set — running as if today were {TODAY} (real today is {datetime.now(PACIFIC).date()}).")
else:
    TODAY = datetime.now(PACIFIC).date()


def prior_n_days_excluding_today(today, n):
    end = today - timedelta(days=1)
    start = end - timedelta(days=n - 1)
    return start, end


def same_window_last_year(start, end):
    def shift(d):
        try:
            return d.replace(year=d.year - 1)
        except ValueError:  # Feb 29 -> Feb 28
            return d.replace(year=d.year - 1, day=28)
    return shift(start), shift(end)


LAST7_START, LAST7_END = prior_n_days_excluding_today(TODAY, 7)
LAST7_LY_START, LAST7_LY_END = same_window_last_year(LAST7_START, LAST7_END)

MTD_START = TODAY.replace(day=1)
MTD_END = TODAY
MTD_LY_START, MTD_LY_END = same_window_last_year(MTD_START, MTD_END)

MONTH_LABEL = TODAY.strftime("%b-%y")
DAYS_IN_MONTH = calendar.monthrange(TODAY.year, TODAY.month)[1]
DAY_OF_MONTH = TODAY.day
LAST_YEAR = TODAY.year - 1

print(f"Prior 7 days:      {LAST7_START} → {LAST7_END}   (LY: {LAST7_LY_START} → {LAST7_LY_END})")
print(f"MTD:               {MTD_START} → {MTD_END}       (LY: {MTD_LY_START} → {MTD_LY_END})")
print(f"Current month:     {MONTH_LABEL}  (day {DAY_OF_MONTH} of {DAYS_IN_MONTH})")

# ─────────────────────────────────────────────────────────────────────────────
# Cell 5 equivalent — financial plan targets (hardcoded from the P&L)
# Update these three dicts whenever you get a revised financial plan.
# ─────────────────────────────────────────────────────────────────────────────
INCOME_TARGET = {
    "Jan-26": 447999.25, "Feb-26": 436037.50, "Mar-26": 452386.18, "Apr-26": 527081.10,
    "May-26": 580020.13, "Jun-26": 592803.52, "Jul-26": 566849.73, "Aug-26": 583966.76,
    "Sep-26": 481385.37, "Oct-26": 555141.18, "Nov-26": 707948.83, "Dec-26": 611375.09,
}

GROSS_SALES_TARGET = {
    "Jan-26": 513996.39, "Feb-26": 500272.49, "Mar-26": 519029.58, "Apr-26": 604728.20,
    "May-26": 665465.96, "Jun-26": 680132.54, "Jul-26": 650355.36, "Aug-26": 669993.99,
    "Sep-26": 552300.79, "Oct-26": 636921.96, "Nov-26": 812240.51, "Dec-26": 701439.99,
}

GOOGLE_SPEND_BUDGET = {
    "Jan-26": 89599.85, "Feb-26": 87207.50, "Mar-26": 90477.24, "Apr-26": 105416.22,
    "May-26": 116004.03, "Jun-26": 118560.70, "Jul-26": 113369.95, "Aug-26": 116793.35,
    "Sep-26": 96277.07, "Oct-26": 111028.24, "Nov-26": 141589.77, "Dec-26": 122275.02,
}

print(f"Plan loaded — {MONTH_LABEL} Net Income target: ${INCOME_TARGET[MONTH_LABEL]:,.2f} | "
      f"Gross Sales target: ${GROSS_SALES_TARGET[MONTH_LABEL]:,.2f} | "
      f"Google spend budget: ${GOOGLE_SPEND_BUDGET[MONTH_LABEL]:,.2f}")

# ─────────────────────────────────────────────────────────────────────────────
# Cell 6 equivalent — Google Ads spend
# Uses GoogleAdsClient.load_from_env(), which reads GOOGLE_ADS_* env vars directly —
# no google-ads.yaml file needed (that was only useful for a Drive-mounted Colab
# session; GitHub Actions Secrets are exposed as env vars, not files).
# ─────────────────────────────────────────────────────────────────────────────
# Built from an explicit dict (same keys as the google-ads.yaml already proven to work
# in the Colab pipeline: developer_token, client_id, client_secret, refresh_token,
# login_customer_id, use_proto_plus) rather than load_from_env(), so there's no
# dependency on guessing the library's own env-var-name mapping correctly.
_google_ads_config = {
    "developer_token": os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"],
    "client_id": os.environ["GOOGLE_ADS_CLIENT_ID"],
    "client_secret": os.environ["GOOGLE_ADS_CLIENT_SECRET"],
    "refresh_token": os.environ["GOOGLE_ADS_REFRESH_TOKEN"],
    "login_customer_id": os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"].replace("-", ""),
    "use_proto_plus": True,
}
client = GoogleAdsClient.load_from_dict(_google_ads_config)
customer_id = os.getenv("GOOGLE_ADS_CUSTOMER_ID", "").replace("-", "")
ga_service = client.get_service("GoogleAdsService")


def google_spend(start: date, end: date):
    """Total Google Ads cost (all campaigns) between start and end, inclusive."""
    query = f"""
        SELECT metrics.cost_micros
        FROM campaign
        WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'
    """
    total = 0
    try:
        for row in ga_service.search(customer_id=customer_id, query=query):
            total += row.metrics.cost_micros
    except GoogleAdsException as ex:
        print(f"Google Ads error for {start}–{end}:")
        for error in ex.failure.errors:
            print(f"   {error.error_code} {error.message}")
        return None
    return total / 1e6


SPEND_LAST7 = google_spend(LAST7_START, LAST7_END)
SPEND_LAST7_LY = google_spend(LAST7_LY_START, LAST7_LY_END)
SPEND_MTD = google_spend(MTD_START, MTD_END)
SPEND_MTD_LY = google_spend(MTD_LY_START, MTD_LY_END)

print(f"Prior 7 days spend:  ${SPEND_LAST7:,.2f}   (LY: ${SPEND_LAST7_LY:,.2f})")
print(f"MTD spend:           ${SPEND_MTD:,.2f}   (LY: ${SPEND_MTD_LY:,.2f})")

MONTHLY_SPEND_ACTUAL = {}
for m in range(1, TODAY.month + 1):
    m_start = date(TODAY.year, m, 1)
    m_end = TODAY if m == TODAY.month else date(TODAY.year, m, calendar.monthrange(TODAY.year, m)[1])
    label = m_start.strftime("%b-%y")
    MONTHLY_SPEND_ACTUAL[label] = google_spend(m_start, m_end)
    print(f"   {label}: ${MONTHLY_SPEND_ACTUAL[label]:,.2f} spend")

MONTHLY_SPEND_ACTUAL_LY = {}
for m in range(1, 13):
    m_start = date(LAST_YEAR, m, 1)
    m_end = date(LAST_YEAR, m, calendar.monthrange(LAST_YEAR, m)[1])
    label = m_start.strftime("%b-%y")
    MONTHLY_SPEND_ACTUAL_LY[label] = google_spend(m_start, m_end)
    print(f"   {label} (LY): ${MONTHLY_SPEND_ACTUAL_LY[label]:,.2f} spend")

# ─────────────────────────────────────────────────────────────────────────────
# Cell 7 equivalent — Shopify gross & net sales (ShopifyQL)
# ─────────────────────────────────────────────────────────────────────────────
SHOPIFY_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN", "new-york-hardware-online.myshopify.com")
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID", "")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET", "")
TOKEN_ENDPOINT = f"https://{SHOPIFY_DOMAIN}/admin/oauth/access_token"
shop = SHOPIFY_DOMAIN.replace(".myshopify.com", "")


def get_shopify_token() -> str:
    """Exchanges Client ID + Secret for a fresh Admin API token (client_credentials grant).
    Requires the app to have the read_reports scope enabled for ShopifyQL access."""
    if not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
        raise ValueError("SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET must be set (as env vars / GitHub secrets)")
    resp = requests.post(
        TOKEN_ENDPOINT,
        json={
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Shopify token request failed ({resp.status_code}): {resp.text}")
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in response: {resp.json()}")
    return token


token = get_shopify_token()
print("Shopify Admin API token obtained.")

sql_client = ShopifyQLClient(shop=shop, access_token=token, version="2025-01")


def _run_shopifyql_df(query: str):
    try:
        return sql_client.query_pandas(query)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 401:
            raise RuntimeError(
                "401 from ShopifyQL. Most likely the app behind SHOPIFY_CLIENT_ID/SECRET "
                "doesn't have the 'read_reports' scope enabled. Add it at "
                "dev.shopify.com → Your App → Configuration → Access scopes, create a new "
                "app version, and re-run."
            ) from e
        raise


def shopify_sales(start: date, end: date) -> dict:
    """Returns {gross_sales, net_sales} for the window."""
    query = f"FROM sales SHOW gross_sales, net_sales SINCE {start.isoformat()} UNTIL {end.isoformat()}"
    df = _run_shopifyql_df(query)
    if len(df) == 0:
        return {"gross_sales": 0.0, "net_sales": 0.0}
    row = df.iloc[0]
    return {
        "gross_sales": float(row.get("gross_sales", 0) or 0),
        "net_sales": float(row.get("net_sales", 0) or 0),
    }


SALES_LAST7 = shopify_sales(LAST7_START, LAST7_END)
SALES_LAST7_LY = shopify_sales(LAST7_LY_START, LAST7_LY_END)
SALES_MTD = shopify_sales(MTD_START, MTD_END)
SALES_MTD_LY = shopify_sales(MTD_LY_START, MTD_LY_END)

print(f"Prior 7 days:  gross ${SALES_LAST7['gross_sales']:,.2f}  net ${SALES_LAST7['net_sales']:,.2f}")
print(f"MTD:           gross ${SALES_MTD['gross_sales']:,.2f}  net ${SALES_MTD['net_sales']:,.2f}")

# Net Income = net_sales + shipping_charges (NOT Shopify's "total_sales", which also
# includes sales tax and duties — see notebook Cell 7 for the full rationale, verified
# against 2025 actuals: total_sales implied an ~8.4% reduction from Gross vs. the real
# ~11.2% discount+return rate).
MONTHLY_INCOME_ACTUAL = {}
MONTHLY_GROSS_SALES_ACTUAL = {}
for m in range(1, TODAY.month + 1):
    m_start = date(TODAY.year, m, 1)
    m_end = TODAY if m == TODAY.month else date(TODAY.year, m, calendar.monthrange(TODAY.year, m)[1])
    label = m_start.strftime("%b-%y")
    query = f"FROM sales SHOW net_sales, shipping_charges, gross_sales SINCE {m_start.isoformat()} UNTIL {m_end.isoformat()}"
    df = _run_shopifyql_df(query)
    row = df.iloc[0] if len(df) else None
    MONTHLY_INCOME_ACTUAL[label] = float((row.get("net_sales", 0) or 0) + (row.get("shipping_charges", 0) or 0)) if row is not None else 0.0
    MONTHLY_GROSS_SALES_ACTUAL[label] = float(row.get("gross_sales", 0) or 0) if row is not None else 0.0
    print(f"   {label}: ${MONTHLY_INCOME_ACTUAL[label]:,.2f} net income (net sales + shipping) | ${MONTHLY_GROSS_SALES_ACTUAL[label]:,.2f} gross sales")

MONTHLY_INCOME_ACTUAL_LY = {}
MONTHLY_GROSS_SALES_ACTUAL_LY = {}
for m in range(1, 13):
    m_start = date(LAST_YEAR, m, 1)
    m_end = date(LAST_YEAR, m, calendar.monthrange(LAST_YEAR, m)[1])
    label = m_start.strftime("%b-%y")
    query = f"FROM sales SHOW net_sales, shipping_charges, gross_sales SINCE {m_start.isoformat()} UNTIL {m_end.isoformat()}"
    df = _run_shopifyql_df(query)
    row = df.iloc[0] if len(df) else None
    MONTHLY_INCOME_ACTUAL_LY[label] = float((row.get("net_sales", 0) or 0) + (row.get("shipping_charges", 0) or 0)) if row is not None else 0.0
    MONTHLY_GROSS_SALES_ACTUAL_LY[label] = float(row.get("gross_sales", 0) or 0) if row is not None else 0.0
    print(f"   {label} (LY): ${MONTHLY_INCOME_ACTUAL_LY[label]:,.2f} net income")

# ─────────────────────────────────────────────────────────────────────────────
# Cell 8 equivalent — True ROAS / ROI + full report_data build
# ─────────────────────────────────────────────────────────────────────────────


def ratios(sales, spend):
    if not spend:
        return {"roas": None, "roi": None}
    return {"roas": sales["gross_sales"] / spend, "roi": sales["net_sales"] / spend}


def pct_change(new, old):
    if not old:
        return None
    return (new - old) / old * 100


period_last7 = {**SALES_LAST7, "spend": SPEND_LAST7, **ratios(SALES_LAST7, SPEND_LAST7)}
period_last7_ly = {**SALES_LAST7_LY, "spend": SPEND_LAST7_LY, **ratios(SALES_LAST7_LY, SPEND_LAST7_LY)}
period_mtd = {**SALES_MTD, "spend": SPEND_MTD, **ratios(SALES_MTD, SPEND_MTD)}
period_mtd_ly = {**SALES_MTD_LY, "spend": SPEND_MTD_LY, **ratios(SALES_MTD_LY, SPEND_MTD_LY)}

yoy_deltas = {
    "last7_roas_pct": pct_change(period_last7["roas"], period_last7_ly["roas"]),
    "last7_roi_pct": pct_change(period_last7["roi"], period_last7_ly["roi"]),
    "mtd_roas_pct": pct_change(period_mtd["roas"], period_mtd_ly["roas"]),
    "mtd_roi_pct": pct_change(period_mtd["roi"], period_mtd_ly["roi"]),
}

plan = {
    "month": MONTH_LABEL,
    "net_income_target": INCOME_TARGET[MONTH_LABEL],
    "net_income_actual_mtd": MONTHLY_INCOME_ACTUAL[MONTH_LABEL],
    "net_income_prorated_target": INCOME_TARGET[MONTH_LABEL] * DAY_OF_MONTH / DAYS_IN_MONTH,
    "net_income_projected": MONTHLY_INCOME_ACTUAL[MONTH_LABEL] / DAY_OF_MONTH * DAYS_IN_MONTH,
    "gross_sales_target": GROSS_SALES_TARGET[MONTH_LABEL],
    "gross_sales_actual_mtd": MONTHLY_GROSS_SALES_ACTUAL[MONTH_LABEL],
    "gross_sales_prorated_target": GROSS_SALES_TARGET[MONTH_LABEL] * DAY_OF_MONTH / DAYS_IN_MONTH,
    "gross_sales_projected": MONTHLY_GROSS_SALES_ACTUAL[MONTH_LABEL] / DAY_OF_MONTH * DAYS_IN_MONTH,
    "spend_budget": GOOGLE_SPEND_BUDGET[MONTH_LABEL],
    "spend_actual_mtd": MONTHLY_SPEND_ACTUAL[MONTH_LABEL],
    "spend_prorated_budget": GOOGLE_SPEND_BUDGET[MONTH_LABEL] * DAY_OF_MONTH / DAYS_IN_MONTH,
    "spend_projected": MONTHLY_SPEND_ACTUAL[MONTH_LABEL] / DAY_OF_MONTH * DAYS_IN_MONTH,
}

monthly_history = []
for label in INCOME_TARGET:
    is_current = label == MONTH_LABEL
    income_actual = MONTHLY_INCOME_ACTUAL.get(label)
    spend_actual = MONTHLY_SPEND_ACTUAL.get(label)
    gross_actual = MONTHLY_GROSS_SALES_ACTUAL.get(label)

    income_projected = None
    spend_projected = None
    gross_projected = None
    if is_current and income_actual is not None:
        income_projected = income_actual / DAY_OF_MONTH * DAYS_IN_MONTH
    if is_current and spend_actual is not None:
        spend_projected = spend_actual / DAY_OF_MONTH * DAYS_IN_MONTH
    if is_current and gross_actual is not None:
        gross_projected = gross_actual / DAY_OF_MONTH * DAYS_IN_MONTH

    income_basis_for_delta = income_projected if is_current else income_actual
    gross_basis_for_delta = gross_projected if is_current else gross_actual
    income_delta = (income_basis_for_delta - INCOME_TARGET[label]) if income_basis_for_delta is not None else None
    gross_sales_delta = (gross_basis_for_delta - GROSS_SALES_TARGET[label]) if gross_basis_for_delta is not None else None

    monthly_history.append({
        "month": label,
        "income_target": INCOME_TARGET[label],
        "income_actual": income_actual,
        "income_projected": income_projected,
        # The value the Δ (Net) column is actually measured against: the
        # extrapolated full-month pace for the current (open) month, or just
        # the realized actual for any closed month — shown as its own column
        # so the delta is never a mystery number.
        "income_delta_basis": round(income_basis_for_delta, 2) if income_basis_for_delta is not None else None,
        "income_delta": round(income_delta, 2) if income_delta is not None else None,
        "gross_sales_target": GROSS_SALES_TARGET[label],
        "gross_sales_actual": gross_actual,
        "gross_sales_projected": gross_projected,
        "gross_sales_delta_basis": round(gross_basis_for_delta, 2) if gross_basis_for_delta is not None else None,
        "gross_sales_delta": round(gross_sales_delta, 2) if gross_sales_delta is not None else None,
        "spend_budget": GOOGLE_SPEND_BUDGET[label],
        "spend_actual": spend_actual,
        "spend_projected": spend_projected,
        "is_current": is_current,
    })

totals = {
    "income_target": round(sum(INCOME_TARGET.values()), 2),
    "income_actual": round(sum(MONTHLY_INCOME_ACTUAL.values()), 2),
    "gross_sales_target": round(sum(GROSS_SALES_TARGET.values()), 2),
    "gross_sales_actual": round(sum(MONTHLY_GROSS_SALES_ACTUAL.values()), 2),
    "spend_budget": round(sum(GOOGLE_SPEND_BUDGET.values()), 2),
    "spend_actual": round(sum(MONTHLY_SPEND_ACTUAL.values()), 2),
}
totals["roas"] = (totals["gross_sales_actual"] / totals["spend_actual"]) if totals["spend_actual"] else None
totals["income_delta"] = round(totals["income_actual"] - totals["income_target"], 2)
totals["gross_sales_delta"] = round(totals["gross_sales_actual"] - totals["gross_sales_target"], 2)

months_order = list(INCOME_TARGET.keys())
current_idx = months_order.index(MONTH_LABEL)

completed_income_actual = sum(MONTHLY_INCOME_ACTUAL[m] for m in months_order[:current_idx])
completed_income_target = sum(INCOME_TARGET[m] for m in months_order[:current_idx])
completed_gross_actual = sum(MONTHLY_GROSS_SALES_ACTUAL[m] for m in months_order[:current_idx])
completed_gross_target = sum(GROSS_SALES_TARGET[m] for m in months_order[:current_idx])
completed_spend_actual = sum(MONTHLY_SPEND_ACTUAL[m] for m in months_order[:current_idx])
completed_spend_budget = sum(GOOGLE_SPEND_BUDGET[m] for m in months_order[:current_idx])

ytd_income_actual = completed_income_actual + MONTHLY_INCOME_ACTUAL[MONTH_LABEL]
ytd_income_target = completed_income_target + plan["net_income_prorated_target"]
ytd_gross_actual = completed_gross_actual + MONTHLY_GROSS_SALES_ACTUAL[MONTH_LABEL]
ytd_gross_target = completed_gross_target + plan["gross_sales_prorated_target"]
ytd_spend_actual = completed_spend_actual + MONTHLY_SPEND_ACTUAL[MONTH_LABEL]
ytd_spend_budget = completed_spend_budget + plan["spend_prorated_budget"]
ytd_roas = (ytd_gross_actual / ytd_spend_actual) if ytd_spend_actual else None

ytd = {
    "label": f"YTD (Jan 1 – {TODAY.strftime('%b %d')})",
    "income_target": round(ytd_income_target, 2),
    "income_actual": round(ytd_income_actual, 2),
    "income_delta": round(ytd_income_actual - ytd_income_target, 2),
    "income_status": "hit" if ytd_income_actual >= ytd_income_target else "miss",
    "gross_sales_target": round(ytd_gross_target, 2),
    "gross_sales_actual": round(ytd_gross_actual, 2),
    "gross_sales_delta": round(ytd_gross_actual - ytd_gross_target, 2),
    "spend_budget": round(ytd_spend_budget, 2),
    "spend_actual": round(ytd_spend_actual, 2),
    "spend_status": "hit" if ytd_spend_actual <= ytd_spend_budget else "miss",
    "roas": round(ytd_roas, 3) if ytd_roas is not None else None,
}

gross_basis_actual = completed_gross_actual + (plan["gross_sales_projected"] or 0)
gross_basis_target = completed_gross_target + GROSS_SALES_TARGET[MONTH_LABEL]
spend_basis_actual = completed_spend_actual + (plan["spend_projected"] or 0)
spend_basis_target = completed_spend_budget + GOOGLE_SPEND_BUDGET[MONTH_LABEL]

gross_attainment = gross_basis_actual / gross_basis_target if gross_basis_target else None
spend_attainment = spend_basis_actual / spend_basis_target if spend_basis_target else None

ANNUAL_INCOME_TARGET = sum(INCOME_TARGET.values())
ANNUAL_GROSS_SALES_TARGET = sum(GROSS_SALES_TARGET.values())
ANNUAL_SPEND_BUDGET = sum(GOOGLE_SPEND_BUDGET.values())

PROJECTED_ANNUAL_GROSS_SALES = gross_attainment * ANNUAL_GROSS_SALES_TARGET if gross_attainment is not None else None
PROJECTED_ANNUAL_SPEND = spend_attainment * ANNUAL_SPEND_BUDGET if spend_attainment is not None else None

if ytd_gross_actual:
    realized_reduction_rate = 1 - (ytd_income_actual / ytd_gross_actual)
elif ANNUAL_GROSS_SALES_TARGET:
    realized_reduction_rate = 1 - (ANNUAL_INCOME_TARGET / ANNUAL_GROSS_SALES_TARGET)
else:
    realized_reduction_rate = 0.0

PROJECTED_ANNUAL_INCOME = (
    PROJECTED_ANNUAL_GROSS_SALES * (1 - realized_reduction_rate)
    if PROJECTED_ANNUAL_GROSS_SALES is not None else None
)
income_attainment = (
    PROJECTED_ANNUAL_INCOME / ANNUAL_INCOME_TARGET
    if (PROJECTED_ANNUAL_INCOME is not None and ANNUAL_INCOME_TARGET) else None
)

net_income_projected_derived = (
    plan["gross_sales_projected"] * (1 - realized_reduction_rate)
    if plan["gross_sales_projected"] is not None else plan["net_income_projected"]
)

year_complete = (TODAY.month == 12 and TODAY.day == DAYS_IN_MONTH)
if year_complete:
    annual_income_status = "hit" if PROJECTED_ANNUAL_INCOME >= ANNUAL_INCOME_TARGET else "miss"
    annual_spend_status = "hit" if PROJECTED_ANNUAL_SPEND <= ANNUAL_SPEND_BUDGET else "miss"
else:
    annual_income_status = "ontrack" if (income_attainment or 0) >= 1 else "atrisk"
    annual_spend_status = "ontrack" if (spend_attainment or 0) <= 1 else "atrisk"
totals["income_status"] = annual_income_status
totals["spend_status"] = annual_spend_status

cum_target_income, cum_actual_income, cum_projected_income = [], [], []
cum_target_spend, cum_actual_spend, cum_projected_spend = [], [], []
running_target_income = running_actual_income = running_projected_income = 0.0
running_target_spend = running_actual_spend = running_projected_spend = 0.0

for i, m in enumerate(months_order):
    running_target_income += INCOME_TARGET[m]
    running_target_spend += GOOGLE_SPEND_BUDGET[m]
    cum_target_income.append(round(running_target_income, 2))
    cum_target_spend.append(round(running_target_spend, 2))

    if i < current_idx:
        running_actual_income += MONTHLY_INCOME_ACTUAL[m]
        running_actual_spend += MONTHLY_SPEND_ACTUAL[m]
        cum_actual_income.append(round(running_actual_income, 2))
        cum_actual_spend.append(round(running_actual_spend, 2))
        cum_projected_income.append(None)
        cum_projected_spend.append(None)
    elif i == current_idx:
        running_actual_income += MONTHLY_INCOME_ACTUAL[m]
        running_actual_spend += MONTHLY_SPEND_ACTUAL[m]
        cum_actual_income.append(round(running_actual_income, 2))
        cum_actual_spend.append(round(running_actual_spend, 2))
        running_projected_income = (running_actual_income - MONTHLY_INCOME_ACTUAL[m]) + (net_income_projected_derived or 0)
        running_projected_spend = (running_actual_spend - MONTHLY_SPEND_ACTUAL[m]) + (plan["spend_projected"] or 0)
        cum_projected_income.append(round(running_projected_income, 2))
        cum_projected_spend.append(round(running_projected_spend, 2))
    else:
        cum_actual_income.append(None)
        cum_actual_spend.append(None)
        running_projected_income += INCOME_TARGET[m] * (income_attainment if income_attainment is not None else 1)
        running_projected_spend += GOOGLE_SPEND_BUDGET[m] * (spend_attainment if spend_attainment is not None else 1)
        cum_projected_income.append(round(running_projected_income, 2))
        cum_projected_spend.append(round(running_projected_spend, 2))

ly_months_order = [date(LAST_YEAR, m, 1).strftime("%b-%y") for m in range(1, 13)]
cum_actual_income_ly, cum_actual_spend_ly = [], []
running_ly_income = running_ly_spend = 0.0
for m in ly_months_order:
    running_ly_income += MONTHLY_INCOME_ACTUAL_LY[m]
    running_ly_spend += MONTHLY_SPEND_ACTUAL_LY[m]
    cum_actual_income_ly.append(round(running_ly_income, 2))
    cum_actual_spend_ly.append(round(running_ly_spend, 2))

ANNUAL_INCOME_LY_TOTAL = sum(MONTHLY_INCOME_ACTUAL_LY.values())
ANNUAL_GROSS_SALES_LY_TOTAL = sum(MONTHLY_GROSS_SALES_ACTUAL_LY.values())
ANNUAL_SPEND_LY_TOTAL = sum(MONTHLY_SPEND_ACTUAL_LY.values())


def build_recommendation(income_att, spend_att):
    if income_att is None or spend_att is None:
        return "Not enough data yet to project a full-year trend."
    income_gap = (income_att - 1) * 100
    spend_gap = (spend_att - 1) * 100
    if income_att >= 1 and spend_att <= 1:
        return (f"Trending {income_gap:+.1f}% vs. the annual income target while pacing "
                f"{-spend_gap:.1f}% under the ad spend budget — efficient. No spend change needed; "
                f"the unused budget headroom could be reinvested for further growth if desired.")
    if income_att >= 1 and spend_att > 1:
        return (f"Trending {income_gap:+.1f}% vs. the annual income target, but spend is pacing "
                f"{spend_gap:.1f}% over budget. On track to hit target, just at a higher cost than "
                f"planned — monitor True ROAS to confirm the extra spend is still worth it.")
    if income_att < 1 and spend_att < 1:
        return (f"Trending {income_gap:.1f}% short of the annual income target, while ad spend is "
                f"pacing {-spend_gap:.1f}% under budget. There's unused budget headroom — given the "
                f"current True ROAS, increasing spend could help close the gap toward target.")
    return (f"Trending {income_gap:.1f}% short of the annual income target even though spend is "
            f"already pacing at or above budget ({spend_gap:+.1f}%). This points to an efficiency "
            f"(ROAS) issue rather than a spend-level issue — consider optimizing campaigns rather "
            f"than increasing budget.")


trend = {
    "months": months_order,
    "current_idx": current_idx,
    "cum_target_income": cum_target_income,
    "cum_actual_income": cum_actual_income,
    "cum_projected_income": cum_projected_income,
    "cum_actual_income_ly": cum_actual_income_ly,
    "cum_target_spend": cum_target_spend,
    "cum_actual_spend": cum_actual_spend,
    "cum_projected_spend": cum_projected_spend,
    "cum_actual_spend_ly": cum_actual_spend_ly,
    "annual_income_target": round(ANNUAL_INCOME_TARGET, 2),
    "annual_gross_sales_target": round(ANNUAL_GROSS_SALES_TARGET, 2),
    "annual_spend_budget": round(ANNUAL_SPEND_BUDGET, 2),
    "annual_income_ly_total": round(ANNUAL_INCOME_LY_TOTAL, 2),
    "annual_gross_sales_ly_total": round(ANNUAL_GROSS_SALES_LY_TOTAL, 2),
    "annual_spend_ly_total": round(ANNUAL_SPEND_LY_TOTAL, 2),
    "projected_annual_income": round(PROJECTED_ANNUAL_INCOME, 2) if PROJECTED_ANNUAL_INCOME is not None else None,
    "projected_annual_gross_sales": round(PROJECTED_ANNUAL_GROSS_SALES, 2) if PROJECTED_ANNUAL_GROSS_SALES is not None else None,
    "projected_annual_spend": round(PROJECTED_ANNUAL_SPEND, 2) if PROJECTED_ANNUAL_SPEND is not None else None,
    "income_attainment_pct": round(income_attainment * 100, 1) if income_attainment is not None else None,
    "gross_attainment_pct": round(gross_attainment * 100, 1) if gross_attainment is not None else None,
    "spend_attainment_pct": round(spend_attainment * 100, 1) if spend_attainment is not None else None,
    "recommendation": build_recommendation(income_attainment, spend_attainment),
}

LAST7_DAYS_COUNT = 7
CURRENT_DAILY_GROSS = period_last7["gross_sales"] / LAST7_DAYS_COUNT
CURRENT_DAILY_NET = period_last7["net_sales"] / LAST7_DAYS_COUNT
CURRENT_DAILY_SPEND = (period_last7["spend"] / LAST7_DAYS_COUNT) if period_last7["spend"] else None
CURRENT_ROAS = period_last7["roas"]
CURRENT_ROI = period_last7["roi"]

TARGET_DAILY_REVENUE = [30000, 33000, 35000, 40000, 45000, 50000]

scaling_targets = []
for target in TARGET_DAILY_REVENUE:
    required_spend = (target / CURRENT_ROAS) if CURRENT_ROAS else None
    implied_net = (required_spend * CURRENT_ROI) if (required_spend is not None and CURRENT_ROI is not None) else None
    delta_spend = (required_spend - CURRENT_DAILY_SPEND) if (required_spend is not None and CURRENT_DAILY_SPEND is not None) else None
    scaling_targets.append({
        "label": f"${target/1000:.0f}K/day",
        "daily_revenue_target": target,
        "required_daily_spend": round(required_spend, 2) if required_spend is not None else None,
        "delta_spend_vs_current": round(delta_spend, 2) if delta_spend is not None else None,
        "implied_net_sales": round(implied_net, 2) if implied_net is not None else None,
        "monthly_spend_equivalent": round(required_spend * 30, 2) if required_spend is not None else None,
    })

scaling = {
    "basis": "Trailing 7-day average (excludes today)",
    "current_daily_gross": round(CURRENT_DAILY_GROSS, 2),
    "current_daily_net": round(CURRENT_DAILY_NET, 2),
    "current_daily_spend": round(CURRENT_DAILY_SPEND, 2) if CURRENT_DAILY_SPEND is not None else None,
    "current_roas": round(CURRENT_ROAS, 3) if CURRENT_ROAS is not None else None,
    "current_roi": round(CURRENT_ROI, 3) if CURRENT_ROI is not None else None,
    "targets": scaling_targets,
}

# ─────────────────────────────────────────────────────────────────────────────
# MoM True ROAS trend — re-visualizes the same monthly_history data already in
# the Monthly Hits & Misses table as a line chart, so the reader sees the raw
# numbers first, then immediately sees the trend. Only includes months with
# actual data so far this year (naturally grows each month, nothing to update).
#
# ROAS_SCALEUP_START_MONTH / ROAS_SCALEUP_STABILIZATION_MONTH are a narrative
# annotation per the CFO's spec: they mark the deliberate ad-spend scale-up
# toward the $30K/day daily revenue target, so a post-peak ROAS dip reads as
# planned pacing rather than eroding efficiency. Update
# ROAS_SCALEUP_STABILIZATION_MONTH (e.g. "Oct-26") once a flattening point is
# projected — leave it as None until then, and the chart just won't mark one.
# ─────────────────────────────────────────────────────────────────────────────
ROAS_SCALEUP_START_MONTH = "Jun-26"
ROAS_SCALEUP_STABILIZATION_MONTH = "Sep-26"

# Manually entered forward-looking ROAS projections — not derived from actual
# spend/sales data, just business projections plugged in ahead of the real
# months landing. Rendered as a dotted continuation of the True ROAS line so
# they're visually distinct from measured actuals. A month is automatically
# dropped from this list once real data exists for it (no manual cleanup
# needed — just leave old entries here, or replace the values as projections
# get revised).
ROAS_TREND_MANUAL_PROJECTIONS = {
    "Aug-26": 6.1,
    "Sep-26": 6.5,
}

roas_trend_months, roas_trend_roas, roas_trend_daily_spend = [], [], []
for m in monthly_history:
    if m["gross_sales_actual"] is None or not m["spend_actual"]:
        continue
    roas_trend_months.append(m["month"])
    roas_trend_roas.append(round(m["gross_sales_actual"] / m["spend_actual"], 3))
    _dt = datetime.strptime(m["month"], "%b-%y")
    _days_in_full_month = calendar.monthrange(_dt.year, _dt.month)[1]
    _days_elapsed = DAY_OF_MONTH if m["is_current"] else _days_in_full_month
    roas_trend_daily_spend.append(round(m["spend_actual"] / _days_elapsed, 2) if _days_elapsed else None)

roas_trend_actual_count = len(roas_trend_months)

for _proj_month, _proj_roas in ROAS_TREND_MANUAL_PROJECTIONS.items():
    if _proj_month in roas_trend_months:
        continue  # real data already landed for this month — don't double up
    roas_trend_months.append(_proj_month)
    roas_trend_roas.append(_proj_roas)
    roas_trend_daily_spend.append(None)

roas_trend = {
    "months": roas_trend_months,
    "roas": roas_trend_roas,
    "daily_spend": roas_trend_daily_spend,
    "actual_count": roas_trend_actual_count,
    "annotation_start_month": ROAS_SCALEUP_START_MONTH,
    "stabilization_month": ROAS_SCALEUP_STABILIZATION_MONTH,
    "annotation_label": ("Planned scale-up period — ROAS dip reflects deliberate pacing toward "
                         "$30K/day daily revenue target, staged to protect CS capacity."),
}

report_data = {
    "store": "New York Hardware, Inc",
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    "last7_range": f"{LAST7_START:%b %d} – {LAST7_END:%b %d, %Y}",
    "periods": {
        "last7": period_last7, "last7_yoy": period_last7_ly,
        "mtd": period_mtd, "mtd_yoy": period_mtd_ly,
    },
    "yoy_deltas": yoy_deltas,
    "plan": plan,
    "monthly_history": monthly_history,
    "totals": totals,
    "ytd": ytd,
    "trend": trend,
    "scaling": scaling,
    "roas_trend": roas_trend,
}

print("Report data built.")

# ─────────────────────────────────────────────────────────────────────────────
# Cell 9 equivalent — render HTML dashboard
# ─────────────────────────────────────────────────────────────────────────────
template_html = (SCRIPT_DIR / "dashboard_template.html").read_text(encoding="utf-8")
html_out = template_html.replace("__REPORT_DATA_JSON__", json.dumps(report_data))
print(f"Dashboard rendered ({len(html_out):,} characters).")

# ─────────────────────────────────────────────────────────────────────────────
# Write outputs — index.html at repo root (served by GitHub Pages, always the
# most recently generated run — including as-of-date backfill runs) + a dated
# HTML snapshot under history/ and a dated JSON snapshot under data/ (kept in
# git history for auditing / browsing past runs). The workflow file commits
# and pushes these; there is no Drive step in this version.
# ─────────────────────────────────────────────────────────────────────────────
(SCRIPT_DIR / "index.html").write_text(html_out, encoding="utf-8")

ts = TODAY.strftime("%Y%m%d")

history_dir = SCRIPT_DIR / "history"
history_dir.mkdir(exist_ok=True)
(history_dir / f"report_{ts}.html").write_text(html_out, encoding="utf-8")

data_dir = SCRIPT_DIR / "data"
data_dir.mkdir(exist_ok=True)
(data_dir / f"weekly_kpi_data_{ts}.json").write_text(json.dumps(report_data, indent=2), encoding="utf-8")

print(f"Wrote {SCRIPT_DIR / 'index.html'}")
print(f"Wrote {history_dir / f'report_{ts}.html'}")
print(f"Wrote {data_dir / f'weekly_kpi_data_{ts}.json'}")

# ─────────────────────────────────────────────────────────────────────────────
# PDF export — renders the dashboard through headless Chromium (not a
# JS-blind HTML-to-PDF tool), so the Chart.js canvases actually appear in the
# PDF exactly as they look on the live page. Written to "latest_report.pdf"
# at repo root + a dated archive under history/. Skipped (non-fatally) if
# Playwright/Chromium isn't available — everything above is unaffected.
# ─────────────────────────────────────────────────────────────────────────────
pdf_path = SCRIPT_DIR / "latest_report.pdf"
pdf_generated = False
try:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(html_out, wait_until="load")
        page.wait_for_timeout(500)  # small buffer for any late chart/layout settling
        page.pdf(
            path=str(pdf_path),
            format="A4",
            landscape=True,
            print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
        )
        browser.close()

    (history_dir / f"report_{ts}.pdf").write_bytes(pdf_path.read_bytes())
    pdf_generated = True
    print(f"Wrote {pdf_path}")
    print(f"Wrote {history_dir / f'report_{ts}.pdf'}")
except Exception as e:
    print(f"PDF export failed (non-fatal — HTML/Sheets outputs are unaffected): {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Google Sheets — full data dump. Every table shown on the dashboard gets its
# own tab, cleared and rewritten fresh each run (not an accumulating log) so
# each tab always mirrors exactly what's on the live page this run. Skipped
# entirely if the two env vars below aren't set — everything above this
# point is unaffected either way.
# ─────────────────────────────────────────────────────────────────────────────
GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "").strip()

if not GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON or not GOOGLE_SHEET_ID:
    print("GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON / GOOGLE_SHEET_ID not set — skipping Google Sheets export.")
else:
    try:
        import gspread

        def _write_sheet_table(gc_client, sheet_id, tab_name, header, rows):
            sh = gc_client.open_by_key(sheet_id)
            try:
                ws = sh.worksheet(tab_name)
                ws.clear()
            except gspread.WorksheetNotFound:
                ws = sh.add_worksheet(title=tab_name, rows=max(len(rows) + 10, 50), cols=max(len(header), 10))
            ws.update([header] + [["" if v is None else v for v in row] for row in rows], value_input_option="USER_ENTERED")

        gc = gspread.service_account_from_dict(json.loads(GOOGLE_SHEETS_SERVICE_ACCOUNT_JSON))

        def _yoy_pct(new, old):
            v = pct_change(new, old)
            return round(v, 1) if v is not None else None

        # Tabs 1-2: Prior 7 Days
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Prior 7 Days", ["Metric", "This Year", "Last Year", "YoY Δ %"], [
            ["Gross Sales", period_last7["gross_sales"], period_last7_ly["gross_sales"], _yoy_pct(period_last7["gross_sales"], period_last7_ly["gross_sales"])],
            ["Net Sales", period_last7["net_sales"], period_last7_ly["net_sales"], _yoy_pct(period_last7["net_sales"], period_last7_ly["net_sales"])],
            ["Ad Spend", period_last7["spend"], period_last7_ly["spend"], _yoy_pct(period_last7["spend"], period_last7_ly["spend"])],
        ])
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Prior 7 Days Ratios", ["Metric", "This Year", "Last Year", "YoY Δ %"], [
            ["True ROAS", period_last7["roas"], period_last7_ly["roas"], yoy_deltas["last7_roas_pct"]],
            ["ROI", period_last7["roi"], period_last7_ly["roi"], yoy_deltas["last7_roi_pct"]],
        ])

        # Tabs 3-4: MTD
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "MTD", ["Metric", "This Year", "Last Year", "YoY Δ %"], [
            ["Gross Sales", period_mtd["gross_sales"], period_mtd_ly["gross_sales"], _yoy_pct(period_mtd["gross_sales"], period_mtd_ly["gross_sales"])],
            ["Net Sales", period_mtd["net_sales"], period_mtd_ly["net_sales"], _yoy_pct(period_mtd["net_sales"], period_mtd_ly["net_sales"])],
            ["Ad Spend", period_mtd["spend"], period_mtd_ly["spend"], _yoy_pct(period_mtd["spend"], period_mtd_ly["spend"])],
        ])
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "MTD Ratios", ["Metric", "This Year", "Last Year", "YoY Δ %"], [
            ["True ROAS", period_mtd["roas"], period_mtd_ly["roas"], yoy_deltas["mtd_roas_pct"]],
            ["ROI", period_mtd["roi"], period_mtd_ly["roi"], yoy_deltas["mtd_roi_pct"]],
        ])

        # Tabs 5-7: Financial Plan — MTD Attainment
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Plan - Net Income", ["Metric", plan["month"]], [
            ["Prorated Target", plan["net_income_prorated_target"]],
            ["Actual MTD", plan["net_income_actual_mtd"]],
            ["Trending", plan["net_income_projected"]],
            ["Full Month Target", plan["net_income_target"]],
        ])
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Plan - Gross Sales", ["Metric", plan["month"]], [
            ["Prorated Target", plan["gross_sales_prorated_target"]],
            ["Actual MTD", plan["gross_sales_actual_mtd"]],
            ["Trending", plan["gross_sales_projected"]],
            ["Full Month Target", plan["gross_sales_target"]],
        ])
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Plan - Ad Spend", ["Metric", plan["month"]], [
            ["Prorated Budget", plan["spend_prorated_budget"]],
            ["Actual MTD", plan["spend_actual_mtd"]],
            ["Trending", plan["spend_projected"]],
            ["Full Month Budget", plan["spend_budget"]],
        ])

        # Tab 8: Monthly Hits & Misses (+ YTD row + Totals/Full-Year row) — status
        # labels replicate dashboard_template.html's incomePill/spendPill logic exactly.
        monthly_header = [
            "Month", "Net Income Target", "Net Income Actual", "Net Income Projected", "Δ (Net)", "Net Result",
            "Gross Sales Target", "Gross Sales Actual", "Gross Sales Projected", "Δ (Gross)",
            "Ad Spend Budget", "Ad Spend Actual", "Spend Result", "True ROAS",
        ]
        monthly_rows = []
        for m in monthly_history:
            if m["is_current"] and m["income_projected"] is not None:
                income_result = "ON TRACK" if m["income_projected"] >= m["income_target"] else "AT RISK"
            elif m["income_actual"] is None:
                income_result = "—"
            else:
                income_result = "HIT" if m["income_actual"] >= m["income_target"] else "MISS"

            if m["is_current"] and m["spend_projected"] is not None:
                spend_result = "ON TRACK" if m["spend_projected"] <= m["spend_budget"] else "OVER PACE"
            elif m["spend_actual"] is None:
                spend_result = "—"
            else:
                spend_result = "ON BUDGET" if m["spend_actual"] <= m["spend_budget"] else "OVER"

            monthly_roas = (m["gross_sales_actual"] / m["spend_actual"]) if (m["gross_sales_actual"] is not None and m["spend_actual"]) else None

            monthly_rows.append([
                m["month"], m["income_target"], m["income_actual"], m["income_delta_basis"], m["income_delta"], income_result,
                m["gross_sales_target"], m["gross_sales_actual"], m["gross_sales_delta_basis"], m["gross_sales_delta"],
                m["spend_budget"], m["spend_actual"], spend_result,
                round(monthly_roas, 3) if monthly_roas is not None else None,
            ])
        # YTD/Totals rows already represent a fully realized (summed) figure, so the
        # "Projected" column is just a passthrough of the actual — nothing left to extrapolate.
        monthly_rows.append([
            ytd["label"], ytd["income_target"], ytd["income_actual"], ytd["income_actual"], ytd["income_delta"], ytd["income_status"].upper(),
            ytd["gross_sales_target"], ytd["gross_sales_actual"], ytd["gross_sales_actual"], ytd["gross_sales_delta"],
            ytd["spend_budget"], ytd["spend_actual"], ytd["spend_status"].upper(),
            ytd["roas"],
        ])
        monthly_rows.append([
            "Total (Full Year)", totals["income_target"], totals["income_actual"], totals["income_actual"], totals["income_delta"], totals["income_status"].upper(),
            totals["gross_sales_target"], totals["gross_sales_actual"], totals["gross_sales_actual"], totals["gross_sales_delta"],
            totals["spend_budget"], totals["spend_actual"], totals["spend_status"].upper(),
            round(totals["roas"], 3) if totals["roas"] is not None else None,
        ])
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Monthly Hits & Misses", monthly_header, monthly_rows)

        # Tab 9: MoM True ROAS Trend (the same series behind the new line chart)
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "MoM ROAS Trend", ["Month", "True ROAS", "Daily Ad Spend Pace"], [
            [roas_trend["months"][i], roas_trend["roas"][i], roas_trend["daily_spend"][i]]
            for i in range(len(roas_trend["months"]))
        ])

        # Tab 10: Full-Year Trend — Prior Year Total vs. Projected
        def _yoy_delta_val(projected, ly_total):
            return round(projected - ly_total, 2) if (projected is not None and ly_total is not None) else None
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Trend - PY vs Projected", ["Metric", "Prior Year Total", "Projected This Year", "Δ vs. Last Year", "% vs. Last Year"], [
            ["Net Income", trend["annual_income_ly_total"], trend["projected_annual_income"],
             _yoy_delta_val(trend["projected_annual_income"], trend["annual_income_ly_total"]),
             _yoy_pct(trend["projected_annual_income"], trend["annual_income_ly_total"])],
            ["Gross Sales", trend["annual_gross_sales_ly_total"], trend["projected_annual_gross_sales"],
             _yoy_delta_val(trend["projected_annual_gross_sales"], trend["annual_gross_sales_ly_total"]),
             _yoy_pct(trend["projected_annual_gross_sales"], trend["annual_gross_sales_ly_total"])],
            ["Ad Spend", trend["annual_spend_ly_total"], trend["projected_annual_spend"],
             _yoy_delta_val(trend["projected_annual_spend"], trend["annual_spend_ly_total"]),
             _yoy_pct(trend["projected_annual_spend"], trend["annual_spend_ly_total"])],
        ])

        # Tabs 11-12: Trend detail — the actual cumulative series behind the two line charts
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Trend Detail - Income", ["Month", "Cumulative Target", "Cumulative Actual", "Cumulative Projected", "Cumulative LY Actual"], [
            [trend["months"][i], trend["cum_target_income"][i], trend["cum_actual_income"][i],
             trend["cum_projected_income"][i], trend["cum_actual_income_ly"][i]]
            for i in range(len(trend["months"]))
        ])
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Trend Detail - Spend", ["Month", "Cumulative Budget", "Cumulative Actual", "Cumulative Projected", "Cumulative LY Actual"], [
            [trend["months"][i], trend["cum_target_spend"][i], trend["cum_actual_spend"][i],
             trend["cum_projected_spend"][i], trend["cum_actual_spend_ly"][i]]
            for i in range(len(trend["months"]))
        ])

        # Tab 13: Scaling Opportunities
        scaling_rows = [[
            "Current (7-day avg)", None, scaling["current_daily_spend"], None,
            scaling["current_daily_net"], round(scaling["current_daily_spend"] * 30, 2) if scaling["current_daily_spend"] is not None else None,
        ]]
        for t in scaling["targets"]:
            scaling_rows.append([
                t["label"], t["daily_revenue_target"], t["required_daily_spend"], t["delta_spend_vs_current"],
                t["implied_net_sales"], t["monthly_spend_equivalent"],
            ])
        _write_sheet_table(gc, GOOGLE_SHEET_ID, "Scaling Opportunities", ["Scenario", "Daily Revenue Target", "Required Daily Spend", "Δ Spend vs. Current", "Implied Daily Net Sales", "Monthly Spend Equivalent"], scaling_rows)

        print("Google Sheets export complete (13 tabs written).")
    except Exception as e:
        print(f"Google Sheets export failed (non-fatal, rest of the run is unaffected): {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Email the PDF to stakeholders via SMTP (stdlib only — no new dependency).
# Skipped entirely if the required env vars aren't all set, or if PDF
# generation above failed — everything else in the run is unaffected.
# ─────────────────────────────────────────────────────────────────────────────
EMAIL_SMTP_HOST = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com").strip()
EMAIL_SMTP_PORT = int((os.getenv("EMAIL_SMTP_PORT", "587") or "587").strip())
EMAIL_FROM_ADDRESS = os.getenv("EMAIL_FROM_ADDRESS", "").strip()
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").strip()
EMAIL_RECIPIENTS = [addr.strip() for addr in os.getenv("EMAIL_RECIPIENTS", "").split(",") if addr.strip()]
REPORT_LIVE_URL = os.getenv("REPORT_LIVE_URL", "").strip()

if not pdf_generated:
    print("Skipping email — no PDF was generated this run.")
elif not (EMAIL_FROM_ADDRESS and EMAIL_APP_PASSWORD and EMAIL_RECIPIENTS):
    print("EMAIL_FROM_ADDRESS / EMAIL_APP_PASSWORD / EMAIL_RECIPIENTS not fully set — skipping email.")
else:
    try:
        import smtplib
        from email import encoders
        from email.mime.base import MIMEBase
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        p7 = period_last7
        subject = f"NYH Weekly KPI Report — {plan['month']} (as of {TODAY.isoformat()})"
        roas_line = f"True ROAS (7d): {p7['roas']:.2f}x   |   ROI (7d): {p7['roi']:.2f}x" if p7["roas"] is not None else "True ROAS/ROI (7d): n/a"
        body_lines = [
            f"Weekly ecommerce KPI report — {report_data['last7_range']} vs. same week last year.",
            "",
            roas_line,
            f"Net Income MTD: ${plan['net_income_actual_mtd']:,.0f} vs. target ${plan['net_income_target']:,.0f}",
            f"Gross Sales MTD: ${plan['gross_sales_actual_mtd']:,.0f} vs. target ${plan['gross_sales_target']:,.0f}",
            f"Ad Spend MTD: ${plan['spend_actual_mtd']:,.0f} vs. budget ${plan['spend_budget']:,.0f}",
            "",
            "Full dashboard attached as PDF.",
        ]
        if REPORT_LIVE_URL:
            body_lines.append(f"Live interactive version: {REPORT_LIVE_URL}")

        msg = MIMEMultipart()
        msg["From"] = EMAIL_FROM_ADDRESS
        msg["To"] = ", ".join(EMAIL_RECIPIENTS)
        msg["Subject"] = subject
        msg.attach(MIMEText("\n".join(body_lines), "plain"))

        with open(pdf_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{pdf_path.name}"')
        msg.attach(part)

        with smtplib.SMTP(EMAIL_SMTP_HOST, EMAIL_SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_FROM_ADDRESS, EMAIL_APP_PASSWORD)
            server.sendmail(EMAIL_FROM_ADDRESS, EMAIL_RECIPIENTS, msg.as_string())

        print(f"Emailed PDF report to: {', '.join(EMAIL_RECIPIENTS)}")
    except Exception as e:
        print(f"Email send failed (non-fatal, rest of the run is unaffected): {e}")

print("Done.")
