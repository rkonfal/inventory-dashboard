#!/usr/bin/env python3
"""Fetch Google Ads overview for reporting-v2 using REST API."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_LOCAL = ROOT / '.env.local'
OUTPUT = ROOT / 'data' / 'current' / 'google_ads_overview.json'
API_VERSION = 'v23'
BASE_URL = f'https://googleads.googleapis.com/{API_VERSION}'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
EUR_TO_CZK = 27.27


def load_env_local(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


def env_value(key: str, default: str = '') -> str:
    return (os.environ.get(key) or default).strip()


def post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    req = Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', **(headers or {})},
        method='POST',
    )
    with urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode('utf-8'))


def post_form(url: str, payload: dict) -> dict:
    req = Request(
        url,
        data=urlencode(payload).encode('utf-8'),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    with urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode('utf-8'))


def google_headers(access_token: str) -> dict:
    return {
        'Authorization': f'Bearer {access_token}',
        'developer-token': env_value('GOOGLE_ADS_DEVELOPER_TOKEN'),
        'login-customer-id': env_value('GOOGLE_ADS_LOGIN_CUSTOMER_ID'),
    }


def fetch_access_token() -> str:
    payload = post_form(TOKEN_URL, {
        'client_id': env_value('GOOGLE_ADS_OAUTH_CLIENT_ID'),
        'client_secret': env_value('GOOGLE_ADS_OAUTH_CLIENT_SECRET'),
        'refresh_token': env_value('GOOGLE_ADS_REFRESH_TOKEN'),
        'grant_type': 'refresh_token',
    })
    token = payload.get('access_token')
    if not token:
        raise SystemExit(f'Failed to refresh access token: {payload}')
    return token


def search_stream(customer_id: str, query: str, access_token: str) -> list[dict]:
    url = f'{BASE_URL}/customers/{customer_id}/googleAds:searchStream'
    payload = post_json(url, {'query': query}, headers=google_headers(access_token))
    rows = []
    for block in payload:
        rows.extend(block.get('results') or [])
    return rows


def current_month_window() -> tuple[str, str]:
    today = date.today()
    since = today.replace(day=1)
    until = today - timedelta(days=1)
    if until < since:
        until = today
    return since.isoformat(), until.isoformat()


def previous_month_window() -> tuple[str, str]:
    first_this_month = date.today().replace(day=1)
    until = first_this_month - timedelta(days=1)
    since = until.replace(day=1)
    return since.isoformat(), until.isoformat()


def fetch_window_payload(accounts: list[dict], access_token: str, since: str, until: str, label: str) -> dict:
    all_daily = []
    all_campaigns = []
    account_payloads = []

    for account in accounts:
        daily = fetch_account_daily(account['id'], access_token, since, until)
        campaigns = fetch_campaigns(account['id'], access_token, since, until)
        spend_czk = round(sum(float(row['spendCzk']) for row in daily), 2)
        conversion_value_czk = round(sum(float(row['conversionValueCzk']) for row in daily), 2)
        payload = {
            **account,
            label: {
                'dateFrom': since,
                'dateTo': until,
                'spend': round(sum(float(row['spend']) for row in daily), 2),
                'spendCzk': spend_czk,
                'impressions': sum(int(row['impressions']) for row in daily),
                'clicks': sum(int(row['clicks']) for row in daily),
                'conversions': round(sum(float(row['conversions']) for row in daily), 2),
                'conversionValue': round(sum(float(row['conversionValue']) for row in daily), 2),
                'conversionValueCzk': conversion_value_czk,
                'roas': round(conversion_value_czk / spend_czk, 2) if spend_czk else None,
            },
            'daily': daily,
        }
        account_payloads.append(payload)
        all_daily.extend(daily)
        all_campaigns.extend([
            {
                **row,
                'roas': round(float(row['conversionValueCzk']) / float(row['spendCzk']), 2) if float(row['spendCzk']) else None,
            }
            for row in campaigns
        ])

    summary_daily = defaultdict(lambda: {
        'date': '',
        'spendCzk': 0.0,
        'impressions': 0,
        'clicks': 0,
        'conversions': 0.0,
        'conversionValueCzk': 0.0,
    })
    for row in all_daily:
        bucket = summary_daily[row['date']]
        bucket['date'] = row['date']
        bucket['spendCzk'] += float(row['spendCzk'])
        bucket['impressions'] += int(row['impressions'])
        bucket['clicks'] += int(row['clicks'])
        bucket['conversions'] += float(row['conversions'])
        bucket['conversionValueCzk'] += float(row['conversionValueCzk'])

    summary = {
        'dateFrom': since,
        'dateTo': until,
        'spendCzk': round(sum(float(row['spendCzk']) for row in all_daily), 2),
        'impressions': sum(int(row['impressions']) for row in all_daily),
        'clicks': sum(int(row['clicks']) for row in all_daily),
        'conversions': round(sum(float(row['conversions']) for row in all_daily), 2),
        'conversionValueCzk': round(sum(float(row['conversionValueCzk']) for row in all_daily), 2),
    }
    summary['roas'] = round(summary['conversionValueCzk'] / summary['spendCzk'], 2) if summary['spendCzk'] else None

    return {
        label: summary,
        f'accounts{label[0].upper()}{label[1:]}': account_payloads,
        f'dailySummary{label[0].upper()}{label[1:]}': [
            {
                **row,
                'spendCzk': round(float(row['spendCzk']), 2),
                'conversions': round(float(row['conversions']), 2),
                'conversionValueCzk': round(float(row['conversionValueCzk']), 2),
                'roas': round(float(row['conversionValueCzk']) / float(row['spendCzk']), 2) if float(row['spendCzk']) else None,
            }
            for _, row in sorted(summary_daily.items())
        ],
        f'campaigns{label[0].upper()}{label[1:]}': all_campaigns,
        f'topCampaigns{label[0].upper()}{label[1:]}': sorted(all_campaigns, key=lambda row: float(row.get('spendCzk') or 0), reverse=True)[:15],
    }


def micros_to_amount(value) -> float:
    return round(float(value or 0) / 1_000_000, 2)


def to_czk(amount: float, currency: str) -> float:
    currency = (currency or '').upper()
    if currency == 'EUR':
        return round(amount * EUR_TO_CZK, 2)
    return round(amount, 2)


def fetch_child_accounts(access_token: str) -> list[dict]:
    manager_id = env_value('GOOGLE_ADS_LOGIN_CUSTOMER_ID')
    query = """
        SELECT
          customer_client.id,
          customer_client.descriptive_name,
          customer_client.currency_code,
          customer_client.level,
          customer_client.manager,
          customer_client.status
        FROM customer_client
        WHERE customer_client.status = 'ENABLED'
          AND customer_client.level <= 1
    """
    rows = search_stream(manager_id, query, access_token)
    accounts = []
    for row in rows:
        cc = row.get('customerClient') or {}
        if cc.get('manager'):
            continue
        customer_id = str(cc.get('id') or '').replace('-', '')
        if not customer_id:
            continue
        accounts.append({
            'id': customer_id,
            'name': cc.get('descriptiveName') or customer_id,
            'currency': cc.get('currencyCode') or 'CZK',
        })
    return accounts


def fetch_account_daily(customer_id: str, access_token: str, since: str, until: str) -> list[dict]:
    query = f"""
        SELECT
          customer.id,
          customer.descriptive_name,
          customer.currency_code,
          segments.date,
          metrics.cost_micros,
          metrics.impressions,
          metrics.clicks,
          metrics.conversions,
          metrics.conversions_value
        FROM customer
        WHERE segments.date BETWEEN '{since}' AND '{until}'
    """
    rows = search_stream(customer_id, query, access_token)
    daily = []
    for row in rows:
        customer = row.get('customer') or {}
        metrics = row.get('metrics') or {}
        segments = row.get('segments') or {}
        currency = customer.get('currencyCode') or 'CZK'
        spend = micros_to_amount(metrics.get('costMicros'))
        purchase_value = round(float(metrics.get('conversionsValue') or 0), 2)
        daily.append({
            'date': segments.get('date'),
            'accountId': customer.get('id') or customer_id,
            'accountName': customer.get('descriptiveName') or customer_id,
            'currency': currency,
            'spend': spend,
            'spendCzk': to_czk(spend, currency),
            'impressions': int(metrics.get('impressions') or 0),
            'clicks': int(metrics.get('clicks') or 0),
            'conversions': round(float(metrics.get('conversions') or 0), 2),
            'conversionValue': purchase_value,
            'conversionValueCzk': to_czk(purchase_value, currency),
        })
    return daily


def fetch_campaigns(customer_id: str, access_token: str, since: str, until: str) -> list[dict]:
    query = f"""
        SELECT
          customer.id,
          customer.descriptive_name,
          customer.currency_code,
          campaign.id,
          campaign.name,
          campaign.status,
          metrics.cost_micros,
          metrics.impressions,
          metrics.clicks,
          metrics.conversions,
          metrics.conversions_value
        FROM campaign
        WHERE segments.date BETWEEN '{since}' AND '{until}'
    """
    rows = search_stream(customer_id, query, access_token)
    campaigns = []
    for row in rows:
        customer = row.get('customer') or {}
        campaign = row.get('campaign') or {}
        metrics = row.get('metrics') or {}
        currency = customer.get('currencyCode') or 'CZK'
        spend = micros_to_amount(metrics.get('costMicros'))
        conversion_value = round(float(metrics.get('conversionsValue') or 0), 2)
        campaigns.append({
            'accountId': customer.get('id') or customer_id,
            'accountName': customer.get('descriptiveName') or customer_id,
            'currency': currency,
            'campaignId': campaign.get('id') or '',
            'campaignName': campaign.get('name') or 'Bez názvu',
            'status': campaign.get('status'),
            'spend': spend,
            'spendCzk': to_czk(spend, currency),
            'impressions': int(metrics.get('impressions') or 0),
            'clicks': int(metrics.get('clicks') or 0),
            'conversions': round(float(metrics.get('conversions') or 0), 2),
            'conversionValue': conversion_value,
            'conversionValueCzk': to_czk(conversion_value, currency),
        })
    return campaigns


def main() -> int:
    load_env_local(ENV_LOCAL)
    required = [
        'GOOGLE_ADS_DEVELOPER_TOKEN',
        'GOOGLE_ADS_LOGIN_CUSTOMER_ID',
        'GOOGLE_ADS_OAUTH_CLIENT_ID',
        'GOOGLE_ADS_OAUTH_CLIENT_SECRET',
        'GOOGLE_ADS_REFRESH_TOKEN',
    ]
    missing = [key for key in required if not env_value(key)]
    if missing:
        raise SystemExit('Missing required env keys: ' + ', '.join(missing))

    access_token = fetch_access_token()
    accounts = fetch_child_accounts(access_token)
    current_since, current_until = current_month_window()
    previous_since, previous_until = previous_month_window()
    current_payload = fetch_window_payload(accounts, access_token, current_since, current_until, 'currentMonth')
    previous_payload = fetch_window_payload(accounts, access_token, previous_since, previous_until, 'previousMonth')

    result = {
        'source': {
            'status': 'live_api',
            'platform': 'google_ads',
            'message': 'Google Ads overview fetched directly from Google Ads API.',
        },
        'window': {'dateFrom': current_since, 'dateTo': current_until},
        'summary': current_payload['currentMonth'],
        'accounts': current_payload['accountsCurrentMonth'],
        'dailySummary': current_payload['dailySummaryCurrentMonth'],
        'campaignsCurrentMonth': current_payload['campaignsCurrentMonth'],
        'topCampaignsCurrentMonth': current_payload['topCampaignsCurrentMonth'],
        'previousMonth': previous_payload['previousMonth'],
        'accountsPreviousMonth': previous_payload['accountsPreviousMonth'],
        'dailySummaryPreviousMonth': previous_payload['dailySummaryPreviousMonth'],
        'campaignsPreviousMonth': previous_payload['campaignsPreviousMonth'],
        'topCampaignsPreviousMonth': previous_payload['topCampaignsPreviousMonth'],
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {OUTPUT}')
    print(json.dumps({'summary': current_payload['currentMonth'], 'previousMonth': previous_payload['previousMonth'], 'accounts': len(current_payload['accountsCurrentMonth'])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
