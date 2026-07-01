#!/usr/bin/env python3
"""Fetch GA4 overview for reporting-v2 using Google Analytics Data API."""

from __future__ import annotations

import json
import os
import base64
import subprocess
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
ENV_LOCAL = ROOT / '.env.local'
OUTPUT = ROOT / 'data' / 'current' / 'ga4_overview.json'
PURCHASE_OUTPUT = ROOT / 'data' / 'current' / 'ga4_purchase_journey_window.json'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_URL = 'https://analyticsdata.googleapis.com/v1beta'
DEFAULT_PROPERTY_ID = '220403487'
EXPLICIT_AI_PATTERNS = (
    'chatgpt',
    'openai',
    'perplexity',
    'claude.ai',
    'anthropic',
    'gemini',
    'bard',
    'copilot',
    'grok',
    'you.com',
    'mistral',
)
ASSISTANT_LIKE_PATTERNS = (
    'bing / organic',
    'ntp.msn.com / referral',
    'edgeservices.bing.com / referral',
    'copilot.microsoft.com / referral',
)
ORGANIC_DIRECT_PROXY_SOURCES = {
    '(direct) / (none)',
    'google / organic',
    'bing / organic',
}
PROXY_EXCLUDED_PREFIXES = (
    '/kosik',
    '/objednavka',
    '/ucet',
    '/prihlaseni',
    '/registrace',
)
AI_BUCKET_META = {
    'explicit_ai': {
        'label': 'Explicitní AI referral',
        'confidence': 'high',
        'description': 'Referrer je přímo z AI nástroje typu ChatGPT nebo Perplexity.',
    },
    'assistant_like': {
        'label': 'Pravděpodobný assistant / Bing vstup',
        'confidence': 'medium',
        'description': 'Zdroje, které často souvisí s Copilotem, Bingem nebo assistant vrstvou, ale nejsou stoprocentní důkaz.',
    },
    'organic_direct_proxy': {
        'label': 'Proxy organic/direct',
        'confidence': 'low',
        'description': 'Nepřímý odhad nad organic/direct vstupy na hlubší obsahové landing pages. Trendově užitečné, ne tvrdá pravda.',
    },
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


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


def post_form(url: str, payload: dict) -> dict:
    req = Request(
        url,
        data=urlencode(payload).encode('utf-8'),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    with urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode('utf-8'))


def post_json(url: str, payload: dict, access_token: str) -> dict:
    req = Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    with urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode('utf-8'))


def fetch_access_token() -> str:
    service_account_file = (
        env_value('GA4_SERVICE_ACCOUNT_FILE')
        or env_value('GOOGLE_APPLICATION_CREDENTIALS')
    )
    if service_account_file:
        return fetch_service_account_access_token(Path(service_account_file))

    client_id = env_value('GA4_OAUTH_CLIENT_ID') or env_value('GOOGLE_ADS_OAUTH_CLIENT_ID')
    client_secret = env_value('GA4_OAUTH_CLIENT_SECRET') or env_value('GOOGLE_ADS_OAUTH_CLIENT_SECRET')
    refresh_token = env_value('GA4_REFRESH_TOKEN')
    if not client_id or not client_secret or not refresh_token:
        missing = []
        if not client_id:
            missing.append('GA4_OAUTH_CLIENT_ID')
        if not client_secret:
            missing.append('GA4_OAUTH_CLIENT_SECRET')
        if not refresh_token:
            missing.append('GA4_REFRESH_TOKEN')
        raise SystemExit(f'Missing required env keys: {", ".join(missing)}')

    payload = post_form(TOKEN_URL, {
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
    })
    token = payload.get('access_token')
    if not token:
        raise SystemExit(f'Failed to refresh GA4 access token: {payload}')
    return token


def fetch_service_account_access_token(path: Path) -> str:
    if not path.exists():
        raise SystemExit(f'GA4 service account file not found: {path}')

    payload = json.loads(path.read_text(encoding='utf-8'))
    client_email = (payload.get('client_email') or '').strip()
    private_key = payload.get('private_key') or ''
    token_uri = (payload.get('token_uri') or TOKEN_URL).strip()
    if not client_email or not private_key:
        raise SystemExit('GA4 service account JSON is missing client_email or private_key')

    issued_at = int(time.time())
    header = {'alg': 'RS256', 'typ': 'JWT'}
    claims = {
        'iss': client_email,
        'scope': 'https://www.googleapis.com/auth/analytics.readonly',
        'aud': token_uri,
        'iat': issued_at,
        'exp': issued_at + 3600,
    }
    signing_input = f"{b64url(json.dumps(header, separators=(',', ':')).encode('utf-8'))}.{b64url(json.dumps(claims, separators=(',', ':')).encode('utf-8'))}"

    with tempfile.TemporaryDirectory() as tmp_dir:
        key_path = Path(tmp_dir) / 'service-account.pem'
        payload_path = Path(tmp_dir) / 'jwt.txt'
        sig_path = Path(tmp_dir) / 'jwt.sig'
        key_path.write_text(private_key, encoding='utf-8')
        payload_path.write_text(signing_input, encoding='utf-8')
        result = subprocess.run(
            ['openssl', 'dgst', '-sha256', '-sign', str(key_path), '-out', str(sig_path), str(payload_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(f"Failed to sign GA4 service-account JWT: {(result.stderr or result.stdout).strip()}")
        signature = sig_path.read_bytes()

    assertion = f'{signing_input}.{b64url(signature)}'
    response = post_form(token_uri, {
        'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
        'assertion': assertion,
    })
    token = response.get('access_token')
    if not token:
        raise SystemExit(f'Failed to exchange GA4 service-account assertion: {response}')
    return token


def run_report(property_id: str, access_token: str, payload: dict) -> dict:
    url = f'{API_URL}/properties/{property_id}:runReport'
    return post_json(url, payload, access_token)


def parse_value(value: str, value_type: str):
    if value in (None, ''):
        return 0
    if value_type in {'TYPE_INTEGER', 'TYPE_STANDARD'}:
        try:
            return int(float(value))
        except ValueError:
            return 0
    try:
        num = float(value)
    except ValueError:
        return value
    if value_type == 'TYPE_SECONDS':
        return round(num, 2)
    if value_type == 'TYPE_PERCENT':
        return round(num * 100, 2)
    return round(num, 2)


def rows_to_dicts(response: dict) -> list[dict]:
    dimension_headers = [item.get('name') for item in response.get('dimensionHeaders') or []]
    metric_headers = response.get('metricHeaders') or []
    rows = []
    for row in response.get('rows') or []:
        record = {}
        for idx, header in enumerate(dimension_headers):
            record[header] = ((row.get('dimensionValues') or [])[idx] or {}).get('value')
        for idx, header in enumerate(metric_headers):
            name = header.get('name')
            value_type = header.get('type') or 'TYPE_STANDARD'
            raw_value = ((row.get('metricValues') or [])[idx] or {}).get('value')
            record[name] = parse_value(raw_value, value_type)
        rows.append(record)
    return rows


def build_summary(response: dict, label: str, date_from: str, date_to: str) -> dict:
    row = (rows_to_dicts(response) or [{}])[0]
    sessions = float(row.get('sessions') or 0)
    engaged_sessions = float(row.get('engagedSessions') or 0)
    purchases = float(row.get('ecommercePurchases') or 0)
    revenue = float(row.get('purchaseRevenue') or 0)
    return {
        'label': label,
        'dateFrom': date_from,
        'dateTo': date_to,
        'activeUsers': int(row.get('activeUsers') or 0),
        'sessions': int(row.get('sessions') or 0),
        'engagedSessions': int(row.get('engagedSessions') or 0),
        'screenPageViews': int(row.get('screenPageViews') or 0),
        'ecommercePurchases': round(purchases, 2),
        'purchaseRevenue': round(revenue, 2),
        'averageSessionDuration': round(float(row.get('averageSessionDuration') or 0), 2),
        'bounceRatePct': round(float(row.get('bounceRate') or 0), 2),
        'engagementRatePct': round((engaged_sessions / sessions) * 100, 2) if sessions else None,
        'purchaseRatePct': round((purchases / sessions) * 100, 2) if sessions else None,
        'revenuePerSession': round(revenue / sessions, 2) if sessions else None,
    }


def report_payload(date_from: str, date_to: str, *, dimensions=None, metrics=None, limit=50, order_bys=None, dimension_filter=None):
    payload = {
        'dateRanges': [{'startDate': date_from, 'endDate': date_to}],
        'metrics': [{'name': name} for name in (metrics or [])],
        'limit': limit,
        'keepEmptyRows': False,
    }
    if dimensions:
        payload['dimensions'] = [{'name': name} for name in dimensions]
    if order_bys:
        payload['orderBys'] = order_bys
    if dimension_filter:
        payload['dimensionFilter'] = dimension_filter
    return payload


def aggregate_purchase_journeys(row_sets: list[list[dict]]) -> dict:
    merged: dict[str, dict] = {}
    for rows in row_sets:
        for row in rows:
            transaction_id = str(row.get('transactionId') or '').strip()
            if not transaction_id or transaction_id in {'(not set)', 'not set'}:
                continue
            target = merged.setdefault(transaction_id, {
                'transactionId': transaction_id,
                'purchaseAt': row.get('dateHourMinute') or '',
                'propertyIds': [],
                'purchaseRevenue': 0.0,
                'ecommercePurchases': 0.0,
                'sessionDefaultChannelGroup': row.get('sessionDefaultChannelGroup') or 'Unassigned',
                'sessionSourceMedium': row.get('sessionSourceMedium') or '(not set)',
                'sessionCampaignName': row.get('sessionCampaignName') or '(not set)',
                'firstUserSourceMedium': row.get('firstUserSourceMedium') or '(not set)',
                'firstUserCampaignName': row.get('firstUserCampaignName') or '(not set)',
                'landingPage': row.get('landingPagePlusQueryString') or '(not set)',
                'touchpoints': [],
            })
            target['purchaseRevenue'] = round(float(target.get('purchaseRevenue') or 0) + float(row.get('purchaseRevenue') or 0), 2)
            target['ecommercePurchases'] = round(float(target.get('ecommercePurchases') or 0) + float(row.get('ecommercePurchases') or 0), 2)
            property_id = row.get('_propertyId')
            if property_id and property_id not in target['propertyIds']:
                target['propertyIds'].append(property_id)
            touch = {
                'propertyId': property_id,
                'sessionDefaultChannelGroup': row.get('sessionDefaultChannelGroup') or 'Unassigned',
                'sessionSourceMedium': row.get('sessionSourceMedium') or '(not set)',
                'sessionCampaignName': row.get('sessionCampaignName') or '(not set)',
                'firstUserSourceMedium': row.get('firstUserSourceMedium') or '(not set)',
                'firstUserCampaignName': row.get('firstUserCampaignName') or '(not set)',
                'landingPage': row.get('landingPagePlusQueryString') or '(not set)',
                'purchaseRevenue': round(float(row.get('purchaseRevenue') or 0), 2),
            }
            if touch not in target['touchpoints']:
                target['touchpoints'].append(touch)
    rows = list(merged.values())
    rows.sort(key=lambda item: float(item.get('purchaseRevenue') or 0), reverse=True)
    total_revenue = round(sum(float(item.get('purchaseRevenue') or 0) for item in rows), 2)
    return {
        'rows': rows,
        'summary': {
            'transactions': len(rows),
            'purchaseRevenue': total_revenue,
        },
    }


def month_window(offset: int = 0) -> tuple[str, str, str]:
    today = date.today()
    first_this_month = today.replace(day=1)
    if offset == 0:
        since = first_this_month
        until = today - timedelta(days=1)
        if until < since:
            until = today
    else:
        until = first_this_month - timedelta(days=1)
        since = until.replace(day=1)
    label = f'{since.month}/{since.year}'
    return label, since.isoformat(), until.isoformat()


def parse_property_ids() -> list[str]:
    raw = env_value('GA4_PROPERTY_IDS') or env_value('GA4_PROPERTY_ID', DEFAULT_PROPERTY_ID)
    return [item.strip() for item in raw.split(',') if item.strip()]


def combine_summary_rows(rows: list[dict], label: str, date_from: str, date_to: str) -> dict:
    sessions = sum(float(row.get('sessions') or 0) for row in rows)
    engaged_sessions = sum(float(row.get('engagedSessions') or 0) for row in rows)
    purchases = sum(float(row.get('ecommercePurchases') or 0) for row in rows)
    revenue = sum(float(row.get('purchaseRevenue') or 0) for row in rows)
    screen_page_views = sum(float(row.get('screenPageViews') or 0) for row in rows)
    active_users = sum(float(row.get('activeUsers') or 0) for row in rows)
    avg_session_duration = sum(float(row.get('averageSessionDuration') or 0) * float(row.get('sessions') or 0) for row in rows) / sessions if sessions else 0
    bounce_rate = sum(float(row.get('bounceRate') or 0) * float(row.get('sessions') or 0) for row in rows) / sessions if sessions else 0
    return {
        'label': label,
        'dateFrom': date_from,
        'dateTo': date_to,
        'activeUsers': int(round(active_users)),
        'sessions': int(round(sessions)),
        'engagedSessions': int(round(engaged_sessions)),
        'screenPageViews': int(round(screen_page_views)),
        'ecommercePurchases': round(purchases, 2),
        'purchaseRevenue': round(revenue, 2),
        'averageSessionDuration': round(avg_session_duration, 2),
        'bounceRatePct': round(bounce_rate, 2),
        'engagementRatePct': round((engaged_sessions / sessions) * 100, 2) if sessions else None,
        'purchaseRatePct': round((purchases / sessions) * 100, 2) if sessions else None,
        'revenuePerSession': round(revenue / sessions, 2) if sessions else None,
    }


def merge_dimension_rows(row_sets: list[list[dict]], key_field: str, metric_fields: list[str], *, limit: int, order_field: str, rename: tuple[str, str] | None = None) -> list[dict]:
    merged = {}
    for rows in row_sets:
        for row in rows:
            key = row.get(key_field) or 'Unassigned'
            target = merged.setdefault(key, {key_field: key})
            for metric in metric_fields:
                target[metric] = round(float(target.get(metric) or 0) + float(row.get(metric) or 0), 2)
    result = list(merged.values())
    result.sort(key=lambda row: float(row.get(order_field) or 0), reverse=True)
    result = result[:limit]
    if rename:
        old_key, new_key = rename
        for row in result:
            row[new_key] = row.pop(old_key, None) or 'Unassigned'
    return result


def normalize_text(value: str) -> str:
    return str(value or '').strip().lower()


def normalize_landing_path(value: str) -> str:
    landing = normalize_text(value)
    if not landing or landing == '(not set)':
        return ''
    return landing.split('?', 1)[0].strip()


def classify_source_bucket(source_medium: str) -> str | None:
    normalized = normalize_text(source_medium)
    if not normalized:
        return None
    if any(pattern in normalized for pattern in EXPLICIT_AI_PATTERNS):
        return 'explicit_ai'
    if normalized in ASSISTANT_LIKE_PATTERNS or any(pattern in normalized for pattern in ASSISTANT_LIKE_PATTERNS if '/' in pattern and pattern != normalized):
        return 'assistant_like'
    return None


def is_proxy_organic_direct(source_medium: str, landing_page: str) -> bool:
    normalized_source = normalize_text(source_medium)
    if normalized_source not in ORGANIC_DIRECT_PROXY_SOURCES:
        return False
    path = normalize_landing_path(landing_page)
    if not path or path == '/':
        return False
    if any(path.startswith(prefix) for prefix in PROXY_EXCLUDED_PREFIXES):
        return False
    return (
        '_z' in path
        or path.startswith('/vyhledavani/')
        or path.startswith('/aktivni-slozky')
        or path.count('/') >= 2
    )


def _top_entries(counter: dict[str, float], *, limit: int = 5) -> list[dict]:
    rows = [{'label': label, 'value': round(value, 2)} for label, value in counter.items() if value]
    rows.sort(key=lambda item: item['value'], reverse=True)
    return rows[:limit]


def build_ai_traffic_layer(source_rows_7d: list[dict], purchase_rows: list[dict]) -> dict:
    bucket_stats = {}
    for key, meta in AI_BUCKET_META.items():
        bucket_stats[key] = {
            'key': key,
            'label': meta['label'],
            'confidence': meta['confidence'],
            'description': meta['description'],
            'sessions7d': 0,
            'activeUsers7d': 0,
            'purchases7d': 0.0,
            'revenue7d': 0.0,
            'transactions30dLastClick': 0,
            'revenue30dLastClick': 0.0,
            'transactions30dInfluenced': 0,
            'revenue30dInfluenced': 0.0,
            'topSessionSources7d': defaultdict(float),
            'topLastClickSources30d': defaultdict(float),
            'topInfluenceSources30d': defaultdict(float),
            'topLandingPages30d': defaultdict(float),
        }

    for row in source_rows_7d:
        bucket = classify_source_bucket(row.get('sourceMedium') or '')
        if not bucket:
            continue
        target = bucket_stats[bucket]
        target['sessions7d'] += int(round(float(row.get('sessions') or 0)))
        target['activeUsers7d'] += int(round(float(row.get('activeUsers') or 0)))
        target['purchases7d'] = round(float(target['purchases7d']) + float(row.get('ecommercePurchases') or 0), 2)
        target['revenue7d'] = round(float(target['revenue7d']) + float(row.get('purchaseRevenue') or 0), 2)
        target['topSessionSources7d'][row.get('sourceMedium') or '(not set)'] += float(row.get('sessions') or 0)

    for row in purchase_rows:
        landing_page = row.get('landingPage') or '(not set)'
        session_source = row.get('sessionSourceMedium') or '(not set)'
        first_user_source = row.get('firstUserSourceMedium') or '(not set)'
        revenue = round(float(row.get('purchaseRevenue') or 0), 2)

        last_click_bucket = classify_source_bucket(session_source)
        if not last_click_bucket and is_proxy_organic_direct(session_source, landing_page):
            last_click_bucket = 'organic_direct_proxy'
        if last_click_bucket:
            target = bucket_stats[last_click_bucket]
            target['transactions30dLastClick'] += 1
            target['revenue30dLastClick'] = round(float(target['revenue30dLastClick']) + revenue, 2)
            target['topLastClickSources30d'][session_source] += revenue
            target['topLandingPages30d'][landing_page] += revenue

        influence_bucket = classify_source_bucket(first_user_source)
        if not influence_bucket and is_proxy_organic_direct(first_user_source, landing_page):
            influence_bucket = 'organic_direct_proxy'
        if influence_bucket:
            target = bucket_stats[influence_bucket]
            target['transactions30dInfluenced'] += 1
            target['revenue30dInfluenced'] = round(float(target['revenue30dInfluenced']) + revenue, 2)
            target['topInfluenceSources30d'][first_user_source] += revenue
            target['topLandingPages30d'][landing_page] += revenue

    buckets = []
    for key in ('explicit_ai', 'assistant_like', 'organic_direct_proxy'):
        row = bucket_stats[key]
        row['topSessionSources7d'] = _top_entries(row['topSessionSources7d'])
        row['topLastClickSources30d'] = _top_entries(row['topLastClickSources30d'])
        row['topInfluenceSources30d'] = _top_entries(row['topInfluenceSources30d'])
        row['topLandingPages30d'] = _top_entries(row['topLandingPages30d'])
        buckets.append(row)

    return {
        'ready': True,
        'methodology': {
            'sessionsWindow': 'last7days',
            'purchaseWindowDays': 30,
            'note': 'Explicit AI referrals jsou tvrdě měřené. Assistant-like a organic/direct proxy jsou trendové bucketi, ne absolutní pravda.',
        },
        'summary': {
            'explicitAiSessions7d': bucket_stats['explicit_ai']['sessions7d'],
            'assistantLikeSessions7d': bucket_stats['assistant_like']['sessions7d'],
            'proxyTransactions30d': bucket_stats['organic_direct_proxy']['transactions30dInfluenced'],
            'explicitAiRevenue30dInfluenced': round(bucket_stats['explicit_ai']['revenue30dInfluenced'], 2),
            'assistantLikeRevenue30dInfluenced': round(bucket_stats['assistant_like']['revenue30dInfluenced'], 2),
            'proxyRevenue30dInfluenced': round(bucket_stats['organic_direct_proxy']['revenue30dInfluenced'], 2),
        },
        'buckets': buckets,
    }


def main() -> None:
    load_env_local(ENV_LOCAL)
    property_ids = parse_property_ids()
    access_token = fetch_access_token()

    yesterday = date.today() - timedelta(days=1)
    current_label, current_from, current_to = month_window(0)
    previous_label, previous_from, previous_to = month_window(1)
    last7_from = (date.today() - timedelta(days=7)).isoformat()
    last30_from = (date.today() - timedelta(days=30)).isoformat()
    yesterday_iso = yesterday.isoformat()

    summary_metrics = [
        'activeUsers', 'sessions', 'engagedSessions', 'screenPageViews',
        'averageSessionDuration', 'bounceRate', 'ecommercePurchases', 'purchaseRevenue'
    ]

    yesterday_rows = []
    last7_rows = []
    last30_rows = []
    current_month_rows = []
    previous_month_rows = []

    channel_metrics = ['sessions', 'activeUsers', 'engagedSessions', 'ecommercePurchases', 'purchaseRevenue']
    channel_order = [{'metric': {'metricName': 'purchaseRevenue'}, 'desc': True}]
    channels_7d_sets = []
    channels_current_sets = []

    landing_metrics = ['sessions', 'activeUsers', 'ecommercePurchases', 'purchaseRevenue']
    landing_sets = []
    source_sets = []
    page_sets = []
    country_sets = []
    purchase_journey_sets = []

    for property_id in property_ids:
        yesterday_rows.append((rows_to_dicts(run_report(property_id, access_token, report_payload(yesterday_iso, yesterday_iso, metrics=summary_metrics, limit=1))) or [{}])[0])
        last7_rows.append((rows_to_dicts(run_report(property_id, access_token, report_payload(last7_from, yesterday_iso, metrics=summary_metrics, limit=1))) or [{}])[0])
        last30_rows.append((rows_to_dicts(run_report(property_id, access_token, report_payload(last30_from, yesterday_iso, metrics=summary_metrics, limit=1))) or [{}])[0])
        current_month_rows.append((rows_to_dicts(run_report(property_id, access_token, report_payload(current_from, current_to, metrics=summary_metrics, limit=1))) or [{}])[0])
        previous_month_rows.append((rows_to_dicts(run_report(property_id, access_token, report_payload(previous_from, previous_to, metrics=summary_metrics, limit=1))) or [{}])[0])
        channels_7d_sets.append(rows_to_dicts(run_report(property_id, access_token, report_payload(last7_from, yesterday_iso, dimensions=['sessionDefaultChannelGroup'], metrics=channel_metrics, limit=12, order_bys=channel_order))))
        channels_current_sets.append(rows_to_dicts(run_report(property_id, access_token, report_payload(current_from, current_to, dimensions=['sessionDefaultChannelGroup'], metrics=channel_metrics, limit=12, order_bys=channel_order))))
        source_sets.append(rows_to_dicts(run_report(property_id, access_token, report_payload(last7_from, yesterday_iso, dimensions=['sessionSourceMedium'], metrics=channel_metrics, limit=120, order_bys=channel_order))))
        landing_sets.append(rows_to_dicts(run_report(property_id, access_token, report_payload(last7_from, yesterday_iso, dimensions=['landingPagePlusQueryString'], metrics=landing_metrics, limit=12, order_bys=channel_order))))
        page_sets.append(rows_to_dicts(run_report(property_id, access_token, report_payload(last7_from, yesterday_iso, dimensions=['pageTitle'], metrics=['screenPageViews', 'activeUsers'], limit=12, order_bys=[{'metric': {'metricName': 'screenPageViews'}, 'desc': True}]))))
        country_sets.append(rows_to_dicts(run_report(property_id, access_token, report_payload(last7_from, yesterday_iso, dimensions=['country'], metrics=['activeUsers', 'ecommercePurchases', 'purchaseRevenue'], limit=10, order_bys=[{'metric': {'metricName': 'activeUsers'}, 'desc': True}]))))
        purchase_rows = rows_to_dicts(run_report(property_id, access_token, report_payload(
            last30_from,
            yesterday_iso,
            dimensions=['transactionId', 'dateHourMinute', 'sessionDefaultChannelGroup', 'sessionSourceMedium', 'sessionCampaignName', 'firstUserSourceMedium', 'firstUserCampaignName', 'landingPagePlusQueryString'],
            metrics=['ecommercePurchases', 'purchaseRevenue'],
            limit=25000,
            order_bys=[{'metric': {'metricName': 'purchaseRevenue'}, 'desc': True}],
        )))
        for row in purchase_rows:
            row['_propertyId'] = property_id
        purchase_journey_sets.append(purchase_rows)

    yesterday_summary = combine_summary_rows(yesterday_rows, yesterday_iso, yesterday_iso, yesterday_iso)
    last7_summary = combine_summary_rows(last7_rows, 'last7days', last7_from, yesterday_iso)
    last30_summary = combine_summary_rows(last30_rows, 'last30days', last30_from, yesterday_iso)
    current_month_summary = combine_summary_rows(current_month_rows, current_label, current_from, current_to)
    previous_month_summary = combine_summary_rows(previous_month_rows, previous_label, previous_from, previous_to)
    channels_7d = merge_dimension_rows(channels_7d_sets, 'sessionDefaultChannelGroup', channel_metrics, limit=12, order_field='purchaseRevenue', rename=('sessionDefaultChannelGroup', 'channel'))
    channels_current = merge_dimension_rows(channels_current_sets, 'sessionDefaultChannelGroup', channel_metrics, limit=12, order_field='purchaseRevenue', rename=('sessionDefaultChannelGroup', 'channel'))
    sources_7d = merge_dimension_rows(source_sets, 'sessionSourceMedium', channel_metrics, limit=120, order_field='purchaseRevenue', rename=('sessionSourceMedium', 'sourceMedium'))
    for row in channels_7d + channels_current:
        sessions = float(row.get('sessions') or 0)
        purchases = float(row.get('ecommercePurchases') or 0)
        revenue = float(row.get('purchaseRevenue') or 0)
        row['purchaseRatePct'] = round((purchases / sessions) * 100, 2) if sessions else None
        row['revenuePerSession'] = round(revenue / sessions, 2) if sessions else None
    for row in sources_7d:
        sessions = float(row.get('sessions') or 0)
        purchases = float(row.get('ecommercePurchases') or 0)
        revenue = float(row.get('purchaseRevenue') or 0)
        row['purchaseRatePct'] = round((purchases / sessions) * 100, 2) if sessions else None
        row['revenuePerSession'] = round(revenue / sessions, 2) if sessions else None
    landing_rows = merge_dimension_rows(landing_sets, 'landingPagePlusQueryString', landing_metrics, limit=12, order_field='purchaseRevenue', rename=('landingPagePlusQueryString', 'landingPage'))
    for row in landing_rows:
        sessions = float(row.get('sessions') or 0)
        purchases = float(row.get('ecommercePurchases') or 0)
        row['purchaseRatePct'] = round((purchases / sessions) * 100, 2) if sessions else None
    page_rows = merge_dimension_rows(page_sets, 'pageTitle', ['screenPageViews', 'activeUsers'], limit=12, order_field='screenPageViews')
    country_rows = merge_dimension_rows(country_sets, 'country', ['activeUsers', 'ecommercePurchases', 'purchaseRevenue'], limit=10, order_field='activeUsers')
    purchase_journeys = aggregate_purchase_journeys(purchase_journey_sets)
    ai_traffic = build_ai_traffic_layer(sources_7d, purchase_journeys['rows'])

    payload = {
        'generatedAt': date.today().isoformat(),
        'source': {
            'status': 'live_api',
            'message': 'GA4 data tečou přes Google Analytics Data API jako sloučený bundle přes více properties.',
        },
        'property': {
            'propertyId': property_ids[0],
            'propertyIds': property_ids,
            'mode': 'combined' if len(property_ids) > 1 else 'single',
        },
        'yesterday': yesterday_summary,
        'last7days': last7_summary,
        'last30days': last30_summary,
        'currentMonth': current_month_summary,
        'previousMonth': previous_month_summary,
        'channelPerformance7d': channels_7d,
        'channelPerformanceCurrentMonth': channels_current,
        'landingPages7d': landing_rows,
        'topPages7d': page_rows,
        'countries7d': country_rows,
        'sourcePerformance7d': sources_7d,
        'aiTraffic': ai_traffic,
        'purchaseJourneyWindow': {
            'dateFrom': last30_from,
            'dateTo': yesterday_iso,
            'transactions': purchase_journeys['summary']['transactions'],
            'purchaseRevenue': purchase_journeys['summary']['purchaseRevenue'],
        },
    }

    purchase_payload = {
        'generatedAt': date.today().isoformat(),
        'window': {
            'dateFrom': last30_from,
            'dateTo': yesterday_iso,
            'days': 30,
        },
        'summary': purchase_journeys['summary'],
        'rows': purchase_journeys['rows'],
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    PURCHASE_OUTPUT.write_text(json.dumps(purchase_payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'GA4 overview written to {OUTPUT}')
    print(f'GA4 purchase journeys written to {PURCHASE_OUTPUT}')


if __name__ == '__main__':
    try:
        main()
    except HTTPError as exc:
        body = exc.read().decode('utf-8', errors='ignore') if hasattr(exc, 'read') else ''
        raise SystemExit(f'GA4 fetch failed: HTTP {exc.code} {body}')
