#!/usr/bin/env python3
"""Fetch Klaviyo email/SMS attribution overview for reporting-v2."""

from __future__ import annotations

import json
import os
import time
import urllib.error
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_LOCAL = ROOT / '.env.local'
OUTPUT = ROOT / 'data' / 'current' / 'klaviyo_overview.json'
BASE_URL = 'https://a.klaviyo.com/api'
REVISION = '2026-04-15'
PRAGUE_TZ = ZoneInfo('Europe/Prague')

CHANNEL_LABELS = {
    '$email_channel': 'email',
    '$sms_channel': 'sms',
    '$mobile_push_channel': 'mobile_push',
    'email': 'email',
    'sms': 'sms',
}

METRIC_CANDIDATES = {
    'placed_order': ['Placed Order', 'TianDe Placed Order', 'Placed Order 2'],
    'received_email': ['Received Email'],
    'clicked_email': ['Clicked Email'],
    'received_sms': ['Received SMS'],
    'clicked_sms': ['Clicked SMS'],
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


def api_headers() -> dict:
    key = env_value('KLAVIYO_PRIVATE_API_KEY')
    if not key:
        raise SystemExit('Missing KLAVIYO_PRIVATE_API_KEY')
    return {
        'Authorization': f'Klaviyo-API-Key {key}',
        'accept': 'application/json',
        'content-type': 'application/json',
        'revision': REVISION,
    }


def request_json(req: Request, timeout: int = 60, retries: int = 4) -> dict:
    last_error = None
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 or attempt >= retries - 1:
                raise
            retry_after = exc.headers.get('Retry-After')
            delay = float(retry_after) if retry_after else min(10 * (attempt + 1), 30)
            time.sleep(delay)
    raise last_error or RuntimeError('Klaviyo request failed without response')


def get_json(url: str) -> dict:
    req = Request(url, headers=api_headers())
    return request_json(req, timeout=60)


def post_json(path: str, payload: dict) -> dict:
    req = Request(
        f"{BASE_URL}/{path.lstrip('/')}",
        data=json.dumps(payload).encode('utf-8'),
        headers=api_headers(),
        method='POST',
    )
    return request_json(req, timeout=90)


def paginate(path_or_url: str) -> list[dict]:
    url = path_or_url if path_or_url.startswith('http') else f"{BASE_URL}/{path_or_url.lstrip('/')}"
    rows = []
    while url:
        payload = get_json(url)
        rows.extend(payload.get('data') or [])
        url = (payload.get('links') or {}).get('next')
    return rows


def current_month_window() -> tuple[str, str, str]:
    today = date.today()
    since = today.replace(day=1)
    until = today - timedelta(days=1)
    if until < since:
        until = today
    until_exclusive = until + timedelta(days=1)
    return since.isoformat(), until.isoformat(), until_exclusive.isoformat()


def metric_lookup() -> dict[str, dict]:
    metrics = paginate('metrics/')
    lookup = {}
    by_name = {((row.get('attributes') or {}).get('name') or '').strip().lower(): row for row in metrics}
    for key, candidates in METRIC_CANDIDATES.items():
        row = next((by_name.get(name.lower()) for name in candidates if by_name.get(name.lower())), None)
        if row:
            lookup[key] = {
                'id': row.get('id'),
                'name': (row.get('attributes') or {}).get('name'),
            }
    return lookup


def build_filter(since: str, until_exclusive: str, extra_filters: list[str] | None = None) -> list[str]:
    return [
        f'greater-or-equal(datetime,{since}T00:00:00)',
        f'less-than(datetime,{until_exclusive}T00:00:00)',
        *list(extra_filters or []),
    ]


def metric_aggregate(metric_id: str, *, measurements: list[str], since: str, until_exclusive: str, interval: str, by: list[str] | None = None, extra_filters: list[str] | None = None) -> dict:
    payload = {
        'data': {
            'type': 'metric-aggregate',
            'attributes': {
                'metric_id': metric_id,
                'measurements': measurements,
                'filter': build_filter(since, until_exclusive, extra_filters),
                'interval': interval,
                'timezone': 'Europe/Prague',
            },
        },
    }
    if by:
        payload['data']['attributes']['by'] = by
    return post_json('metric-aggregates/', payload)


def dimension_channel(value: str) -> str:
    return CHANNEL_LABELS.get(str(value or '').strip(), 'other')


def local_date_string(value: str) -> str:
    if not value:
        return ''
    return datetime.fromisoformat(value).astimezone(PRAGUE_TZ).date().isoformat()


def series_by_channel(payload: dict) -> list[dict]:
    attributes = (payload.get('data') or {}).get('attributes') or {}
    dates = attributes.get('dates') or []
    rows = {local_date_string(raw): {'date': local_date_string(raw)} for raw in dates}
    for bucket in attributes.get('data') or []:
        channel = dimension_channel((bucket.get('dimensions') or [''])[0])
        measurements = bucket.get('measurements') or {}
        counts = measurements.get('count') or [0] * len(dates)
        values = measurements.get('sum_value') or [0] * len(dates)
        for idx, raw_date in enumerate(dates):
            date_key = local_date_string(raw_date)
            row = rows.setdefault(date_key, {'date': date_key})
            row[f'{channel}Orders'] = round(float(counts[idx] or 0), 2)
            row[f'{channel}RevenueCzk'] = round(float(values[idx] or 0), 2)
    output = []
    for date_key in sorted(rows):
        row = rows[date_key]
        email_revenue = round(float(row.get('emailRevenueCzk') or 0), 2)
        sms_revenue = round(float(row.get('smsRevenueCzk') or 0), 2)
        email_orders = round(float(row.get('emailOrders') or 0), 2)
        sms_orders = round(float(row.get('smsOrders') or 0), 2)
        output.append({
            'date': row['date'],
            'emailAttributedRevenueCzk': email_revenue,
            'smsAttributedRevenueCzk': sms_revenue,
            'totalAttributedRevenueCzk': round(email_revenue + sms_revenue, 2),
            'emailAttributedOrders': email_orders,
            'smsAttributedOrders': sms_orders,
            'totalAttributedOrders': round(email_orders + sms_orders, 2),
        })
    return output


def daily_count_series(payload: dict) -> list[dict]:
    attributes = (payload.get('data') or {}).get('attributes') or {}
    dates = attributes.get('dates') or []
    data = ((attributes.get('data') or [{}])[0].get('measurements') or {}).get('count') or []
    return [
        {'date': local_date_string(raw), 'count': round(float(data[idx] or 0), 2)}
        for idx, raw in enumerate(dates)
    ]


def merge_daily_series(*series_sets: tuple[str, list[dict]]) -> list[dict]:
    rows = defaultdict(lambda: {'date': ''})
    for key, series in series_sets:
        for row in series:
            day = row.get('date')
            if not day:
                continue
            rows[day]['date'] = day
            rows[day][key] = row.get('count', 0)
    output = []
    for day in sorted(rows):
        row = rows[day]
        email_recipients = round(float(row.get('emailRecipients') or 0), 2)
        sms_recipients = round(float(row.get('smsRecipients') or 0), 2)
        email_clicks = round(float(row.get('emailClicks') or 0), 2)
        sms_clicks = round(float(row.get('smsClicks') or 0), 2)
        output.append({
            'date': day,
            'emailRecipients': email_recipients,
            'smsRecipients': sms_recipients,
            'totalRecipients': round(email_recipients + sms_recipients, 2),
            'emailClicks': email_clicks,
            'smsClicks': sms_clicks,
            'totalClicks': round(email_clicks + sms_clicks, 2),
        })
    return output


def fetch_account() -> dict:
    accounts = paginate('accounts/')
    account = accounts[0] if accounts else {}
    attrs = account.get('attributes') or {}
    contact = attrs.get('contact_information') or {}
    return {
        'id': account.get('id'),
        'organizationName': (contact.get('organization_name') or '').strip(),
        'senderName': (contact.get('default_sender_name') or '').strip(),
        'senderEmail': (contact.get('default_sender_email') or '').strip(),
        'websiteUrl': (contact.get('website_url') or '').strip(),
        'timezone': attrs.get('timezone') or 'Europe/Prague',
        'preferredCurrency': attrs.get('preferred_currency') or 'CZK',
        'publicApiKey': attrs.get('public_api_key') or '',
    }


def fetch_flows_map() -> dict[str, dict]:
    rows = paginate('flows/')
    return {
        row.get('id'): {
            'id': row.get('id'),
            'name': (row.get('attributes') or {}).get('name') or row.get('id') or 'Flow',
            'status': (row.get('attributes') or {}).get('status') or 'unknown',
            'triggerType': (row.get('attributes') or {}).get('trigger_type') or '',
        }
        for row in rows
        if row.get('id')
    }


def fetch_recent_campaigns(limit_per_channel: int = 6) -> list[dict]:
    items = []
    for channel in ('email', 'sms'):
        filt = quote(f"equals(messages.channel,'{channel}')", safe='')
        rows = paginate(f'campaigns/?filter={filt}&sort=-updated_at')
        for row in rows[:limit_per_channel]:
            attrs = row.get('attributes') or {}
            send_strategy = attrs.get('send_strategy') or {}
            items.append({
                'id': row.get('id'),
                'name': attrs.get('name') or row.get('id') or 'Kampaň',
                'channel': channel,
                'status': attrs.get('status') or 'unknown',
                'createdAt': attrs.get('created_at'),
                'updatedAt': attrs.get('updated_at'),
                'scheduledAt': attrs.get('scheduled_at'),
                'sendDatetime': (send_strategy.get('datetime') or attrs.get('send_time')),
            })
    items.sort(key=lambda row: row.get('updatedAt') or row.get('createdAt') or '', reverse=True)
    return items[:10]


def top_flows(placed_order_metric_id: str, since: str, until_exclusive: str, flows_map: dict[str, dict]) -> list[dict]:
    payload = metric_aggregate(
        placed_order_metric_id,
        measurements=['count', 'sum_value'],
        since=since,
        until_exclusive=until_exclusive,
        interval='month',
        by=['$attributed_flow', '$attributed_channel'],
        extra_filters=['not(equals($attributed_flow,""))'],
    )
    rows = []
    for bucket in ((payload.get('data') or {}).get('attributes') or {}).get('data') or []:
        dimensions = bucket.get('dimensions') or ['', '']
        flow_id = dimensions[0] or ''
        channel = dimension_channel(dimensions[1] if len(dimensions) > 1 else '')
        measurements = bucket.get('measurements') or {}
        orders = round(float((measurements.get('count') or [0])[0] or 0), 2)
        revenue = round(float((measurements.get('sum_value') or [0])[0] or 0), 2)
        meta = flows_map.get(flow_id) or {'name': flow_id or 'Bez flow', 'status': 'unknown', 'triggerType': ''}
        rows.append({
            'flowId': flow_id,
            'flowName': meta['name'],
            'status': meta['status'],
            'triggerType': meta['triggerType'],
            'channel': channel,
            'attributedOrders': orders,
            'attributedRevenueCzk': revenue,
        })
    rows.sort(key=lambda row: row['attributedRevenueCzk'], reverse=True)
    return rows


def main() -> int:
    load_env_local(ENV_LOCAL)
    account = fetch_account()
    metrics = metric_lookup()
    missing_metrics = [key for key in ('placed_order', 'received_email', 'clicked_email') if key not in metrics]
    if missing_metrics:
        raise SystemExit('Missing required Klaviyo metrics: ' + ', '.join(missing_metrics))

    since, until, until_exclusive = current_month_window()
    flows_map = fetch_flows_map()

    placed_daily = series_by_channel(metric_aggregate(
        metrics['placed_order']['id'],
        measurements=['count', 'sum_value'],
        since=since,
        until_exclusive=until_exclusive,
        interval='day',
        by=['$attributed_channel'],
    ))

    email_received_daily = daily_count_series(metric_aggregate(
        metrics['received_email']['id'],
        measurements=['count'],
        since=since,
        until_exclusive=until_exclusive,
        interval='day',
    ))
    email_clicked_daily = daily_count_series(metric_aggregate(
        metrics['clicked_email']['id'],
        measurements=['count'],
        since=since,
        until_exclusive=until_exclusive,
        interval='day',
    ))
    sms_received_daily = daily_count_series(metric_aggregate(
        metrics.get('received_sms', {}).get('id') or metrics['received_email']['id'],
        measurements=['count'],
        since=since,
        until_exclusive=until_exclusive,
        interval='day',
        extra_filters=['equals($message,"__no_sms_metric__")'] if 'received_sms' not in metrics else None,
    )) if 'received_sms' in metrics else []
    sms_clicked_daily = daily_count_series(metric_aggregate(
        metrics.get('clicked_sms', {}).get('id') or metrics['clicked_email']['id'],
        measurements=['count'],
        since=since,
        until_exclusive=until_exclusive,
        interval='day',
        extra_filters=['equals($message,"__no_sms_metric__")'] if 'clicked_sms' not in metrics else None,
    )) if 'clicked_sms' in metrics else []

    engagement_daily = merge_daily_series(
        ('emailRecipients', email_received_daily),
        ('smsRecipients', sms_received_daily),
        ('emailClicks', email_clicked_daily),
        ('smsClicks', sms_clicked_daily),
    )

    engagement_by_day = {row['date']: row for row in engagement_daily}
    daily_summary = []
    for row in placed_daily:
        engagement = engagement_by_day.get(row['date']) or {'emailRecipients': 0, 'smsRecipients': 0, 'totalRecipients': 0, 'emailClicks': 0, 'smsClicks': 0, 'totalClicks': 0}
        total_recipients = round(float(engagement.get('totalRecipients') or 0), 2)
        total_clicks = round(float(engagement.get('totalClicks') or 0), 2)
        daily_summary.append({
            **row,
            **engagement,
            'clickRate': round(total_clicks / total_recipients, 4) if total_recipients else None,
        })
    for day, engagement in engagement_by_day.items():
        if any(item['date'] == day for item in daily_summary):
            continue
        total_recipients = round(float(engagement.get('totalRecipients') or 0), 2)
        total_clicks = round(float(engagement.get('totalClicks') or 0), 2)
        daily_summary.append({
            'date': day,
            'emailAttributedRevenueCzk': 0.0,
            'smsAttributedRevenueCzk': 0.0,
            'totalAttributedRevenueCzk': 0.0,
            'emailAttributedOrders': 0.0,
            'smsAttributedOrders': 0.0,
            'totalAttributedOrders': 0.0,
            **engagement,
            'clickRate': round(total_clicks / total_recipients, 4) if total_recipients else None,
        })
    daily_summary.sort(key=lambda row: row['date'])

    current = {
        'dateFrom': since,
        'dateTo': until,
        'emailAttributedRevenueCzk': round(sum(float(row.get('emailAttributedRevenueCzk') or 0) for row in daily_summary), 2),
        'smsAttributedRevenueCzk': round(sum(float(row.get('smsAttributedRevenueCzk') or 0) for row in daily_summary), 2),
        'emailAttributedOrders': round(sum(float(row.get('emailAttributedOrders') or 0) for row in daily_summary), 2),
        'smsAttributedOrders': round(sum(float(row.get('smsAttributedOrders') or 0) for row in daily_summary), 2),
        'emailRecipients': round(sum(float(row.get('emailRecipients') or 0) for row in daily_summary), 2),
        'smsRecipients': round(sum(float(row.get('smsRecipients') or 0) for row in daily_summary), 2),
        'emailClicks': round(sum(float(row.get('emailClicks') or 0) for row in daily_summary), 2),
        'smsClicks': round(sum(float(row.get('smsClicks') or 0) for row in daily_summary), 2),
    }
    current['totalAttributedRevenueCzk'] = round(current['emailAttributedRevenueCzk'] + current['smsAttributedRevenueCzk'], 2)
    current['totalAttributedOrders'] = round(current['emailAttributedOrders'] + current['smsAttributedOrders'], 2)
    current['totalRecipients'] = round(current['emailRecipients'] + current['smsRecipients'], 2)
    current['totalClicks'] = round(current['emailClicks'] + current['smsClicks'], 2)
    current['clickRate'] = round(current['totalClicks'] / current['totalRecipients'], 4) if current['totalRecipients'] else None

    flows_current_month = top_flows(metrics['placed_order']['id'], since, until_exclusive, flows_map)
    result = {
        'source': {
            'status': 'live_api',
            'platform': 'klaviyo',
            'message': 'Klaviyo email/SMS attribution fetched directly from Klaviyo APIs.',
        },
        'window': {'dateFrom': since, 'dateTo': until},
        'account': account,
        'metrics': metrics,
        'currentMonth': current,
        'dailySummary': daily_summary,
        'flowsCurrentMonth': flows_current_month,
        'topFlowsCurrentMonth': flows_current_month[:10],
        'recentCampaigns': fetch_recent_campaigns(),
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {OUTPUT}')
    print(json.dumps({
        'account': account.get('organizationName') or account.get('id'),
        'currentMonth': current,
        'topFlows': len(result['topFlowsCurrentMonth']),
        'recentCampaigns': len(result['recentCampaigns']),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
