#!/usr/bin/env python3
"""Fetch a basic Sklik account + campaign snapshot.

Current scope:
- authenticate by token
- fetch main account profile
- fetch accessible foreign accounts
- fetch campaign list with simple aggregate summary

This is the first live foothold for direct marketing-platform integration in
reporting-v2. Stats/reports can build on top of this next.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List
from xmlrpc.client import DateTime, ServerProxy

ROOT = Path(__file__).resolve().parents[1]
ENV_LOCAL = ROOT / '.env.local'
OUTPUT = ROOT / 'data' / 'current' / 'sklik_overview.json'
RPC_URL = 'https://api.sklik.cz/drak/RPC2'


def load_env_local(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


def sklik_token() -> str:
    load_env_local(ENV_LOCAL)
    token = (os.environ.get('SKLIK_API_TOKEN') or '').strip()
    if not token:
        raise SystemExit('Missing SKLIK_API_TOKEN')
    return token


def campaign_status_summary(rows: List[Dict[str, object]]) -> Dict[str, int]:
    return dict(Counter((row.get('status') or 'unknown') for row in rows))


def campaign_type_summary(rows: List[Dict[str, object]]) -> Dict[str, int]:
    return dict(Counter((row.get('type') or 'unknown') for row in rows))


def normalize(value):
    if isinstance(value, DateTime):
        return str(value)
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def xmlrpc_day(value: date) -> DateTime:
    return DateTime(value.strftime('%Y%m%dT00:00:00'))


def money_czk(value: int | float | None) -> float:
    return round(float(value or 0) / 100.0, 2)


def normalize_stat_row(row: Dict[str, object]) -> Dict[str, object]:
    return {
        'date': str(row.get('date') or ''),
        'impressions': int(row.get('impressions') or 0),
        'clicks': int(row.get('clicks') or 0),
        'ctr': float(row.get('ctr') or 0),
        'cpcCzk': money_czk(row.get('cpc')),
        'priceCzk': money_czk(row.get('price') or row.get('totalMoney')),
        'conversions': float(row.get('conversions') or 0),
        'conversionValueCzk': money_czk(row.get('conversionValue')),
        'transactions': int(row.get('transactions') or 0),
        'avgPosition': float(row.get('avgPosition') or row.get('avgPos') or 0),
    }


def main() -> int:
    proxy = ServerProxy(RPC_URL)
    login = proxy.client.loginByToken(sklik_token())
    if str(login.get('status')) != '200':
        raise SystemExit(f"Sklik login failed: {login.get('status')} {login.get('statusMessage')}")

    user = {'session': login['session']}
    client = proxy.client.get(user)
    if str(client.get('status')) != '200':
        raise SystemExit(f"Sklik client.get failed: {client.get('status')} {client.get('statusMessage')}")

    campaigns: List[Dict[str, object]] = []
    offset = 0
    page_size = 200
    columns = ['id', 'name', 'status', 'type', 'createDate', 'startDate', 'endDate']

    while True:
        page = proxy.campaigns.list(user, {}, {
            'offset': offset,
            'limit': page_size,
            'displayColumns': columns,
        })
        if str(page.get('status')) != '200':
            raise SystemExit(f"Sklik campaigns.list failed: {page.get('status')} {page.get('statusMessage')}")
        batch = page.get('campaigns') or []
        campaigns.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    today = date.today()
    date_from = today.replace(day=1)
    date_to = today - timedelta(days=1)

    stats_status = proxy.stats.status(user, {
        'dateFrom': xmlrpc_day(date_from),
        'dateTo': xmlrpc_day(date_to),
    })
    if str(stats_status.get('status')) != '200':
        raise SystemExit(f"Sklik stats.status failed: {stats_status.get('status')} {stats_status.get('statusMessage')}")

    client_total = proxy.client.stats(user, {
        'dateFrom': xmlrpc_day(date_from),
        'dateTo': xmlrpc_day(date_to),
        'granularity': 'total',
        'includeFulltext': True,
        'includeContext': True,
        'includeZbozi': True,
    })
    if str(client_total.get('status')) != '200':
        raise SystemExit(f"Sklik client.stats total failed: {client_total.get('status')} {client_total.get('statusMessage')}")

    client_daily = proxy.client.stats(user, {
        'dateFrom': xmlrpc_day(date_from),
        'dateTo': xmlrpc_day(date_to),
        'granularity': 'daily',
        'includeFulltext': True,
        'includeContext': True,
        'includeZbozi': True,
    })
    if str(client_daily.get('status')) != '200':
        raise SystemExit(f"Sklik client.stats daily failed: {client_daily.get('status')} {client_daily.get('statusMessage')}")

    campaign_report = proxy.campaigns.createReport(user, {
        'dateFrom': xmlrpc_day(date_from),
        'dateTo': xmlrpc_day(date_to),
        'isDeleted': False,
    }, {
        'statGranularity': 'total',
    })
    if str(campaign_report.get('status')) != '200':
        raise SystemExit(f"Sklik campaigns.createReport failed: {campaign_report.get('status')} {campaign_report.get('statusMessage')}")

    campaign_rows = []
    report_id = campaign_report.get('reportId')
    report_offset = 0
    report_limit = 200
    report_columns = ['id', 'name', 'status', 'type', 'clicks', 'impressions', 'totalMoney', 'conversions', 'conversionValue']
    while True:
        page = proxy.campaigns.readReport(user, report_id, {
            'offset': report_offset,
            'limit': report_limit,
            'displayColumns': report_columns,
            'allowEmptyStatistics': False,
        })
        if str(page.get('status')) != '200':
            raise SystemExit(f"Sklik campaigns.readReport failed: {page.get('status')} {page.get('statusMessage')}")
        batch = page.get('report') or []
        campaign_rows.extend(batch)
        if len(batch) < report_limit:
            break
        report_offset += report_limit

    result = normalize({
        'source': {
            'status': 'live_api',
            'platform': 'sklik',
            'message': 'Sklik account and campaign list fetched directly from API Drak.',
        },
        'account': {
            'userId': (client.get('user') or {}).get('userId'),
            'username': (client.get('user') or {}).get('username'),
            'agencyStatus': (client.get('user') or {}).get('agencyStatus'),
        },
        'foreignAccounts': [
            {
                'userId': row.get('userId'),
                'username': row.get('username'),
                'access': row.get('access'),
                'relationName': row.get('relationName'),
                'relationStatus': row.get('relationStatus'),
                'relationType': row.get('relationType'),
            }
            for row in (client.get('foreignAccounts') or [])
        ],
        'campaignSummary': {
            'count': len(campaigns),
            'byStatus': campaign_status_summary(campaigns),
            'byType': campaign_type_summary(campaigns),
        },
        'statsStatusCurrentMonth': [
            {
                'date': str(row.get('date') or ''),
                'status': row.get('status'),
            }
            for row in (stats_status.get('days') or [])
        ],
        'currentMonth': {
            'dateFrom': date_from.isoformat(),
            'dateTo': date_to.isoformat(),
            'total': normalize_stat_row(((client_total.get('report') or [{}])[0])),
            'daily': [normalize_stat_row(row) for row in (client_daily.get('report') or [])],
        },
        'campaignPerformanceCurrentMonth': [
            {
                'id': row.get('id'),
                'name': row.get('name'),
                'status': row.get('status'),
                'type': row.get('type'),
                **(normalize_stat_row((row.get('stats') or [{}])[0]))
            }
            for row in campaign_rows
            if row.get('stats')
        ],
        'campaigns': campaigns,
    })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {OUTPUT}')
    print(json.dumps({
        'account': result['account'],
        'foreignAccountsCount': len(result['foreignAccounts']),
        'campaignCount': result['campaignSummary']['count'],
        'byStatus': result['campaignSummary']['byStatus'],
        'byType': result['campaignSummary']['byType'],
        'currentMonthTotal': result['currentMonth']['total'],
        'campaignPerformanceRows': len(result['campaignPerformanceCurrentMonth']),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
