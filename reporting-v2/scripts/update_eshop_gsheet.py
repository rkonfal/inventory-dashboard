#!/usr/bin/env python3
"""
update_eshop_gsheet.py

Fills the "Výsledky eshopu" Google Sheet with daily e-shop data
from the reporting pipeline. Called as part of the morning report.

Usage:
    python3 scripts/update_eshop_gsheet.py [--date YYYY-MM-DD] [--dry-run]

Writes previous day's data (or --date) to the correct row in the
correct monthly sheet tab.
"""

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SPREADSHEET_ID = "1S7M9yuB4shhGhmuNSBMouDZIWSGlVgYNQuUg0xr9kVc"
ROOT = Path(__file__).parent.parent

# Month tab mapping (Czech month names)
MONTH_TABS = {
    1: "Leden {year}",
    2: "Únor {year}",
    3: "Březen {year}",
    4: "Duben {year}",
    5: "Květen {year}",
    6: "Červen{year}",   # no space (as seen in sheet)
    7: "Červenec {year}",
    8: "Srpen {year}.",   # has period (as seen in sheet)
    9: "Září {year}",
    10: "Říjen {year}",
    11: "Listopad {year}",
    12: "Prosinec {year}",
}


def b64url(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def get_access_token(sa_path: Path) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    sa = json.loads(sa_path.read_text())
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}))
    now = int(time.time())
    payload = b64url(
        json.dumps(
            {
                "iss": sa["client_email"],
                "scope": "https://www.googleapis.com/auth/spreadsheets",
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now,
                "exp": now + 3600,
            }
        )
    )
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    jwt = f"{header}.{payload}.{b64url(sig)}"
    data = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": jwt,
        }
    ).encode()
    with urllib.request.urlopen(
        urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
    ) as r:
        return json.loads(r.read())["access_token"]


def sheets_get(token: str, sheet_id: str, range_name: str, unformatted: bool = False):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{urllib.parse.quote(range_name)}"
    if unformatted:
        url += "?valueRenderOption=UNFORMATTED_VALUE"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def sheets_put(token: str, sheet_id: str, range_name: str, values: list, dry_run: bool = False):
    if dry_run:
        print(f"[DRY-RUN] Would write to {range_name}:")
        for row in values:
            print(f"  {row}")
        return
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{urllib.parse.quote(range_name)}?valueInputOption=USER_ENTERED"
    body = {"range": range_name, "majorDimension": "ROWS", "values": values}
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read())
            return result
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Sheets API error {e.code}: {e.read().decode()[:300]}")


def fmt_czk(val):
    """Format as Czech Kč string (matching existing sheet format)."""
    if val == 0:
        return 0
    whole = int(val)
    cents = round((val - whole) * 100)
    whole_str = f"{whole:,}".replace(",", " ")
    return f"{whole_str},{cents:02d} Kč "


def get_daily_orders(report_date: str):
    """
    Parse order_fact_ytd_window.json and return (cz_count, sk_count, de_count,
    cz_revenue, sk_revenue, de_revenue) for the given date.
    Counts ALL orders including cancelled (matching spreadsheet convention).
    """
    path = ROOT / "data" / "current" / "order_fact_ytd_window.json"
    if not path.exists():
        print(f"WARN: {path} not found", file=sys.stderr)
        return None

    data = json.loads(path.read_text())
    orders = data.get("orders", [])

    by_country = defaultdict(lambda: {"count": 0, "revenue": 0.0})
    for o in orders:
        d = o.get("dateCreated", "")[:10]
        if d != report_date:
            continue
        country = o.get("countryCode", "CZ")
        rev = o.get("revenueWithVat", 0) or 0
        by_country[country]["count"] += 1
        by_country[country]["revenue"] += rev

    cz = by_country.get("CZ", {"count": 0, "revenue": 0})
    sk = by_country.get("SK", {"count": 0, "revenue": 0})
    de = by_country.get("DE", {"count": 0, "revenue": 0})
    return (
        cz["count"], sk["count"], de["count"],
        cz["revenue"], sk["revenue"], de["revenue"],
    )


def get_shipments_for_date(report_date: str):
    """
    Returns (cz_packages, sk_packages) for the given date.
    Falls back to 0,0 for weekends or when data is unavailable.
    Checks morning_report_previous_day.json for yesterday's logistics.
    """
    import datetime as dt_module

    d = dt_module.date.fromisoformat(report_date)
    # Weekends: no warehouse dispatch
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return 0, 0

    # Check if it's yesterday's date (morning report always has yesterday's logistics)
    yesterday = (dt_module.date.today() - timedelta(days=1)).isoformat()
    if report_date == yesterday:
        path = ROOT / "data" / "current" / "morning_report_previous_day.json"
        if path.exists():
            rpt = json.loads(path.read_text())
            logistics = rpt.get("logistics", {})
            by_account = logistics.get("byAccount", {})
            cz = by_account.get("CZ", 0)
            sk = by_account.get("SK", 0)
            return cz, sk

    # Try 4PX outbound files for other dates
    from datetime import datetime as dt_cls
    cz_total, sk_total = 0, 0

    for fname, key in [("4px_cz_outbound_recent.json", "cz"), ("4px_sk_outbound_recent.json", "sk")]:
        fpath = ROOT / "data" / "current" / fname
        if not fpath.exists():
            continue
        data = json.loads(fpath.read_text())
        items = data.get("items", [])
        count = 0
        for item in items:
            ts = item.get("create_time", 0)
            if ts:
                item_date = dt_cls.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
                if item_date == report_date:
                    count += 1
        if key == "cz":
            cz_total = count
        else:
            sk_total = count

    return cz_total, sk_total


def get_tab_name(report_date: str) -> str:
    d = date.fromisoformat(report_date)
    tmpl = MONTH_TABS.get(d.month, "")
    if not tmpl:
        raise ValueError(f"No tab mapping for month {d.month}")
    # Handle special case: June has no space before year
    if d.month == 6:
        return f"Červen{d.year}"
    return tmpl.format(year=d.year)


def get_sheet_row(report_date: str) -> int:
    """Row 1=header, row 2=day 1, so row = day_of_month + 1."""
    return date.fromisoformat(report_date).day + 1


def main():
    parser = argparse.ArgumentParser(description="Fill Výsledky eshopu GSheet")
    parser.add_argument("--date", help="Date YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    args = parser.parse_args()

    report_date = args.date or (date.today() - timedelta(days=1)).isoformat()
    print(f"Updating sheet for date: {report_date}")

    # Get order data
    order_data = get_daily_orders(report_date)
    if order_data is None:
        print("ERROR: Could not load order data", file=sys.stderr)
        sys.exit(1)

    cz_orders, sk_orders, de_orders, cz_rev, sk_rev, de_rev = order_data
    cz_pkg, sk_pkg = get_shipments_for_date(report_date)

    print(f"Orders: CZ={cz_orders} SK={sk_orders} DE={de_orders}")
    print(f"Revenue: CZ={cz_rev:.2f} SK={sk_rev:.2f} DE={de_rev:.2f}")
    print(f"Packages: CZ={cz_pkg} SK={sk_pkg}")

    tab = get_tab_name(report_date)
    row = get_sheet_row(report_date)
    range_name = f"'{tab}'!C{row}:J{row}"
    print(f"Target: {tab} row {row} → range {range_name}")

    values = [[
        cz_orders,
        sk_orders,
        de_orders,
        fmt_czk(cz_rev),
        fmt_czk(sk_rev),
        fmt_czk(de_rev),
        cz_pkg if cz_pkg else "",   # blank when unavailable
        sk_pkg,
    ]]

    sa_path = ROOT / "secrets" / "ga4-service-account.json"
    if not sa_path.exists():
        print(f"ERROR: Service account not found at {sa_path}", file=sys.stderr)
        sys.exit(1)

    try:
        token = get_access_token(sa_path)
    except Exception as e:
        print(f"ERROR: Could not get access token: {e}", file=sys.stderr)
        sys.exit(1)

    result = sheets_put(token, SPREADSHEET_ID, range_name, values, dry_run=args.dry_run)
    if not args.dry_run:
        updated = result.get("updatedCells", 0) if result else 0
        print(f"✓ Updated {updated} cells in range {result.get('updatedRange', range_name)}")


if __name__ == "__main__":
    main()
