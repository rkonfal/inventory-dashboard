#!/usr/bin/env python3
import hashlib
import json
import os
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / '.env.local'
CURRENT_DIR = ROOT / 'data' / 'current'
SNAPSHOT_DIR = ROOT / 'data' / 'snapshots'
BASE_URL = 'https://open.eu.4px.com/router/api/service'


def load_env_file(path: Path):
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


def compact_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def build_sign(params, body_json, app_secret):
    ordered = ''.join(f'{key}{params[key]}' for key in sorted(params))
    return hashlib.md5((ordered + body_json + app_secret).encode('utf-8')).hexdigest()


def call_4px(method, body, app_key, app_secret, language='en'):
    body_json = compact_json(body)
    params = {
        'app_key': app_key,
        'format': 'json',
        'method': method,
        'timestamp': str(int(time.time() * 1000)),
        'v': '1.0',
    }
    params['sign'] = build_sign(params, body_json, app_secret)
    if language:
        params['language'] = language
    req = Request(
        f'{BASE_URL}?{urlencode(params)}',
        data=body_json.encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'reporting-v2/1.0'},
    )
    with urlopen(req, timeout=40) as resp:
        payload = json.loads(resp.read().decode('utf-8', 'ignore'))
    if str(payload.get('result')) != '1':
        raise RuntimeError(f'4PX {method} failed: {payload.get("msg") or payload}')
    return payload.get('data')


def fetch_inventory(app_key, app_secret, warehouse_code):
    page = 1
    page_size = 100
    items = []
    total = None
    while True:
        payload = call_4px(
            'fu.wms.inventory.get',
            {'warehouse_code': warehouse_code, 'page_no': page, 'page_size': page_size},
            app_key,
            app_secret,
        )
        page_items = payload.get('data') or []
        items.extend(page_items)
        total = int(payload.get('total') or len(items))
        if not page_items or len(items) >= total:
            break
        page += 1
        if page > 30:
            break
    low_stock = [x for x in items if float(x.get('available_stock') or 0) <= 10]
    return {
        'items': items,
        'lowStock': sorted(low_stock, key=lambda x: float(x.get('available_stock') or 0))[:100],
        'total': total or len(items),
        'availableStockTotal': sum(float(x.get('available_stock') or 0) for x in items),
        'pendingStockTotal': sum(float(x.get('pending_stock') or 0) for x in items),
        'freezeStockTotal': sum(float(x.get('freeze_stock') or 0) for x in items),
    }


def fetch_recent_outbound(app_key, app_secret, warehouse_code, max_pages=10):
    page = 1
    page_size = 100
    items = []
    while page <= max_pages:
        payload = call_4px(
            'fu.wms.outbound.getlist',
            {'from_warehouse_code': warehouse_code, 'page_no': page, 'page_size': page_size},
            app_key,
            app_secret,
        )
        page_items = payload.get('data') or []
        if not page_items:
            break
        items.extend(page_items)
        if len(page_items) < page_size:
            break
        page += 1
    return {
        'items': items,
        'scannedPages': min(page, max_pages),
        'topLogisticsProducts': Counter(x.get('logistics_product_code') or '–' for x in items).most_common(10),
        'topCountries': Counter(x.get('country') or '–' for x in items).most_common(10),
    }


def account_payload(label, inventory, outbound):
    top_product = outbound['topLogisticsProducts'][0][0] if outbound['topLogisticsProducts'] else None
    return {
        'label': label,
        'inventory': {
            'items': len(inventory['items']),
            'availableStockTotal': round(inventory['availableStockTotal']),
            'pendingStockTotal': round(inventory['pendingStockTotal']),
            'freezeStockTotal': round(inventory['freezeStockTotal']),
            'lowStockItems': len(inventory['lowStock']),
        },
        'outbound': {
            'items': len(outbound['items']),
            'scannedPages': outbound['scannedPages'],
            'topLogisticsProduct': top_product,
        },
    }


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def main():
    load_env_file(ENV_FILE)
    warehouse_code = os.environ.get('FOURPX_WAREHOUSE_CODE', 'CZPRGA')
    max_pages = int(os.environ.get('FOURPX_OUTBOUND_MAX_PAGES', '10'))
    now = datetime.now(timezone.utc).astimezone()
    stamp = now.strftime('%Y%m%d-%H%M%S')
    generated_at = now.isoformat()

    required = [
        'FOURPX_CZ_APP_KEY', 'FOURPX_CZ_APP_SECRET',
        'FOURPX_SK_APP_KEY', 'FOURPX_SK_APP_SECRET',
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(f'Missing required env keys: {", ".join(missing)}')

    cz_inventory = fetch_inventory(os.environ['FOURPX_CZ_APP_KEY'], os.environ['FOURPX_CZ_APP_SECRET'], warehouse_code)
    sk_inventory = fetch_inventory(os.environ['FOURPX_SK_APP_KEY'], os.environ['FOURPX_SK_APP_SECRET'], warehouse_code)
    cz_outbound = fetch_recent_outbound(os.environ['FOURPX_CZ_APP_KEY'], os.environ['FOURPX_CZ_APP_SECRET'], warehouse_code, max_pages=max_pages)
    sk_outbound = fetch_recent_outbound(os.environ['FOURPX_SK_APP_KEY'], os.environ['FOURPX_SK_APP_SECRET'], warehouse_code, max_pages=max_pages)

    wpj_ready = bool(os.environ.get('WPJ_GRAPHQL_URL') and os.environ.get('WPJ_ACCESS_TOKEN'))
    warnings = []
    if not wpj_ready:
        warnings.append('WPJ část zatím není připojená. 4PX reporting běží, ale e-shop výkon čeká na GraphQL token nebo current Claude flow.')

    CURRENT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / stamp
    snapshot_path.mkdir(parents=True, exist_ok=True)

    payloads = {
        '4px_cz_inventory.json': {'generatedAt': generated_at, **cz_inventory},
        '4px_sk_inventory.json': {'generatedAt': generated_at, **sk_inventory},
        '4px_cz_outbound_recent.json': {'generatedAt': generated_at, **cz_outbound},
        '4px_sk_outbound_recent.json': {'generatedAt': generated_at, **sk_outbound},
    }

    for name, payload in payloads.items():
        write_json(CURRENT_DIR / name, payload)
        write_json(snapshot_path / name, payload)

    portal_summary = {
        'generatedAt': generated_at,
        'config': {
            'warehouseCode': warehouse_code,
            'outboundMaxPages': max_pages,
        },
        'warnings': warnings,
        'accounts': {
            'cz': account_payload('CZ', cz_inventory, cz_outbound),
            'sk': account_payload('SK', sk_inventory, sk_outbound),
        },
        'wpJ': {
            'ready': wpj_ready,
            'message': 'WPJ připojeno.' if wpj_ready else 'WPJ zatím není připojené. Chybí token nebo current refresh flow.',
            'orders': 0,
        },
    }
    write_json(CURRENT_DIR / 'portal_summary.json', portal_summary)
    write_json(snapshot_path / 'portal_summary.json', portal_summary)

    latest_snapshot = SNAPSHOT_DIR / 'latest'
    if latest_snapshot.exists() or latest_snapshot.is_symlink():
        latest_snapshot.unlink()
    latest_snapshot.symlink_to(snapshot_path.name)

    print(f'Refreshed reporting data at {generated_at}')
    print(f'CZ inventory rows: {len(cz_inventory["items"])} | CZ outbound rows: {len(cz_outbound["items"])}')
    print(f'SK inventory rows: {len(sk_inventory["items"])} | SK outbound rows: {len(sk_outbound["items"])}')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise
