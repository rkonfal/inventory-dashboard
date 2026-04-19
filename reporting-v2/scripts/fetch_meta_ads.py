#!/usr/bin/env python3
"""Fetch Meta Ads account and campaign overview for reporting-v2."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_LOCAL = ROOT / '.env.local'
OUTPUT = ROOT / 'data' / 'current' / 'meta_ads_overview.json'
BASE_URL = 'https://graph.facebook.com/v22.0'
EUR_TO_CZK = 27.27
PURCHASE_ACTION_TYPES = {
    'purchase',
    'omni_purchase',
    'onsite_web_purchase',
    'offsite_conversion.fb_pixel_purchase',
}


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


def parse_account_ids(raw: str) -> List[str]:
    values = []
    for part in (raw or '').split(','):
        item = part.strip()
        if not item:
            continue
        if not item.startswith('act_'):
            item = f'act_{item}'
        values.append(item)
    return values


def graph_json(path: str, **params):
    token = env_value('META_ACCESS_TOKEN')
    if not token:
        raise SystemExit('Missing META_ACCESS_TOKEN')
    params['access_token'] = token
    url = f"{BASE_URL}/{path}?{urlencode(params)}"
    with urlopen(url, timeout=45) as response:
        return json.loads(response.read().decode('utf-8'))


def paginate(path: str, **params):
    cursor = None
    while True:
        query = dict(params)
        if cursor:
            query['after'] = cursor
        payload = graph_json(path, **query)
        for row in payload.get('data') or []:
            yield row
        cursor = ((payload.get('paging') or {}).get('cursors') or {}).get('after')
        if not cursor:
            break


def money(value) -> float:
    return round(float(value or 0), 2)


def as_int(value) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def extract_purchase_metric(rows, money_mode=False) -> float:
    total = 0.0
    for row in rows or []:
        if (row.get('action_type') or '') in PURCHASE_ACTION_TYPES:
            total += float(row.get('value') or 0)
    return round(total, 2 if money_mode else 4)


def to_czk(amount: float, currency: str) -> float:
    currency = (currency or '').upper()
    if currency == 'EUR':
        return round(amount * EUR_TO_CZK, 2)
    return round(amount, 2)


def current_month_window() -> tuple[str, str]:
    today = date.today()
    since = today.replace(day=1)
    until = today - timedelta(days=1)
    if until < since:
        until = today
    return since.isoformat(), until.isoformat()


def fetch_account_meta(account_id: str) -> Dict[str, object]:
    payload = graph_json(account_id, fields='id,account_id,name,currency')
    return {
        'id': f"act_{payload.get('account_id') or payload.get('id')}",
        'accountId': str(payload.get('account_id') or payload.get('id') or ''),
        'name': payload.get('name') or account_id,
        'currency': payload.get('currency') or 'CZK',
    }


def fetch_account_daily(account_id: str, since: str, until: str) -> List[Dict[str, object]]:
    rows = list(paginate(
        f'{account_id}/insights',
        fields='account_id,account_name,date_start,date_stop,spend,impressions,clicks,actions,action_values',
        level='account',
        time_increment='1',
        time_range=json.dumps({'since': since, 'until': until}),
        limit='100',
    ))
    daily = []
    for row in rows:
        currency = None
        daily.append({
            'date': row.get('date_start'),
            'accountId': f"act_{row.get('account_id')}",
            'accountName': row.get('account_name') or account_id,
            'spend': money(row.get('spend')),
            'impressions': as_int(row.get('impressions')),
            'clicks': as_int(row.get('clicks')),
            'purchaseConversions': extract_purchase_metric(row.get('actions') or []),
            'purchaseValue': extract_purchase_metric(row.get('action_values') or [], money_mode=True),
        })
    return daily


def fetch_campaign_totals(account_id: str, since: str, until: str) -> List[Dict[str, object]]:
    rows = list(paginate(
        f'{account_id}/insights',
        fields='account_id,account_name,campaign_id,campaign_name,spend,impressions,clicks,actions,action_values',
        level='campaign',
        time_increment='all_days',
        time_range=json.dumps({'since': since, 'until': until}),
        limit='200',
    ))
    campaigns = []
    for row in rows:
        campaigns.append({
            'accountId': f"act_{row.get('account_id')}",
            'accountName': row.get('account_name') or account_id,
            'campaignId': row.get('campaign_id') or '',
            'campaignName': row.get('campaign_name') or 'Bez názvu',
            'spend': money(row.get('spend')),
            'impressions': as_int(row.get('impressions')),
            'clicks': as_int(row.get('clicks')),
            'purchaseConversions': extract_purchase_metric(row.get('actions') or []),
            'purchaseValue': extract_purchase_metric(row.get('action_values') or [], money_mode=True),
        })
    return campaigns


def fetch_campaign_statuses(account_id: str) -> Dict[str, Dict[str, object]]:
    rows = list(paginate(
        f'{account_id}/campaigns',
        fields='id,name,status,effective_status',
        limit='200',
    ))
    return {
        str(row.get('id') or ''): {
            'status': row.get('status'),
            'effectiveStatus': row.get('effective_status'),
        }
        for row in rows
        if row.get('id')
    }


def main() -> int:
    load_env_local(ENV_LOCAL)
    account_ids = parse_account_ids(env_value('META_AD_ACCOUNT_IDS'))
    if not account_ids:
        raise SystemExit('Missing META_AD_ACCOUNT_IDS')

    since, until = current_month_window()
    accounts = []
    daily_all = []
    campaigns_all = []

    for account_id in account_ids:
        meta = fetch_account_meta(account_id)
        currency = meta['currency']
        daily = fetch_account_daily(account_id, since, until)
        campaigns = fetch_campaign_totals(account_id, since, until)
        campaign_statuses = fetch_campaign_statuses(account_id)

        spend = round(sum(float(row['spend']) for row in daily), 2)
        clicks = sum(int(row['clicks']) for row in daily)
        impressions = sum(int(row['impressions']) for row in daily)
        purchase_conversions = round(sum(float(row['purchaseConversions']) for row in daily), 2)
        purchase_value = round(sum(float(row['purchaseValue']) for row in daily), 2)
        spend_czk = to_czk(spend, currency)
        purchase_value_czk = to_czk(purchase_value, currency)
        roas = round((purchase_value / spend), 2) if spend else None

        account_payload = {
            **meta,
            'currentMonth': {
                'dateFrom': since,
                'dateTo': until,
                'spend': spend,
                'spendCzk': spend_czk,
                'impressions': impressions,
                'clicks': clicks,
                'purchaseConversions': purchase_conversions,
                'purchaseValue': purchase_value,
                'purchaseValueCzk': purchase_value_czk,
                'roas': roas,
            },
            'daily': [
                {
                    **row,
                    'currency': currency,
                    'spendCzk': to_czk(float(row['spend']), currency),
                    'purchaseValueCzk': to_czk(float(row['purchaseValue']), currency),
                }
                for row in daily
            ],
        }
        accounts.append(account_payload)
        daily_all.extend(account_payload['daily'])
        campaigns_all.extend([
            {
                **row,
                'status': (campaign_statuses.get(str(row.get('campaignId'))) or {}).get('status'),
                'effectiveStatus': (campaign_statuses.get(str(row.get('campaignId'))) or {}).get('effectiveStatus'),
                'currency': currency,
                'spendCzk': to_czk(float(row['spend']), currency),
                'purchaseValueCzk': to_czk(float(row['purchaseValue']), currency),
                'roas': round((float(row['purchaseValue']) / float(row['spend'])), 2) if float(row['spend'] or 0) else None,
            }
            for row in campaigns
        ])

    summary_daily = defaultdict(lambda: {
        'date': '',
        'spendCzk': 0.0,
        'impressions': 0,
        'clicks': 0,
        'purchaseConversions': 0.0,
        'purchaseValueCzk': 0.0,
    })
    for row in daily_all:
        bucket = summary_daily[row['date']]
        bucket['date'] = row['date']
        bucket['spendCzk'] += float(row['spendCzk'])
        bucket['impressions'] += int(row['impressions'])
        bucket['clicks'] += int(row['clicks'])
        bucket['purchaseConversions'] += float(row['purchaseConversions'])
        bucket['purchaseValueCzk'] += float(row['purchaseValueCzk'])

    summary = {
        'dateFrom': since,
        'dateTo': until,
        'spendCzk': round(sum(float(row['spendCzk']) for row in daily_all), 2),
        'impressions': sum(int(row['impressions']) for row in daily_all),
        'clicks': sum(int(row['clicks']) for row in daily_all),
        'purchaseConversions': round(sum(float(row['purchaseConversions']) for row in daily_all), 2),
        'purchaseValueCzk': round(sum(float(row['purchaseValueCzk']) for row in daily_all), 2),
    }
    summary['roas'] = round(summary['purchaseValueCzk'] / summary['spendCzk'], 2) if summary['spendCzk'] else None

    campaigns_top = sorted(campaigns_all, key=lambda row: float(row.get('spendCzk') or 0), reverse=True)[:15]

    result = {
        'source': {
            'status': 'live_api',
            'platform': 'meta_ads',
            'message': 'Meta Ads overview fetched directly from Marketing API.',
        },
        'window': {'dateFrom': since, 'dateTo': until},
        'summary': summary,
        'accounts': accounts,
        'dailySummary': [
            {
                **row,
                'spendCzk': round(float(row['spendCzk']), 2),
                'purchaseConversions': round(float(row['purchaseConversions']), 2),
                'purchaseValueCzk': round(float(row['purchaseValueCzk']), 2),
                'roas': round(float(row['purchaseValueCzk']) / float(row['spendCzk']), 2) if float(row['spendCzk']) else None,
            }
            for _, row in sorted(summary_daily.items())
        ],
        'campaignsCurrentMonth': campaigns_all,
        'topCampaignsCurrentMonth': campaigns_top,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {OUTPUT}')
    print(json.dumps({'summary': summary, 'accounts': len(accounts), 'topCampaigns': len(campaigns_top)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
