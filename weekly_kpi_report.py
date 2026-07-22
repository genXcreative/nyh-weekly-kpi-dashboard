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
# ─────────────────────────────────────────────────────────────────────────────
PACIFIC = ZoneInfo("America/Los_Angeles")
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
        "income_delta": round(income_delta, 2) if income_delta is not None else None,
        "gross_sales_target": GROSS_SALES_TARGET[label],
        "gross_sales_actual": gross_actual,
        "gross_sales_projected": gross_projected,
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
                f"the unused budget headroom allows opportunities for discussion.")
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
}

print("Report data built.")

# ─────────────────────────────────────────────────────────────────────────────
# Cell 9 equivalent — render HTML dashboard
# ─────────────────────────────────────────────────────────────────────────────
template_html = (SCRIPT_DIR / "dashboard_template.html").read_text(encoding="utf-8")
html_out = template_html.replace("__REPORT_DATA_JSON__", json.dumps(report_data))
print(f"Dashboard rendered ({len(html_out):,} characters).")

# ─────────────────────────────────────────────────────────────────────────────
# Write outputs — index.html at repo root (served by GitHub Pages) + a dated
# JSON snapshot under data/ (kept in git history for auditing). The workflow
# file commits and pushes these; there is no Drive step in this version.
# ─────────────────────────────────────────────────────────────────────────────
(SCRIPT_DIR / "index.html").write_text(html_out, encoding="utf-8")

data_dir = SCRIPT_DIR / "data"
data_dir.mkdir(exist_ok=True)
ts = TODAY.strftime("%Y%m%d")
(data_dir / f"weekly_kpi_data_{ts}.json").write_text(json.dumps(report_data, indent=2), encoding="utf-8")

print(f"Wrote {SCRIPT_DIR / 'index.html'}")
print(f"Wrote {data_dir / f'weekly_kpi_data_{ts}.json'}")
print("Done.")
