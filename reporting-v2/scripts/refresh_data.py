#!/usr/bin/env python3
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
VENDOR_PY_DIR = ROOT / 'vendor_py'
if VENDOR_PY_DIR.exists() and str(VENDOR_PY_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_PY_DIR))

try:
    import xlrd
except ImportError:
    xlrd = None

ENV_FILE = ROOT / '.env.local'
CONFIG_DIR = ROOT / 'config'
SKU_MAPPING_OVERRIDE_FILE = CONFIG_DIR / 'sku_mapping_overrides.json'
POS_ADMIN_VIEW_OVERRIDE_FILE = CONFIG_DIR / 'pos_admin_view_overrides.json'
CURRENT_DIR = ROOT / 'data' / 'current'
SNAPSHOT_DIR = ROOT / 'data' / 'snapshots'
BASE_URL = 'https://open.eu.4px.com/router/api/service'
PRAGUE_TZ = ZoneInfo('Europe/Prague')
LEGACY_ABRA_HTML = ROOT.parent / 'portals' / 'diamond-plus-report' / 'index.html'
LEGACY_MONTH_KEYS = ['jan', 'feb', 'mar']
LIVE_FINANCE_MARKETING_ACCOUNTS = ('518900', '518901')
LIVE_FINANCE_LOGISTICS_ACCOUNTS = ('518201', '518400')
LIVE_FINANCE_BANKFEE_ACCOUNTS = ('568001', '568100')
SK_EUR_TO_CZK_RATE = 27.27

WPJ_ORDER_CLASSIFICATION_FIELDS = '''
      source { name }
      deliveryAddress {
        city
        country { name code }
      }
      invoiceAddress {
        city
      }
      history {
        adminId
        comment
      }
'''

WPJ_ORDERS_HISTORY_QUERY = f'''
query ($offset:Int,$limit:Int,$sort:OrderSortInput,$filter:OrderFilterInput){{
  orders(offset:$offset, limit:$limit, sort:$sort, filter:$filter) {{
    items {{
      id
      code
      dateCreated
      cancelled
      isPaid
      status {{ id name }}
      totalPrice {{ withVat withoutVat }}
{WPJ_ORDER_CLASSIFICATION_FIELDS}
    }}
    hasNextPage
    hasPreviousPage
  }}
}}
'''

WPJ_ORDERS_DETAIL_QUERY = f'''
query ($offset:Int,$limit:Int,$sort:OrderSortInput,$filter:OrderFilterInput){{
  orders(offset:$offset, limit:$limit, sort:$sort, filter:$filter) {{
    items {{
      id
      code
      dateCreated
      cancelled
      isPaid
      status {{ id name }}
      totalPrice {{ withVat withoutVat }}
{WPJ_ORDER_CLASSIFICATION_FIELDS}
      deliveryType {{
        id
        delivery {{ id name }}
        payment {{ id name type }}
        price {{ withVat }}
      }}
      items {{
        type
        productId
        code
        name
        ean
        pieces
        totalPrice {{ withVat withoutVat }}
      }}
    }}
    hasNextPage
    hasPreviousPage
  }}
}}
'''

WPJ_ORDERS_YEAR_METRICS_QUERY = f'''
query ($offset:Int,$limit:Int,$sort:OrderSortInput,$filter:OrderFilterInput){{
  orders(offset:$offset, limit:$limit, sort:$sort, filter:$filter) {{
    items {{
      id
      dateCreated
{WPJ_ORDER_CLASSIFICATION_FIELDS}
      items {{
        type
        code
        name
        pieces
      }}
    }}
    hasNextPage
    hasPreviousPage
  }}
}}
'''

WPJ_PRODUCTS_QUERY = '''
query ($offset:Int,$limit:Int,$sort:ProductSortInput){
  products(offset:$offset, limit:$limit, sort:$sort) {
    items {
      id
      code
      ean
      title
      url
      visible
      inStore
      price { withVat withoutVat }
      stores {
        inStore
        store { id name }
      }
    }
    hasNextPage
    hasPreviousPage
  }
}
'''


def load_env_file(path: Path):
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


def load_manual_sku_overrides(path: Path):
    overrides = {
        'aliases': {},
        'ignore': set(),
    }
    if not path.exists():
        return overrides
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'Neplatný JSON v override mapě SKU: {path}') from exc

    for raw_code, canonical_code in (payload.get('aliases') or {}).items():
        raw = normalize_product_code(raw_code)
        canonical = normalize_product_code(canonical_code)
        if raw and canonical:
            overrides['aliases'][raw] = canonical

    for raw_code in (payload.get('ignore') or []):
        raw = normalize_product_code(raw_code)
        if raw:
            overrides['ignore'].add(raw)

    return overrides


def load_pos_admin_view_overrides(path: Path):
    overrides = {}
    if not path.exists():
        return overrides
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'Neplatný JSON v override mapě POS adminů: {path}') from exc

    for view, admin_ids in (payload or {}).items():
        if view not in {'ltm', 'mecin'}:
            continue
        for admin_id in admin_ids or []:
            try:
                overrides[int(admin_id)] = view
            except (TypeError, ValueError):
                continue
    return overrides


def compact_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def build_sign(params, body_json, app_secret):
    ordered = ''.join(f'{key}{params[key]}' for key in sorted(params))
    return hashlib.md5((ordered + body_json + app_secret).encode('utf-8')).hexdigest()


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def write_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def normalize_date_string(value):
    if value is None:
        return None
    text = str(value).strip().strip('"')
    if not text:
        return None
    if ' ' in text and 'T' not in text:
        text = text.replace(' ', 'T')
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    return text


def parse_dt(value, default_tz=PRAGUE_TZ):
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(default_tz)
    text = normalize_date_string(value)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=default_tz)
    return dt.astimezone(default_tz)


def money(value):
    return round(float(value or 0), 2)


def num(value):
    return float(value or 0)


def pct_delta(current, baseline):
    if not baseline:
        return None
    return round(((current - baseline) / baseline) * 100, 1)


def delta_label(current, baseline, suffix=''):
    if baseline is None:
        return 'bez srovnání'
    delta = pct_delta(current, baseline)
    sign = '+' if delta and delta > 0 else ''
    return f'{sign}{delta:.1f} % vs průměr {baseline:.1f}{suffix}'


def format_czk(value):
    return f'{round(float(value or 0)):,}'.replace(',', ' ') + ' Kč'


def format_units(value):
    number = float(value or 0)
    if abs(number - round(number)) < 0.05:
        return f'{int(round(number)):,}'.replace(',', ' ') + ' ks'
    return f'{number:,.1f}'.replace(',', ' ').replace('.0', '') + ' ks'


def previous_day_window(now_local: datetime):
    target_date = now_local.date() - timedelta(days=1)
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 1, tzinfo=PRAGUE_TZ)
    end = start + timedelta(days=1) - timedelta(seconds=1)
    return start, end


def current_local_time():
    return datetime.now(timezone.utc).astimezone(PRAGUE_TZ)


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


def chunked(values, size):
    for idx in range(0, len(values), size):
        yield values[idx:idx + size]


def fetch_inventory_details(app_key, app_secret, warehouse_code, inventory_items, batch_size=100):
    unique_skus = []
    seen = set()
    for row in inventory_items:
        sku = str(row.get('sku_code') or '').strip()
        if not sku or sku in seen:
            continue
        seen.add(sku)
        unique_skus.append(sku)

    details = []
    for sku_batch in chunked(unique_skus, batch_size):
        payload = call_4px(
            'fu.wms.inventory.getdetail',
            {'warehouse_code': warehouse_code, 'lstsku': sku_batch},
            app_key,
            app_secret,
        )
        details.extend(payload.get('inventorydetaillist') or [])
    return {
        'items': details,
        'uniqueSkuCount': len(unique_skus),
        'detailRows': len(details),
    }


def summarize_expiry_details(label, detail_rows):
    per_sku = {}
    for row in detail_rows:
        expiry_dt = parse_dt(row.get('expiry_date'))
        stock = num(row.get('warehouse_stock'))
        if not expiry_dt or stock <= 0:
            continue
        sku = row.get('sku_code') or '–'
        if str(sku).upper().startswith('TEST'):
            continue
        item = per_sku.setdefault(sku, {
            'account': label,
            'sku': sku,
            'batchCount': 0,
            'datedStock': 0.0,
            'nearestExpiry': None,
            'nearestExpiryStock': 0.0,
        })
        item['batchCount'] += 1
        item['datedStock'] += stock
        if item['nearestExpiry'] is None or expiry_dt < item['nearestExpiry']:
            item['nearestExpiry'] = expiry_dt
            item['nearestExpiryStock'] = stock
        elif expiry_dt == item['nearestExpiry']:
            item['nearestExpiryStock'] += stock

    results = []
    now_local = current_local_time()
    for sku, row in per_sku.items():
        days_to_expiry = (row['nearestExpiry'].date() - now_local.date()).days
        results.append({
            'account': row['account'],
            'sku': sku,
            'dateExpiry': row['nearestExpiry'].date().isoformat(),
            'daysToExpiry': days_to_expiry,
            'datedStock': round(row['datedStock'], 2),
            'stockAtNearestExpiry': round(row['nearestExpiryStock'], 2),
            'batchCount': row['batchCount'],
            'riskScore': round(row['datedStock'] / (max(days_to_expiry + 1, 1) ** 1.3), 2),
        })

    results.sort(key=lambda item: (-item['riskScore'], item['daysToExpiry'], -item['datedStock'], item['sku']))
    return results


def outbound_timestamp(item):
    return parse_dt(item.get('create_time') or item.get('audit_time') or item.get('update_time'))


def fetch_recent_outbound(app_key, app_secret, warehouse_code, max_pages=20, stop_before=None):
    page = 1
    page_size = 100
    items = []
    crossed_stop_boundary = False
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
        if stop_before:
            if any((outbound_timestamp(item) or datetime.max.replace(tzinfo=PRAGUE_TZ)) < stop_before for item in page_items):
                crossed_stop_boundary = True
                break
        if len(page_items) < page_size:
            break
        page += 1
    timestamps = [outbound_timestamp(item) for item in items]
    timestamps = [ts for ts in timestamps if ts]
    return {
        'items': items,
        'scannedPages': min(page, max_pages),
        'hitMaxPages': page >= max_pages,
        'crossedStopBoundary': crossed_stop_boundary,
        'newestTimestamp': max(timestamps).isoformat() if timestamps else None,
        'oldestTimestamp': min(timestamps).isoformat() if timestamps else None,
        'topLogisticsProducts': Counter(x.get('logistics_product_code') or '–' for x in items).most_common(10),
        'topCountries': Counter(x.get('country') or '–' for x in items).most_common(10),
    }


def wpj_endpoint():
    return os.environ.get('WPJ_GRAPHQL_URL') or os.environ.get('WPJ_PROXY_URL')


def call_wpj(query, variables, url, access_token):
    payload = {'query': query, 'variables': variables or {}}
    req = Request(
        url,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'X-Access-Token': access_token,
            'User-Agent': 'reporting-v2/1.0',
        },
        method='POST',
    )
    with urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode('utf-8', 'ignore'))
    if data.get('errors'):
        raise RuntimeError(f'WPJ GraphQL failed: {data["errors"][:2]}')
    return data['data']


def fetch_wpj_orders(url, access_token, start_dt, end_dt, *, limit=1000, detailed=False):
    offset = 0
    items = []
    query = WPJ_ORDERS_DETAIL_QUERY if detailed else WPJ_ORDERS_HISTORY_QUERY
    while True:
        payload = call_wpj(
            query,
            {
                'offset': offset,
                'limit': limit,
                'sort': {'dateCreated': 'DESC'},
                'filter': {
                    'dateFrom': start_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'dateTo': end_dt.strftime('%Y-%m-%d %H:%M:%S'),
                },
            },
            url,
            access_token,
        )
        page = payload['orders']
        page_items = page.get('items') or []
        items.extend(page_items)
        if not page.get('hasNextPage') or not page_items:
            break
        offset += len(page_items)
    return items


def fetch_wpj_products(url, access_token, limit=1000):
    offset = 0
    items = []
    while True:
        payload = call_wpj(
            WPJ_PRODUCTS_QUERY,
            {
                'offset': offset,
                'limit': limit,
                'sort': {'id': 'ASC'},
            },
            url,
            access_token,
        )
        page = payload['products']
        page_items = page.get('items') or []
        items.extend(page_items)
        if not page.get('hasNextPage') or not page_items:
            break
        offset += len(page_items)
    return items


def fetch_wpj_year_order_metrics(url, access_token, start_dt, end_dt, limit=1000):
    offset = 0
    items = []
    while True:
        payload = call_wpj(
            WPJ_ORDERS_YEAR_METRICS_QUERY,
            {
                'offset': offset,
                'limit': limit,
                'sort': {'dateCreated': 'DESC'},
                'filter': {
                    'dateFrom': start_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'dateTo': end_dt.strftime('%Y-%m-%d %H:%M:%S'),
                },
            },
            url,
            access_token,
        )
        page = payload['orders']
        page_items = page.get('items') or []
        items.extend(page_items)
        if not page.get('hasNextPage') or not page_items:
            break
        offset += len(page_items)
    return items


def load_json_if_fresh(path: Path, *, max_age_hours):
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    generated = parse_dt(data.get('generatedAt'))
    if not generated:
        return None
    age_hours = (current_local_time() - generated).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None
    return data


def product_label(item):
    code = item.get('code') or '–'
    title = item.get('title') or item.get('name') or 'Bez názvu'
    return f'{code} · {title}'


def order_status_name(order):
    return (order.get('status') or {}).get('name') or '–'


def is_problematic_order(order):
    if order.get('cancelled'):
        return True
    name = order_status_name(order).lower()
    patterns = ('storno', 'zruš', 'chyba', 'reklam', 'vrác', 'neuhra', 'nezaplac', 'nedokon')
    return any(p in name for p in patterns)


def summarize_orders(orders, include_views=True, pos_admin_views=None):
    product_units = Counter()
    product_revenue = Counter()
    payment_methods = Counter()
    delivery_methods = Counter()
    status_counts = Counter()
    sold_product_codes = set()
    product_rows = {}
    revenue = 0.0
    cancelled = 0
    problematic = 0

    for order in orders:
        revenue += order_total_czk(order, pos_admin_views)
        if order.get('cancelled'):
            cancelled += 1
        if is_problematic_order(order):
            problematic += 1
        status_counts[order_status_name(order)] += 1

        delivery_type = order.get('deliveryType') or {}
        delivery = delivery_type.get('delivery') or {}
        payment = delivery_type.get('payment') or {}
        if payment.get('name'):
            payment_methods[payment['name']] += 1
        if delivery.get('name'):
            delivery_methods[delivery['name']] += 1

        for item in order.get('items') or []:
            if item.get('type') != 'product':
                continue
            key = item.get('code') or str(item.get('productId') or item.get('name') or '–')
            sold_product_codes.add(key)
            label = f'{item.get("code") or "–"} · {item.get("name") or "Bez názvu"}'
            product_rows[key] = {
                'code': item.get('code'),
                'name': item.get('name'),
                'label': label,
            }
            product_units[key] += num(item.get('pieces'))
            product_revenue[key] += order_item_revenue_czk(order, item, pos_admin_views)

    def top_products(counter, limit=5, formatter=None):
        rows = []
        for key, value in counter.most_common(limit):
            meta = product_rows.get(key, {'code': key, 'name': key, 'label': key})
            row = {'code': meta.get('code'), 'name': meta.get('name'), 'label': meta.get('label'), 'value': round(value, 2)}
            if formatter:
                row['formatted'] = formatter(value)
            rows.append(row)
        return rows

    average_order_value = revenue / len(orders) if orders else 0
    summary = {
        'orders': len(orders),
        'revenueWithVat': round(revenue, 2),
        'averageOrderValue': round(average_order_value, 2),
        'cancelledOrders': cancelled,
        'problematicOrders': problematic,
        'statuses': [{'name': k, 'count': v} for k, v in status_counts.most_common()],
        'paymentMethods': [{'name': k, 'count': v} for k, v in payment_methods.most_common()],
        'deliveryMethods': [{'name': k, 'count': v} for k, v in delivery_methods.most_common()],
        'topProductsByUnits': top_products(product_units, formatter=lambda x: format_units(x)),
        'topProductsByRevenue': top_products(product_revenue, formatter=lambda x: format_czk(x)),
        'soldProductCodes': sorted(sold_product_codes),
    }

    if include_views:
        summary['byView'] = {
            'complete': summarize_orders(orders, include_views=False, pos_admin_views=pos_admin_views),
            'cz': summarize_orders([order for order in orders if classify_order_view(order, pos_admin_views) == 'cz'], include_views=False, pos_admin_views=pos_admin_views),
            'sk': summarize_orders([order for order in orders if classify_order_view(order, pos_admin_views) == 'sk'], include_views=False, pos_admin_views=pos_admin_views),
            'ltm': summarize_orders([order for order in orders if classify_order_view(order, pos_admin_views) == 'ltm'], include_views=False, pos_admin_views=pos_admin_views),
            'mecin': summarize_orders([order for order in orders if classify_order_view(order, pos_admin_views) == 'mecin'], include_views=False, pos_admin_views=pos_admin_views),
        }

    return summary


def summarize_daily_history(orders, target_date, pos_admin_views=None):
    by_day = defaultdict(list)
    for order in orders:
        dt = parse_dt(order.get('dateCreated'))
        if not dt:
            continue
        by_day[dt.date()].append(order)

    days = []
    for index in range(7, -1, -1):
        day = target_date - timedelta(days=index)
        summary = summarize_orders(by_day.get(day, []), pos_admin_views=pos_admin_views)
        days.append({
            'date': day.isoformat(),
            'orders': summary['orders'],
            'revenueWithVat': summary['revenueWithVat'],
            'averageOrderValue': summary['averageOrderValue'],
        })

    yesterday = days[-1]
    history = days[:-1]
    baseline_orders = round(sum(day['orders'] for day in history) / len(history), 2) if history else None
    baseline_revenue = round(sum(day['revenueWithVat'] for day in history) / len(history), 2) if history else None
    return days, baseline_orders, baseline_revenue


def store_stock_breakdown(product):
    rows = []
    for row in product.get('stores') or []:
        store = row.get('store') or {}
        rows.append({
            'storeId': store.get('id'),
            'storeName': store.get('name') or '–',
            'inStore': num(row.get('inStore')),
        })
    return rows


def summarize_stock(products, sold_product_codes, previous_products=None):
    previous_by_code = {item.get('code'): item for item in (previous_products or []) if item.get('code')}
    sold_set = set(sold_product_codes or [])
    low_stock_sold = []
    low_stock_global = []
    negative_rows = []
    movement_rows = []

    for product in products:
        code = product.get('code')
        if not code:
            continue
        stores = store_stock_breakdown(product)
        fourpx_stores = [store for store in stores if (store.get('storeName') or '').startswith('4PX')]
        effective_stock = sum(store['inStore'] for store in fourpx_stores) if fourpx_stores else num(product.get('inStore'))
        row = {
            'code': code,
            'title': product.get('title') or 'Bez názvu',
            'stock': round(effective_stock, 2),
            'reportedStock': num(product.get('inStore')),
            'stores': stores,
            'priceWithVat': money((product.get('price') or {}).get('withVat')),
            'visible': bool(product.get('visible')),
        }
        if row['visible'] and effective_stock <= 10:
            low_stock_global.append(row)
            if code in sold_set:
                low_stock_sold.append(row)
        for store in stores:
            if store['inStore'] < 0:
                negative_rows.append({
                    'code': code,
                    'title': row['title'],
                    'storeName': store['storeName'],
                    'inStore': store['inStore'],
                })
        previous = previous_by_code.get(code)
        if previous:
            previous_stores = store_stock_breakdown(previous)
            previous_fourpx = [store for store in previous_stores if (store.get('storeName') or '').startswith('4PX')]
            previous_stock = sum(store['inStore'] for store in previous_fourpx) if previous_fourpx else num(previous.get('inStore'))
            diff = round(effective_stock - previous_stock, 2)
            if diff:
                movement_rows.append({
                    'code': code,
                    'title': row['title'],
                    'currentStock': round(effective_stock, 2),
                    'previousStock': round(previous_stock, 2),
                    'delta': diff,
                })

    low_stock_sold.sort(key=lambda x: (x['stock'], x['title']))
    low_stock_global.sort(key=lambda x: (x['stock'], x['title']))
    negative_rows.sort(key=lambda x: x['inStore'])
    movement_rows.sort(key=lambda x: abs(x['delta']), reverse=True)

    return {
        'lowStockSoldYesterday': low_stock_sold[:5],
        'lowStockOverall': low_stock_global[:10],
        'negativeStoreStock': negative_rows[:10],
        'largestMovesSinceLastSnapshot': movement_rows[:10],
    }


def normalize_city_name(value):
    text = str(value or '').strip().lower()
    return ''.join(ch for ch in unicodedata.normalize('NFD', text) if unicodedata.category(ch) != 'Mn')


def classify_order_view(order, pos_admin_views=None):
    pos_admin_views = pos_admin_views or {}
    source_name = ((order.get('source') or {}).get('name') or '').strip().lower()
    delivery_city = ((order.get('deliveryAddress') or {}).get('city') or '').strip().lower()
    invoice_city = ((order.get('invoiceAddress') or {}).get('city') or '').strip().lower()
    country = (((order.get('deliveryAddress') or {}).get('country') or {}).get('code') or '').strip().upper()

    if source_name == 'pokladna':
        for entry in order.get('history') or []:
            if (entry.get('comment') or '').strip() == 'Vytvořeno v pokladně':
                admin_id = entry.get('adminId')
                if admin_id in pos_admin_views:
                    return pos_admin_views[admin_id]
                break

        for city in (delivery_city, invoice_city):
            city_ascii = normalize_city_name(city)
            if 'mecin' in city_ascii:
                return 'mecin'
            if 'litomer' in city_ascii:
                return 'ltm'
        return 'ltm'
    if country == 'SK':
        return 'sk'
    return 'cz'


def order_total_czk(order, pos_admin_views=None):
    total = money((order.get('totalPrice') or {}).get('withVat'))
    return round(total * SK_EUR_TO_CZK_RATE, 2) if classify_order_view(order, pos_admin_views) == 'sk' else total


def order_item_revenue_czk(order, item, pos_admin_views=None):
    revenue = money((item.get('totalPrice') or {}).get('withVat'))
    return round(revenue * SK_EUR_TO_CZK_RATE, 2) if classify_order_view(order, pos_admin_views) == 'sk' else revenue


def collect_wpj_order_product_metrics(orders, wpj_by_code=None, manual_overrides=None, pos_admin_views=None):
    wpj_by_code = wpj_by_code or {}
    metrics = {}
    for order in orders:
        view = classify_order_view(order, pos_admin_views)
        for item in order.get('items') or []:
            if item.get('type') != 'product':
                continue
            raw_code = item.get('code') or str(item.get('productId') or item.get('name') or '–')
            code, mapping = resolve_4px_code_alias(raw_code, wpj_by_code, manual_overrides)
            row = metrics.setdefault(code, {
                'code': code,
                'name': (wpj_by_code.get(code) or {}).get('title') or item.get('name') or 'Bez názvu',
                'units': 0.0,
                'revenueWithVat': 0.0,
                'sourceCodes': set(),
                'byView': {
                    'complete': {'units': 0.0, 'revenueWithVat': 0.0},
                    'cz': {'units': 0.0, 'revenueWithVat': 0.0},
                    'sk': {'units': 0.0, 'revenueWithVat': 0.0},
                    'ltm': {'units': 0.0, 'revenueWithVat': 0.0},
                    'mecin': {'units': 0.0, 'revenueWithVat': 0.0},
                },
            })
            row['sourceCodes'].add(normalize_product_code(raw_code))
            units = num(item.get('pieces'))
            revenue = order_item_revenue_czk(order, item)
            row['units'] += units
            row['revenueWithVat'] += revenue
            row['byView']['complete']['units'] += units
            row['byView']['complete']['revenueWithVat'] += revenue
            row['byView'][view]['units'] += units
            row['byView'][view]['revenueWithVat'] += revenue
    for row in metrics.values():
        row['sourceCodes'] = sorted(row['sourceCodes'])
        row['byView'] = {
            key: {
                'units': round(value['units'], 2),
                'revenueWithVat': round(value['revenueWithVat'], 2),
            }
            for key, value in row['byView'].items()
        }
    return metrics


def normalize_product_code(value):
    return str(value or '').strip()


def resolve_4px_code_alias(code, wpj_by_code, manual_overrides=None):
    manual_overrides = manual_overrides or {'aliases': {}, 'ignore': set()}
    code = normalize_product_code(code)
    if not code:
        return code, None
    manual_target = normalize_product_code((manual_overrides.get('aliases') or {}).get(code))
    if manual_target:
        if manual_target in wpj_by_code:
            return manual_target, {
                'sourceCode': code,
                'canonicalCode': manual_target,
                'rule': 'manual_override',
                'confidence': 'manual',
            }
        return code, None
    if code in (manual_overrides.get('ignore') or set()):
        return code, None
    if code in wpj_by_code:
        return code, None
    if '/' in code:
        base_code, suffix = code.split('/', 1)
        base_code = normalize_product_code(base_code)
        suffix = normalize_product_code(suffix)
        if base_code and suffix and base_code in wpj_by_code:
            return base_code, {
                'sourceCode': code,
                'canonicalCode': base_code,
                'rule': 'strip_/variant_suffix',
                'confidence': 'high',
            }
    return code, None


def aggregate_4px_inventory(items, wpj_by_code=None, manual_overrides=None):
    wpj_by_code = wpj_by_code or {}
    grouped = {}
    for item in items or []:
        raw_code = normalize_product_code(item.get('sku_code'))
        code, mapping = resolve_4px_code_alias(raw_code, wpj_by_code, manual_overrides)
        if not code:
            continue
        row = grouped.setdefault(code, {
            'code': code,
            'sourceCodes': set(),
            'mappedSourceCodes': set(),
            'manualMappedSourceCodes': set(),
            'autoMappedSourceCodes': set(),
            'mappingRules': set(),
            'skuIds': set(),
            'batchNos': set(),
            'availableStock': 0.0,
            'pendingStock': 0.0,
            'freezeStock': 0.0,
            'onwayStock': 0.0,
        })
        row['sourceCodes'].add(raw_code)
        if mapping:
            row['mappedSourceCodes'].add(raw_code)
            row['mappingRules'].add(mapping['rule'])
            if mapping['rule'] == 'manual_override':
                row['manualMappedSourceCodes'].add(raw_code)
            else:
                row['autoMappedSourceCodes'].add(raw_code)
        if item.get('sku_id'):
            row['skuIds'].add(item['sku_id'])
        if item.get('batch_no'):
            row['batchNos'].add(item['batch_no'])
        row['availableStock'] += num(item.get('available_stock'))
        row['pendingStock'] += num(item.get('pending_stock'))
        row['freezeStock'] += num(item.get('freeze_stock'))
        row['onwayStock'] += num(item.get('onway_stock'))
    for row in grouped.values():
        row['sourceCodes'] = sorted(row['sourceCodes'])
        row['mappedSourceCodes'] = sorted(row['mappedSourceCodes'])
        row['manualMappedSourceCodes'] = sorted(row['manualMappedSourceCodes'])
        row['autoMappedSourceCodes'] = sorted(row['autoMappedSourceCodes'])
        row['mappingRules'] = sorted(row['mappingRules'])
        row['skuIds'] = sorted(row['skuIds'])
        row['batchNos'] = sorted(row['batchNos'])
    return grouped


def aggregate_4px_outbound_by_sku(outbound_payload, start_dt, end_dt, account_label, wpj_by_code=None, manual_overrides=None):
    wpj_by_code = wpj_by_code or {}
    grouped = {}
    for consignment in outbound_payload.get('items') or []:
        ts = outbound_timestamp(consignment)
        if not ts or ts < start_dt or ts > end_dt:
            continue
        consignment_no = consignment.get('consignment_no') or ''
        logistics = consignment.get('logistics_product_code') or ''
        carrier = consignment.get('carrier_brand_name') or consignment.get('carrier_code') or ''
        for sku in consignment.get('outboundlist_sku') or []:
            raw_code = normalize_product_code(sku.get('sku_code'))
            code, mapping = resolve_4px_code_alias(raw_code, wpj_by_code, manual_overrides)
            if not code:
                continue
            row = grouped.setdefault(code, {
                'code': code,
                'name': sku.get('sku_name') or 'Bez názvu',
                'sourceCodes': set(),
                'mappedSourceCodes': set(),
                'manualMappedSourceCodes': set(),
                'autoMappedSourceCodes': set(),
                'mappingRules': set(),
                'units': 0.0,
                'shipments': set(),
                'accounts': set(),
                'logisticsProducts': Counter(),
                'carriers': Counter(),
            })
            row['sourceCodes'].add(raw_code)
            if mapping:
                row['mappedSourceCodes'].add(raw_code)
                row['mappingRules'].add(mapping['rule'])
                if mapping['rule'] == 'manual_override':
                    row['manualMappedSourceCodes'].add(raw_code)
                else:
                    row['autoMappedSourceCodes'].add(raw_code)
            row['units'] += num(sku.get('qty'))
            if consignment_no:
                row['shipments'].add(consignment_no)
            row['accounts'].add(account_label)
            if logistics:
                row['logisticsProducts'][logistics] += 1
            if carrier:
                row['carriers'][carrier] += 1
    for row in grouped.values():
        row['sourceCodes'] = sorted(row['sourceCodes'])
        row['mappedSourceCodes'] = sorted(row['mappedSourceCodes'])
        row['manualMappedSourceCodes'] = sorted(row['manualMappedSourceCodes'])
        row['autoMappedSourceCodes'] = sorted(row['autoMappedSourceCodes'])
        row['mappingRules'] = sorted(row['mappingRules'])
        row['shipments'] = sorted(row['shipments'])
        row['accounts'] = sorted(row['accounts'])
        row['logisticsProducts'] = [{'name': k, 'count': v} for k, v in row['logisticsProducts'].most_common(5)]
        row['carriers'] = [{'name': k, 'count': v} for k, v in row['carriers'].most_common(5)]
    return grouped


def build_combined_product_views(wpj_products, yesterday_orders, cz_inventory, sk_inventory, cz_outbound, sk_outbound, start_dt, end_dt, generated_at, manual_overrides=None, pos_admin_views=None):
    wpj_by_code = {item.get('code'): item for item in wpj_products if item.get('code')}
    order_metrics = collect_wpj_order_product_metrics(yesterday_orders, wpj_by_code, manual_overrides, pos_admin_views)
    cz_inventory_by_code = aggregate_4px_inventory(cz_inventory.get('items') or [], wpj_by_code, manual_overrides)
    sk_inventory_by_code = aggregate_4px_inventory(sk_inventory.get('items') or [], wpj_by_code, manual_overrides)
    cz_outbound_by_code = aggregate_4px_outbound_by_sku(cz_outbound, start_dt, end_dt, 'CZ', wpj_by_code, manual_overrides)
    sk_outbound_by_code = aggregate_4px_outbound_by_sku(sk_outbound, start_dt, end_dt, 'SK', wpj_by_code, manual_overrides)

    all_codes = set(wpj_by_code) | set(cz_inventory_by_code) | set(sk_inventory_by_code) | set(cz_outbound_by_code) | set(sk_outbound_by_code) | set(order_metrics)
    items = []
    auto_mapped_aliases = set()
    manual_mapped_aliases = set()

    for code in sorted(all_codes):
        wpj = wpj_by_code.get(code)
        stores = store_stock_breakdown(wpj) if wpj else []
        has_wpj_4px_context = any((store.get('storeName') or '').startswith('4PX') for store in stores)
        wpj_fourpx_total = round(sum(store['inStore'] for store in stores if (store.get('storeName') or '').startswith('4PX')), 2)
        fourpx_cz = cz_inventory_by_code.get(code, {'availableStock': 0.0, 'pendingStock': 0.0, 'freezeStock': 0.0, 'onwayStock': 0.0, 'batchNos': [], 'skuIds': [], 'sourceCodes': [], 'mappedSourceCodes': [], 'mappingRules': []})
        fourpx_sk = sk_inventory_by_code.get(code, {'availableStock': 0.0, 'pendingStock': 0.0, 'freezeStock': 0.0, 'onwayStock': 0.0, 'batchNos': [], 'skuIds': [], 'sourceCodes': [], 'mappedSourceCodes': [], 'mappingRules': []})
        outbound_cz = cz_outbound_by_code.get(code, {'units': 0.0, 'shipments': [], 'logisticsProducts': [], 'carriers': [], 'name': None, 'accounts': [], 'sourceCodes': [], 'mappedSourceCodes': [], 'mappingRules': []})
        outbound_sk = sk_outbound_by_code.get(code, {'units': 0.0, 'shipments': [], 'logisticsProducts': [], 'carriers': [], 'name': None, 'accounts': [], 'sourceCodes': [], 'mappedSourceCodes': [], 'mappingRules': []})
        sales = order_metrics.get(code, {
            'units': 0.0,
            'revenueWithVat': 0.0,
            'name': None,
            'sourceCodes': [],
            'byView': {
                'complete': {'units': 0.0, 'revenueWithVat': 0.0},
                'cz': {'units': 0.0, 'revenueWithVat': 0.0},
                'sk': {'units': 0.0, 'revenueWithVat': 0.0},
                'ltm': {'units': 0.0, 'revenueWithVat': 0.0},
                'mecin': {'units': 0.0, 'revenueWithVat': 0.0},
            },
        })

        inventory_source_codes = sorted(set(fourpx_cz.get('sourceCodes') or []) | set(fourpx_sk.get('sourceCodes') or []))
        mapped_inventory_codes = sorted(set(fourpx_cz.get('mappedSourceCodes') or []) | set(fourpx_sk.get('mappedSourceCodes') or []))
        manual_inventory_codes = sorted(set(fourpx_cz.get('manualMappedSourceCodes') or []) | set(fourpx_sk.get('manualMappedSourceCodes') or []))
        auto_inventory_codes = sorted(set(fourpx_cz.get('autoMappedSourceCodes') or []) | set(fourpx_sk.get('autoMappedSourceCodes') or []))
        outbound_source_codes = sorted(set(outbound_cz.get('sourceCodes') or []) | set(outbound_sk.get('sourceCodes') or []))
        mapped_outbound_codes = sorted(set(outbound_cz.get('mappedSourceCodes') or []) | set(outbound_sk.get('mappedSourceCodes') or []))
        manual_outbound_codes = sorted(set(outbound_cz.get('manualMappedSourceCodes') or []) | set(outbound_sk.get('manualMappedSourceCodes') or []))
        auto_outbound_codes = sorted(set(outbound_cz.get('autoMappedSourceCodes') or []) | set(outbound_sk.get('autoMappedSourceCodes') or []))
        mapped_source_codes = sorted(set(mapped_inventory_codes) | set(mapped_outbound_codes))
        mapping_rules = sorted(set(fourpx_cz.get('mappingRules') or []) | set(fourpx_sk.get('mappingRules') or []) | set(outbound_cz.get('mappingRules') or []) | set(outbound_sk.get('mappingRules') or []))
        auto_mapped_aliases.update(auto_inventory_codes)
        auto_mapped_aliases.update(auto_outbound_codes)
        manual_mapped_aliases.update(manual_inventory_codes)
        manual_mapped_aliases.update(manual_outbound_codes)

        fourpx_total = round(fourpx_cz['availableStock'] + fourpx_sk['availableStock'], 2)
        stock_delta = round(wpj_fourpx_total - fourpx_total, 2)
        fourpx_relevant = has_wpj_4px_context or fourpx_total > 0 or outbound_cz['units'] > 0 or outbound_sk['units'] > 0
        flags = []
        if wpj and fourpx_total <= 0 and has_wpj_4px_context:
            flags.append('only_in_wpj_4px_context')
        if (not wpj) and fourpx_total > 0:
            flags.append('only_in_4px')
        if fourpx_relevant and wpj and abs(stock_delta) >= 5:
            flags.append('stock_mismatch')
        if fourpx_relevant and sales['units'] > 0 and fourpx_total <= 10:
            flags.append('low_after_sales')
        if (outbound_cz['units'] + outbound_sk['units']) > 0 and not wpj:
            flags.append('shipped_without_wpj_product')
        if mapped_source_codes:
            flags.append('auto_mapped_4px_alias')

        items.append({
            'code': code,
            'title': (wpj or {}).get('title') or sales.get('name') or outbound_cz.get('name') or outbound_sk.get('name') or 'Bez názvu',
            'ean': (wpj or {}).get('ean'),
            'url': (wpj or {}).get('url'),
            'visible': bool((wpj or {}).get('visible')),
            'wpj': {
                'inStore': num((wpj or {}).get('inStore')),
                'fourpxStoreTotal': wpj_fourpx_total,
                'priceWithVat': money(((wpj or {}).get('price') or {}).get('withVat')),
                'stores': stores,
            },
            'fourpx': {
                'cz': fourpx_cz,
                'sk': fourpx_sk,
                'availableTotal': fourpx_total,
                'sourceCodes': inventory_source_codes,
                'mappedSourceCodes': mapped_inventory_codes,
                'manualMappedSourceCodes': manual_inventory_codes,
                'autoMappedSourceCodes': auto_inventory_codes,
                'mappingRules': mapping_rules,
            },
            'yesterdaySales': {
                'units': round(sales['units'], 2),
                'revenueWithVat': round(sales['revenueWithVat'], 2),
                'sourceCodes': sales.get('sourceCodes') or [],
                'czUnits': round(((sales.get('byView') or {}).get('cz') or {}).get('units', 0.0), 2),
                'skUnits': round(((sales.get('byView') or {}).get('sk') or {}).get('units', 0.0), 2),
                'ltmUnits': round(((sales.get('byView') or {}).get('ltm') or {}).get('units', 0.0), 2),
                'mecinUnits': round(((sales.get('byView') or {}).get('mecin') or {}).get('units', 0.0), 2),
                'byView': sales.get('byView') or {},
            },
            'yesterdayOutbound': {
                'czUnits': round(outbound_cz['units'], 2),
                'skUnits': round(outbound_sk['units'], 2),
                'shipments': len(outbound_cz['shipments']) + len(outbound_sk['shipments']),
                'sourceCodes': outbound_source_codes,
                'mappedSourceCodes': mapped_outbound_codes,
                'manualMappedSourceCodes': manual_outbound_codes,
                'autoMappedSourceCodes': auto_outbound_codes,
            },
            'stockDelta': stock_delta,
            'flags': flags,
        })

    low_after_sales = [item for item in items if 'low_after_sales' in item['flags']]
    stock_mismatches = [item for item in items if 'stock_mismatch' in item['flags']]
    only_in_4px = [item for item in items if 'only_in_4px' in item['flags']]
    shipped_yesterday = [item for item in items if item['yesterdayOutbound']['shipments'] > 0]

    priority_shortlist = []
    mapping_suggestions = []
    for item in items:
        if not ({'stock_mismatch', 'only_in_4px', 'only_in_wpj_4px_context'} & set(item['flags'])):
            continue
        score = min(abs(item['stockDelta']) / 50, 40)
        reasons = []
        sales_units = item['yesterdaySales']['units']
        outbound_units = item['yesterdayOutbound']['czUnits'] + item['yesterdayOutbound']['skUnits']
        fourpx_available = item['fourpx']['availableTotal']

        if sales_units > 0:
            score += min(18 + sales_units * 2.5, 28)
            reasons.append(f'včera se prodalo {round(sales_units, 2):g} ks')
        if outbound_units > 0:
            score += min(14 + outbound_units * 1.8, 24)
            reasons.append(f'včera se expedovalo {round(outbound_units, 2):g} ks')
        if 'only_in_4px' in item['flags']:
            score += 14
            reasons.append('existuje jen ve 4PX')
        if 'only_in_wpj_4px_context' in item['flags']:
            score += 12
            reasons.append('WPJ ukazuje 4PX kontext, ale 4PX inventory nesedí')
        if 'low_after_sales' in item['flags']:
            score += 10
            reasons.append('po včerejším prodeji je na nízkém skladu')
        if fourpx_available <= 0:
            score += 8
            reasons.append('4PX available je nula')

        if score >= 65:
            priority = 'critical'
        elif score >= 38:
            priority = 'high'
        else:
            priority = 'medium'

        if 'only_in_4px' in item['flags']:
            action = 'Doplnit nebo opravit vazbu SKU mezi 4PX a WPJ.'
        elif 'only_in_wpj_4px_context' in item['flags']:
            action = 'Zkontrolovat, jestli WPJ 4PX store stav není historický nebo špatně mapovaný.'
        else:
            action = 'Prověřit rozdíl mezi WPJ store stavem a 4PX inventory pull em.'

        priority_shortlist.append({
            'code': item['code'],
            'title': item['title'],
            'priority': priority,
            'score': round(score, 2),
            'reasons': reasons,
            'recommendedAction': action,
            'wpj4pxStock': item['wpj']['fourpxStoreTotal'],
            'fourpxAvailable': fourpx_available,
            'stockDelta': item['stockDelta'],
            'yesterdaySalesUnits': sales_units,
            'yesterdayOutboundUnits': outbound_units,
            'flags': item['flags'],
        })

        mapped_alias_codes = sorted(set(item['fourpx'].get('mappedSourceCodes') or []) | set(item['yesterdayOutbound'].get('mappedSourceCodes') or []))
        manual_alias_codes = set(item['fourpx'].get('manualMappedSourceCodes') or []) | set(item['yesterdayOutbound'].get('manualMappedSourceCodes') or [])
        if mapped_alias_codes and wpj_by_code.get(item['code']):
            for alias_code in mapped_alias_codes:
                mapping_suggestions.append({
                    'orphanCode': alias_code,
                    'orphanTitle': item['title'],
                    'suggestedWpjCode': item['code'],
                    'suggestedWpjTitle': item['title'] or 'Bez názvu',
                    'confidence': 'high',
                    'rule': 'manual_override' if alias_code in manual_alias_codes else 'strip_/variant_suffix',
                    'applied': True,
                    'fourpxAvailable': item['fourpx']['availableTotal'],
                    'yesterdaySalesUnits': sales_units,
                    'yesterdayOutboundUnits': outbound_units,
                })

    low_after_sales.sort(key=lambda item: (item['fourpx']['availableTotal'], -item['yesterdaySales']['units'], item['code']))
    stock_mismatches.sort(key=lambda item: abs(item['stockDelta']), reverse=True)
    only_in_4px.sort(key=lambda item: item['fourpx']['availableTotal'], reverse=True)
    shipped_yesterday.sort(key=lambda item: item['yesterdayOutbound']['czUnits'] + item['yesterdayOutbound']['skUnits'], reverse=True)
    priority_shortlist.sort(key=lambda item: item['score'], reverse=True)
    mapping_suggestions.sort(key=lambda item: (item['yesterdayOutboundUnits'], item['yesterdaySalesUnits'], item['fourpxAvailable']), reverse=True)

    combined_index = {
        'generatedAt': generated_at,
        'window': {'from': start_dt.isoformat(), 'to': end_dt.isoformat()},
        'counts': {
            'allCodes': len(items),
            'pairedProducts': sum(1 for item in items if item['code'] in wpj_by_code and item['fourpx']['availableTotal'] > 0),
            'onlyInWpj': sum(1 for item in items if 'only_in_wpj_4px_context' in item['flags']),
            'onlyIn4px': len(only_in_4px),
            'stockMismatch': len(stock_mismatches),
            'lowAfterSales': len(low_after_sales),
            'manualMapped4pxAliases': len(manual_mapped_aliases),
            'autoMapped4pxAliases': len(auto_mapped_aliases),
        },
        'items': items,
    }

    combined_overview = {
        'generatedAt': generated_at,
        'window': {'from': start_dt.isoformat(), 'to': end_dt.isoformat()},
        'counts': combined_index['counts'],
        'priorityShortlist': priority_shortlist[:25],
        'mappingSuggestions': mapping_suggestions[:50],
        'manualMappedAliases': len(manual_mapped_aliases),
        'autoMappedAliases': len(auto_mapped_aliases),
        'lowAfterSales': low_after_sales[:20],
        'stockMismatches': stock_mismatches[:20],
        'onlyIn4px': only_in_4px[:20],
        'topOutboundProducts': shipped_yesterday[:20],
    }
    return combined_index, combined_overview


def build_inventory_analytics_365d(combined_index, year_orders, start_dt, end_dt, generated_at, wpj_by_code=None, manual_overrides=None, pos_admin_views=None):
    wpj_by_code = wpj_by_code or {}
    metrics = {}
    view_keys = ('complete', 'cz', 'sk', 'ltm', 'mecin')
    for order in year_orders:
        dt = parse_dt(order.get('dateCreated'))
        if not dt:
            continue
        days_ago = (end_dt.date() - dt.date()).days
        view = classify_order_view(order, pos_admin_views)
        for item in order.get('items') or []:
            if item.get('type') != 'product':
                continue
            raw_code = item.get('code') or item.get('name') or '–'
            code, mapping = resolve_4px_code_alias(raw_code, wpj_by_code, manual_overrides)
            row = metrics.setdefault(code, {
                'code': code,
                'name': (wpj_by_code.get(code) or {}).get('title') or item.get('name') or 'Bez názvu',
                'units365d': 0.0,
                'units180d': 0.0,
                'units90d': 0.0,
                'units30d': 0.0,
                'lastSaleDate': None,
                'byView': {
                    key: {'units365d': 0.0, 'units180d': 0.0, 'units90d': 0.0, 'units30d': 0.0}
                    for key in view_keys
                },
            })
            units = num(item.get('pieces'))
            row['units365d'] += units
            row['byView']['complete']['units365d'] += units
            row['byView'][view]['units365d'] += units
            if days_ago <= 180:
                row['units180d'] += units
                row['byView']['complete']['units180d'] += units
                row['byView'][view]['units180d'] += units
            if days_ago <= 90:
                row['units90d'] += units
                row['byView']['complete']['units90d'] += units
                row['byView'][view]['units90d'] += units
            if days_ago <= 30:
                row['units30d'] += units
                row['byView']['complete']['units30d'] += units
                row['byView'][view]['units30d'] += units
            if not row['lastSaleDate'] or dt.isoformat() > row['lastSaleDate']:
                row['lastSaleDate'] = dt.isoformat()

    items = []
    for item in combined_index.get('items') or []:
        code = item['code']
        metric = metrics.get(code, {'units365d': 0.0, 'units180d': 0.0, 'units90d': 0.0, 'units30d': 0.0, 'lastSaleDate': None, 'name': item['title']})
        effective_stock = item['fourpx']['availableTotal'] if item['fourpx']['availableTotal'] > 0 else item['wpj']['fourpxStoreTotal']
        daily_run_rate = metric['units365d'] / 365 if metric['units365d'] else 0.0
        days_of_cover = round(effective_stock / daily_run_rate, 1) if daily_run_rate > 0 else None
        last_sale_dt = parse_dt(metric.get('lastSaleDate'))
        days_since_last_sale = (end_dt.date() - last_sale_dt.date()).days if last_sale_dt else None
        selling_value = round(effective_stock * money(item.get('wpj', {}).get('priceWithVat')), 2) if item.get('wpj', {}).get('priceWithVat') else None

        tags = []
        if effective_stock > 0 and metric['units365d'] == 0:
            tags.append('dead_stock')
        if effective_stock > 0 and days_since_last_sale is not None and days_since_last_sale >= 90:
            tags.append('slow_mover')
        if effective_stock > 0 and days_of_cover is not None and days_of_cover >= 365:
            tags.append('overstocked')
        if effective_stock > 0 and days_of_cover is not None and days_of_cover <= 30 and metric['units365d'] > 0:
            tags.append('fast_mover_low_cover')

        by_view = {}
        for view_key in view_keys:
            view_metric = (metric.get('byView') or {}).get(view_key) or {}
            view_units_365d = round(view_metric.get('units365d', 0.0), 2)
            by_view[view_key] = {
                'units365d': view_units_365d,
                'units180d': round(view_metric.get('units180d', 0.0), 2),
                'units90d': round(view_metric.get('units90d', 0.0), 2),
                'units30d': round(view_metric.get('units30d', 0.0), 2),
                'avgMonthlyUnits365d': round(view_units_365d / 12, 1) if view_units_365d else 0,
            }

        items.append({
            'code': code,
            'title': item['title'],
            'effectiveStock': round(effective_stock, 2),
            'fourpxAvailable': item['fourpx']['availableTotal'],
            'wpj4pxStoreTotal': item['wpj']['fourpxStoreTotal'],
            'units365d': round(metric['units365d'], 2),
            'units180d': round(metric['units180d'], 2),
            'units90d': round(metric['units90d'], 2),
            'units30d': round(metric['units30d'], 2),
            'czUnits365d': by_view['cz']['units365d'],
            'skUnits365d': by_view['sk']['units365d'],
            'ltmUnits365d': by_view['ltm']['units365d'],
            'mecinUnits365d': by_view['mecin']['units365d'],
            'avgMonthlyUnits365d': round(metric['units365d'] / 12, 1) if metric['units365d'] else 0,
            'dailyRunRate365d': round(daily_run_rate, 3),
            'daysOfCover365d': days_of_cover,
            'lastSaleDate': metric['lastSaleDate'],
            'daysSinceLastSale': days_since_last_sale,
            'stockValueSelling': selling_value,
            'tags': tags,
            'byView': by_view,
        })

    turnover = sorted([item for item in items if item['units365d'] > 0 and item['effectiveStock'] > 0], key=lambda item: item['units365d'], reverse=True)
    dead_stock = sorted([item for item in items if 'dead_stock' in item['tags']], key=lambda item: item['effectiveStock'], reverse=True)
    slow_movers = sorted([item for item in items if 'slow_mover' in item['tags'] and item['effectiveStock'] > 0], key=lambda item: ((item['daysSinceLastSale'] or 0), item['effectiveStock']), reverse=True)
    overstocked = sorted([item for item in items if 'overstocked' in item['tags'] and item['effectiveStock'] > 0], key=lambda item: (item['daysOfCover365d'] or 0), reverse=True)
    fast_low_cover = sorted([item for item in items if 'fast_mover_low_cover' in item['tags']], key=lambda item: (item['daysOfCover365d'] or 999, -item['units365d']))

    return {
        'generatedAt': generated_at,
        'window': {'from': start_dt.isoformat(), 'to': end_dt.isoformat()},
        'summary': {
            'trackedItems': len(items),
            'turnoverItems': len(turnover),
            'deadStockItems': len(dead_stock),
            'slowMoverItems': len(slow_movers),
            'overstockedItems': len(overstocked),
            'fastLowCoverItems': len(fast_low_cover),
        },
        'items': items,
        'topTurnover': turnover[:50],
        'deadStock': dead_stock[:100],
        'slowMovers': slow_movers[:100],
        'overstocked': overstocked[:100],
        'fastLowCover': fast_low_cover[:100],
    }


def build_expiry_overview(generated_at, combined_index, cz_expiry_rows, sk_expiry_rows):
    title_by_code = {}
    for row in combined_index.get('items') or []:
        title_by_code[row['code']] = row['title']
        for source_code in row.get('fourpx', {}).get('sourceCodes') or []:
            title_by_code.setdefault(source_code, row['title'])
    combined_rows = []
    for row in (cz_expiry_rows or []) + (sk_expiry_rows or []):
        enriched = dict(row)
        enriched['title'] = title_by_code.get(row['sku']) or row['sku']
        enriched['label'] = f"{row['sku']} · {enriched['title']} ({row['account']})"
        combined_rows.append(enriched)

    combined_rows.sort(key=lambda item: (-item['riskScore'], item['daysToExpiry'], -item['datedStock'], item['sku']))
    return {
        'generatedAt': generated_at,
        'summary': {
            'datedSkuCount': len(combined_rows),
            'czSkuCount': len(cz_expiry_rows or []),
            'skSkuCount': len(sk_expiry_rows or []),
        },
        'topExpiring': combined_rows[:100],
    }


def summarize_4px_window(label, outbound, start_dt, end_dt):
    window_items = []
    for item in outbound.get('items') or []:
        ts = outbound_timestamp(item)
        if ts and start_dt <= ts <= end_dt:
            window_items.append(item)

    carrier_counts = Counter(
        (item.get('carrier_brand_name') or item.get('carrier_code') or item.get('logistics_product_code') or '–')
        for item in window_items
    )
    logistics_counts = Counter((item.get('logistics_product_code') or '–') for item in window_items)
    status_counts = Counter((item.get('status') or '–') for item in window_items)
    coverage_warning = None
    oldest = parse_dt(outbound.get('oldestTimestamp'))
    if outbound.get('hitMaxPages') and oldest and oldest > start_dt:
        coverage_warning = f'{label}: pull pravděpodobně nepokryl celý včerejšek, je potřeba navýšit rozsah stránek.'

    return {
        'label': label,
        'shipments': len(window_items),
        'items': window_items,
        'carrierCounts': [{'name': k, 'count': v} for k, v in carrier_counts.most_common()],
        'logisticsCounts': [{'name': k, 'count': v} for k, v in logistics_counts.most_common()],
        'statusCounts': [{'name': k, 'count': v} for k, v in status_counts.most_common()],
        'coverageWarning': coverage_warning,
    }


def build_alerts(wpj_summary, stock_summary, logistics_summary, warnings):
    alerts = []
    if wpj_summary.get('problematicOrders'):
        alerts.append(f'{wpj_summary["problematicOrders"]} problematických nebo stornovaných objednávek.')
    if stock_summary.get('lowStockSoldYesterday'):
        alerts.append(f'{len(stock_summary["lowStockSoldYesterday"])} včera prodaných produktů je teď na nízkém skladu.')
    if stock_summary.get('negativeStoreStock'):
        alerts.append(f'{len(stock_summary["negativeStoreStock"])} skladových pozic je v mínusu.')
    if logistics_summary.get('coverageWarnings'):
        alerts.extend(logistics_summary['coverageWarnings'])
    if any((row.get('daysToExpiry') is not None and row.get('daysToExpiry') <= 30) for row in (logistics_summary.get('expiringProducts') or [])):
        alerts.append('V top expiracích je aspoň jedna položka do 30 dnů.')
    alerts.extend(warnings)
    deduped = []
    for alert in alerts:
        if alert not in deduped:
            deduped.append(alert)
    return deduped[:3]


def build_priorities(wpj_summary, stock_summary, logistics_summary):
    priorities = []
    for row in stock_summary.get('lowStockSoldYesterday') or []:
        priorities.append(f'Dohlédnout {row["code"]} ({row["title"]}), aktuálně {format_units(row["stock"])}.')
        if len(priorities) >= 2:
            break
    if wpj_summary.get('problematicOrders'):
        priorities.append(f'Projít {wpj_summary["problematicOrders"]} problematických nebo stornovaných objednávek z včerejška.')
    if logistics_summary.get('coverageWarnings'):
        priorities.append('Rozšířit 4PX pull, aby ranní report neřezal starší včerejší zásilky.')
    if not logistics_summary.get('expiringProducts'):
        priorities.append('Dohledat spolehlivý zdroj expirací, 4PX inventory zatím vrací jen batch_no bez data spotřeby.')
    else:
        priorities.append('Projít nejbližší expirace v 4PX a rozhodnout o doprodeji nebo přesunu zásoby.')
    return priorities[:5]


def build_morning_report(report_date, wpj_summary, baseline_orders, baseline_revenue, stock_summary, inventory_summary, logistics_summary, alerts, priorities, warnings):
    orders_delta = pct_delta(wpj_summary['orders'], baseline_orders) if baseline_orders is not None else None
    revenue_delta = pct_delta(wpj_summary['revenueWithVat'], baseline_revenue) if baseline_revenue is not None else None
    quick = {
        'orders': {
            'value': wpj_summary['orders'],
            'baseline': baseline_orders,
            'deltaPct': orders_delta,
        },
        'revenueWithVat': {
            'value': wpj_summary['revenueWithVat'],
            'baseline': baseline_revenue,
            'deltaPct': revenue_delta,
        },
        'shipmentsTotal': logistics_summary['shipmentsTotal'],
        'alerts': alerts,
    }

    report = {
        'generatedAt': current_local_time().isoformat(),
        'reportDate': report_date.isoformat(),
        'detailUrl': os.environ.get('MORNING_REPORT_DETAIL_URL', 'https://rkonfal.github.io/diamond-plus-reporting-preview/site/index.html'),
        'window': {
            'from': datetime(report_date.year, report_date.month, report_date.day, 0, 0, 1, tzinfo=PRAGUE_TZ).isoformat(),
            'to': datetime(report_date.year, report_date.month, report_date.day, 23, 59, 59, tzinfo=PRAGUE_TZ).isoformat(),
        },
        'warnings': warnings,
        'quickSummary': quick,
        'eshop': wpj_summary,
        'stock': stock_summary,
        'inventory': inventory_summary,
        'logistics': logistics_summary,
        'priorities': priorities,
    }
    return report


def top_rows_text(rows, value_key, suffix=''):
    if not rows:
        return ['• data zatím nejsou']
    out = []
    for index, row in enumerate(rows, start=1):
        value = row.get('formatted') or row.get(value_key)
        if isinstance(value, (int, float)):
            value = round(value, 2)
        out.append(f'{index}. {row.get("label") or row.get("name")}: {value}{suffix}')
    return out


def counts_text(rows, empty='• data zatím nejsou'):
    if not rows:
        return [empty]
    return [f'• {row["name"]}: {row["count"]}' for row in rows]


def compact_counts_line(rows, limit=4, empty='bez dat'):
    if not rows:
        return empty
    return ', '.join(f'{row["name"]} {row["count"]}' for row in rows[:limit])


def format_pct_compact(value):
    if value is None:
        return 'bez srovnání'
    return f'{value:+.1f}'.replace('.', ',') + ' % vs 7D'


def compact_alert_text(alert):
    if not alert:
        return ''
    clean = alert.rstrip('.')
    replacements = {
        'problematických nebo stornovaných objednávek': 'problematických / stornovaných objednávek',
        'včera prodaných produktů je teď na nízkém skladu': 'včera prodané produkty jsou teď na nízkém skladu',
        'skladových pozic je v mínusu': 'skladových pozic je v mínusu',
    }
    for source, target in replacements.items():
        clean = clean.replace(source, target)
    return clean


def compact_top_codes(rows, limit=3, value_key='formatted', empty='bez dat'):
    if not rows:
        return empty
    parts = []
    for row in rows[:limit]:
        code = row.get('code') or row.get('sku') or row.get('name') or 'položka'
        value = row.get('formatted') or row.get(value_key)
        if isinstance(value, (int, float)):
            value = round(value, 2)
        parts.append(f'{code} {value}')
    return ', '.join(parts)


def first_method_text(rows, empty='bez dat'):
    if not rows:
        return empty
    row = rows[0]
    return f'{row.get("name", "bez názvu")} ({row.get("count", 0)})'


def source_split_lines(eshop):
    by_view = eshop.get('byView') or {}
    items = [
        ('CZ e-shop', (by_view.get('cz') or {}).get('orders', 0), (by_view.get('cz') or {}).get('revenueWithVat', 0)),
        ('SK e-shop', (by_view.get('sk') or {}).get('orders', 0), (by_view.get('sk') or {}).get('revenueWithVat', 0)),
        ('Litoměřice', (by_view.get('ltm') or {}).get('orders', 0), (by_view.get('ltm') or {}).get('revenueWithVat', 0)),
        ('Měčín', (by_view.get('mecin') or {}).get('orders', 0), (by_view.get('mecin') or {}).get('revenueWithVat', 0)),
    ]
    return [f'• {label}: {orders} objednávek, {format_czk(revenue)}' for label, orders, revenue in items]


def low_stock_line(rows, limit=2):
    if not rows:
        return 'nic kritického po včerejším prodeji'
    return ', '.join(f'{row.get("code", "SKU")} {format_units(row.get("stock", 0))}' for row in rows[:limit])


def negative_positions_line(rows, limit=3):
    if not rows:
        return 'bez mínusových pozic'
    return ', '.join(f'{row.get("code", "SKU")} {format_units(row.get("inStore", 0))}' for row in rows[:limit])


def expiry_line(rows, limit=2):
    if not rows:
        return 'bez kritické expirace v dostupných datech'
    parts = []
    for row in rows[:limit]:
        try:
            expiry = parse_dt(row.get('dateExpiry')).strftime('%-d. %-m.')
        except Exception:
            expiry = row.get('dateExpiry') or 'bez data'
        parts.append(f'{row.get("sku") or row.get("code") or "SKU"} do {expiry} ({format_units(row.get("datedStock", 0))})')
    return ', '.join(parts)


def status_lines(warnings):
    if not warnings:
        return ['✅ WPJ + 4PX kompletní']
    lines = [f'⚠️ {warning}' for warning in warnings[:2]]
    if len(warnings) > 2:
        lines.append(f'⚠️ +{len(warnings) - 2} další upozornění')
    return lines


def compact_priority_text(priority):
    if not priority:
        return ''
    text = priority.strip()
    match = re.match(r'^Dohlédnout\s+([^\s]+)\s+\([^)]*\),\s*(aktuálně\s+.+)$', text)
    if match:
        return f'Dohlédnout {match.group(1)}, {match.group(2)}'
    replacements = {
        'Dohlédnout ': 'Dohlédnout ',
        'Projít 6 problematických nebo stornovaných objednávek z včerejška.': 'Prověřit 6 problémových / stornovaných objednávek.',
        'Projít nejbližší expirace v 4PX a rozhodnout o doprodeji nebo přesunu zásoby.': 'Rozhodnout o doprodeji nebo přesunu nejbližších expirací.',
    }
    return replacements.get(text, text)


def format_morning_report_text(report):
    report_date = parse_dt(report['window']['from']).strftime('%-d. %-m. %Y')
    quick = report['quickSummary']
    eshop = report['eshop']
    stock = report['stock']
    inventory = report.get('inventory') or {}
    logistics = report['logistics']
    warnings = report.get('warnings') or []

    priorities = report.get('priorities') or []
    alerts = [compact_alert_text(alert) for alert in quick.get('alerts') or [] if compact_alert_text(alert)]

    lines = [
        f'**Ranní report, včerejšek ({report_date})**',
        *status_lines(warnings),
        '',
        '**1. Přehled dne**',
        f'• Objednávky: {eshop["orders"]} ({format_pct_compact(quick["orders"].get("deltaPct"))})',
        f'• Tržby s DPH: {format_czk(eshop["revenueWithVat"])} ({format_pct_compact(quick["revenueWithVat"].get("deltaPct"))})',
        f'• Expedice: {logistics["shipmentsTotal"]} zásilek (CZ {logistics["byAccount"].get("CZ", 0)} / SK {logistics["byAccount"].get("SK", 0)})',
        f'• Sklad CZ+SK: {format_units(inventory.get("availableStockTotal", 0))}',
        '',
        '**2. Co dnes pálí**',
    ]
    lines.extend(f'• {item}' for item in (alerts[:3] or ['Bez zásadního ranního alertu.']))
    lines.extend([
        '',
        '**3. E-shop včera**',
        f'• AOV: {format_czk(eshop["averageOrderValue"])}',
        f'• Problematické / storno: {eshop["problematicOrders"]} / {eshop["cancelledOrders"]}',
        f'• Tahouni podle kusů: {compact_top_codes(eshop.get("topProductsByUnits"), 3)}',
        f'• Tahouni podle obratu: {compact_top_codes(eshop.get("topProductsByRevenue"), 3)}',
        f'• Top platba: {first_method_text(eshop.get("paymentMethods"))}',
        f'• Top doprava: {first_method_text(eshop.get("deliveryMethods"))}',
        '• Rozpad zdrojů:',
        *source_split_lines(eshop),
        '',
        '**4. Sklad a logistika**',
        f'• Nízký sklad po včerejším prodeji: {low_stock_line(stock.get("lowStockSoldYesterday"))}',
        f'• Mínusové pozice: {negative_positions_line(stock.get("negativeStoreStock"))}',
        f'• Nejbližší expirace: {expiry_line(logistics.get("expiringProducts"))}',
    ])
    if logistics.get('coverageWarnings'):
        lines.extend(f'• {warning}' for warning in logistics['coverageWarnings'][:2])
    lines.extend([
        '',
        '**5. Co dnes udělat**',
    ])
    lines.extend(f'• {compact_priority_text(item)}' for item in (priorities[:4] or ['Bez nové priority.']))

    detail_url = report.get('detailUrl')
    if detail_url:
        lines.extend([
            '',
            '**6. Detail**',
            f'• {detail_url}',
        ])

    return '\n'.join(lines).strip() + '\n'


def format_morning_report_telegram_text(report):
    report_date = parse_dt(report['window']['from']).strftime('%-d. %-m. %Y')
    quick = report['quickSummary']
    eshop = report['eshop']
    inventory = report.get('inventory') or {}
    logistics = report['logistics']
    warnings = report.get('warnings') or []
    priorities = report.get('priorities') or []
    alerts = [compact_alert_text(alert) for alert in quick.get('alerts') or [] if compact_alert_text(alert)]

    lines = [
        f'Ranní report, včerejšek ({report_date})',
        *status_lines(warnings),
        '',
        '📌 Přehled',
        f'• Objednávky: {eshop["orders"]} ({format_pct_compact(quick["orders"].get("deltaPct"))})',
        f'• Tržby: {format_czk(eshop["revenueWithVat"])} ({format_pct_compact(quick["revenueWithVat"].get("deltaPct"))})',
        f'• Expedice: {logistics["shipmentsTotal"]} (CZ {logistics["byAccount"].get("CZ", 0)} / SK {logistics["byAccount"].get("SK", 0)})',
        f'• Sklad: {format_units(inventory.get("availableStockTotal", 0))}',
        '',
        '⚠️ Co dnes řešit',
    ]
    lines.extend(f'• {item}' for item in (alerts[:3] or ['Bez zásadního ranního alertu.']))
    lines.extend([
        '',
        '✅ Co dnes udělat',
    ])
    lines.extend(f'• {compact_priority_text(item)}' for item in (priorities[:3] or ['Bez nové priority.']))

    top_units = compact_top_codes(eshop.get('topProductsByUnits'), 3)
    if top_units and top_units != 'bez dat':
        lines.extend([
            '',
            f'🛒 Tahouni: {top_units}',
        ])

    lines.extend([
        '',
        '🧭 Zdroje prodeje',
        *source_split_lines(eshop),
    ])

    detail_url = report.get('detailUrl')
    if detail_url:
        lines.extend(['', f'Detail: {detail_url}'])

    return '\n'.join(lines).strip() + '\n'


def clean_html_cell(value):
    return html.unescape(re.sub(r'<[^>]+>', '', value or '')).replace('\xa0', ' ').strip()


def parse_czk_text(value):
    text = clean_html_cell(value)
    text = text.replace('Kč', '').replace(' ', '').replace('\u202f', '').replace('\xa0', '')
    text = text.replace(',', '.')
    if not text:
        return 0.0
    try:
        return round(float(text), 2)
    except ValueError:
        return 0.0


def safe_ratio(value, baseline):
    if not baseline:
        return None
    return round((float(value or 0) / float(baseline or 0)) * 100, 1)


def env_first(*keys):
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None


def abra_config():
    base_url = env_first('ABRA_API_URL', 'FLEXI_API_URL')
    company = env_first('ABRA_COMPANY', 'FLEXI_COMPANY')
    username = env_first('ABRA_USERNAME', 'FLEXI_USERNAME')
    password = env_first('ABRA_PASSWORD', 'FLEXI_PASSWORD')
    return {
        'baseUrl': (base_url or '').rstrip('/'),
        'company': company or '',
        'username': username or '',
        'password': password or '',
        'enabled': all([base_url, company, username, password]),
    }


def abra_text(value):
    if isinstance(value, dict):
        for key in ('showAs', 'value', 'name', 'nazev', 'id', 'code'):
            nested = value.get(key)
            if nested not in (None, ''):
                return str(nested).strip()
        return ''
    return str(value).strip() if value not in (None, '') else ''


def abra_money(value):
    if isinstance(value, dict):
        return abra_money(value.get('value'))
    if value in (None, ''):
        return 0.0
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    text = str(value).strip().replace('Kč', '').replace(' ', '').replace('\xa0', '').replace('\u202f', '')
    text = text.replace(',', '.')
    if not text:
        return 0.0
    try:
        return round(float(text), 2)
    except ValueError:
        return 0.0


def abra_bool(value):
    if isinstance(value, bool):
        return value
    text = abra_text(value).lower()
    if not text:
        return None
    if text in ('true', '1', 'yes', 'ano'):
        return True
    if text in ('false', '0', 'no', 'ne'):
        return False
    return None


def abra_pick(row, *keys):
    for key in keys:
        if key in row and row[key] not in (None, ''):
            return row[key]
    return None


def abra_records(payload, evidence):
    root = (payload or {}).get('winstrom') or {}
    records = root.get(evidence)
    if isinstance(records, list):
        return records
    if isinstance(records, dict):
        return [records]
    for value in root.values():
        if isinstance(value, list):
            return value
    return []


def abra_get(config, evidence, params=None, selector=None):
    if not config.get('enabled'):
        raise RuntimeError('ABRA API není nakonfigurované.')

    query = urlencode({'auth': 'http', **(params or {})}, doseq=True)
    base = f"{config['baseUrl']}/c/{config['company']}/{evidence}"
    if selector:
        base = base + '/' + quote(selector, safe="()'/-")
    url = f'{base}.json'
    if query:
        url = f'{url}?{query}'

    token = base64.b64encode(f"{config['username']}:{config['password']}".encode('utf-8')).decode('ascii')
    request = Request(url, headers={
        'Accept': 'application/json',
        'Authorization': f'Basic {token}',
    })
    with urlopen(request, timeout=60) as response:
        charset = response.headers.get_content_charset() or 'utf-8'
        return json.loads(response.read().decode(charset))


def abra_download(config, path, params=None, accept=None):
    if not config.get('enabled'):
        raise RuntimeError('ABRA API není nakonfigurované.')

    query = urlencode({'auth': 'http', **(params or {})}, doseq=True)
    url = f"{config['baseUrl']}/c/{config['company']}/{path.lstrip('/')}"
    if query:
        url = f'{url}?{query}'

    token = base64.b64encode(f"{config['username']}:{config['password']}".encode('utf-8')).decode('ascii')
    request = Request(url, headers={
        'Authorization': f'Basic {token}',
        'Accept': accept or '*/*',
    })
    with urlopen(request, timeout=120) as response:
        return {
            'url': url,
            'contentType': response.headers.get('Content-Type', ''),
            'body': response.read(),
        }


def parse_abra_vykaz_hospodareni_xls(body, label, month_key):
    if xlrd is None:
        raise RuntimeError('Chybí knihovna xlrd pro čtení XLS exportu.')

    book = xlrd.open_workbook(file_contents=body)
    sheet = book.sheet_by_index(0)
    rows = {}

    for idx in range(sheet.nrows):
        values = sheet.row_values(idx)
        code = str(values[2]).strip() if len(values) > 2 else ''
        if not code:
            continue
        title = str(values[3]).strip() if len(values) > 3 else ''
        rows[code] = {
            'code': code,
            'title': title,
            'month': abra_money(values[5] if len(values) > 5 else 0),
            'year': abra_money(values[7] if len(values) > 7 else 0),
            'included': str(values[12]).strip() if len(values) > 12 else '',
        }

    def month_value(*codes):
        return round(sum((rows.get(code) or {}).get('month', 0.0) for code in codes), 2)

    revenue = month_value('60....')
    marketing = month_value(*LIVE_FINANCE_MARKETING_ACCOUNTS)
    logistics = month_value(*LIVE_FINANCE_LOGISTICS_ACCOUNTS)
    bank_fees = month_value(*LIVE_FINANCE_BANKFEE_ACCOUNTS)
    cogs_and_fees = round(month_value('50....') + bank_fees, 2)
    opex = round(max(month_value('51....') - marketing - logistics, 0.0) + month_value('52....') + month_value('54....'), 2)
    depreciation = month_value('55....')
    profit = month_value('Zisk (+), ztráta (-)')
    gross_margin = round(revenue - cogs_and_fees, 2)
    after_logistics = round(gross_margin - logistics, 2)
    after_marketing = round(after_logistics - marketing, 2)
    operating_margin = round(after_marketing - opex, 2)
    ebit = round(operating_margin - depreciation, 2)
    other = round(profit - ebit, 2)

    return {
        'label': label,
        'monthKey': month_key,
        'reportTitle': str(sheet.cell_value(0, 0)).strip() if sheet.nrows else '',
        'company': str(sheet.cell_value(1, 0)).strip() if sheet.nrows > 1 else '',
        'metrics': {
            'revenue': revenue,
            'cogsAndFees': cogs_and_fees,
            'marketing': marketing,
            'logistics': logistics,
            'opex': opex,
            'depreciation': depreciation,
            'other': other,
            'grossMargin': gross_margin,
            'afterLogistics': after_logistics,
            'afterMarketing': after_marketing,
            'operatingMargin': operating_margin,
            'ebit': ebit,
            'profit': profit,
            'expenseTotal': month_value('5.....'),
            'incomeTotal': month_value('6.....'),
            'bankFees': bank_fees,
        },
        'accounts': {
            code: row for code, row in rows.items()
            if re.fullmatch(r'\d{6}', code)
        },
        'sections': {
            code: row for code, row in rows.items()
            if code.endswith('....') or code == 'Zisk (+), ztráta (-)'
        },
    }


def fetch_abra_vykaz_hospodareni_reports(now_local):
    config = abra_config()
    if not config.get('enabled'):
        return {
            'source': {
                'status': 'missing',
                'message': 'ABRA report endpoint není nakonfigurovaný.',
            },
            'exports': [],
        }

    exports = []
    current_month = month_floor(now_local)
    target_month = datetime(current_month.year, 1, 1, tzinfo=current_month.tzinfo)

    while target_month <= current_month:
        label = month_label(target_month)
        month_key = f'{target_month.month:02d}/{target_month.year}'
        file_name = f'abra_vykaz_hospodareni_{target_month.year}-{target_month.month:02d}.xls'
        params = {
            'report-name': 'vykazHospodareni',
            'ucetniObdobi': str(target_month.year),
            'mesicRok': month_key,
            'mena': 'code:CZK',
        }
        try:
            download = abra_download(config, 'vykaz-hospodareni.xls', params=params, accept='application/vnd.ms-excel, application/octet-stream, */*')
            parsed = parse_abra_vykaz_hospodareni_xls(download['body'], label, month_key)
            exports.append({
                'label': label,
                'monthKey': month_key,
                'fileName': file_name,
                'contentType': download.get('contentType') or '',
                'url': download['url'],
                'bytes': download['body'],
                'parsed': parsed,
            })
        except Exception as exc:
            return {
                'source': {
                    'status': 'error',
                    'message': f'ABRA report Výkaz hospodaření za měsíc se nepodařilo stáhnout ({exc}).',
                },
                'exports': exports,
            }

        target_month = shift_month(target_month, 1)

    return {
        'source': {
            'status': 'live',
            'message': 'Report Výkaz hospodaření za měsíc se tahá přímo z ABRA report endpointu.',
        },
        'exports': exports,
    }


def abra_due_status(due_dt, amount_total, amount_due, now_local):
    if not due_dt:
        status = 'bez splatnosti'
    else:
        delta_days = (due_dt.date() - now_local.date()).days
        if delta_days < 0:
            status = f'{abs(delta_days)} dní PO SPLATNOSTI'
        elif delta_days == 0:
            status = 'splatné dnes'
        else:
            status = f'{delta_days} dní do splatnosti'
    if amount_total and amount_due and amount_due < amount_total:
        paid_pct = round((1 - (amount_due / amount_total)) * 100)
        status += f' ({paid_pct}% uhrazeno)'
    return status


ACCOUNT_CLASS_LABELS = {
    '50': 'Spotřeba a zboží',
    '51': 'Služby',
    '52': 'Mzdy a personální náklady',
    '53': 'Daně a poplatky',
    '54': 'Jiné provozní náklady',
    '55': 'Odpisy a rezervy',
    '56': 'Finanční náklady',
    '57': 'Mimořádné / opravné položky',
    '58': 'Daňové a mimořádné náklady',
    '60': 'Tržby za vlastní výkony a zboží',
    '61': 'Změny stavu zásob / aktivace',
    '64': 'Jiné provozní výnosy',
    '66': 'Finanční výnosy',
}


def month_floor(dt):
    return datetime(dt.year, dt.month, 1, 0, 0, 0, tzinfo=PRAGUE_TZ)


def shift_month(dt, delta_months):
    month_index = (dt.year * 12 + (dt.month - 1)) + delta_months
    year = month_index // 12
    month = (month_index % 12) + 1
    return datetime(year, month, 1, 0, 0, 0, tzinfo=PRAGUE_TZ)


def month_label(dt):
    return f'{dt.month}/{dt.year}'


def abra_account_parts(raw_value, show_as):
    show = abra_text(show_as)
    raw = abra_text(raw_value)
    source = show or raw
    if not source:
        return '', ''
    if ':' in source:
        code, label = source.split(':', 1)
        return code.replace('code', '').replace('=', '').strip(), label.strip()
    return source.strip(), source.strip()


def account_class_label(account_code):
    prefix = (account_code or '')[:2]
    return ACCOUNT_CLASS_LABELS.get(prefix, f'Účet {prefix}xx' if prefix else 'Bez účtu')


def fetch_abra_journal_rows(config, start_dt, end_dt, page_size=2000, max_pages=12):
    selector = f"(datUcto gt '{start_dt.date().isoformat()}' and datUcto lt '{end_dt.date().isoformat()}')"
    rows = []
    for page in range(max_pages):
        chunk = abra_records(abra_get(config, 'ucetni-denik', {
            'detail': 'full',
            'limit': page_size,
            'start': page * page_size,
            'order': 'datUcto@A',
        }, selector=selector), 'ucetni-denik')
        rows.extend(chunk)
        if len(chunk) < page_size:
            break
    return rows


def build_live_journal_snapshot(config, now_local):
    current_start = month_floor(now_local)
    month_starts = [shift_month(current_start, offset) for offset in (-2, -1, 0)]
    monthly = []

    for start_dt in month_starts:
        end_dt = shift_month(start_dt, 1)
        rows = fetch_abra_journal_rows(config, start_dt, end_dt)
        label = month_label(start_dt)
        expense_total = 0.0
        revenue_total = 0.0
        class_totals = defaultdict(float)
        account_totals = defaultdict(float)
        account_labels = {}
        vendor_totals = defaultdict(float)
        month_entries = []

        for row in rows:
            amount = abra_money(abra_pick(row, 'sumTuz', 'sumMen', 'sumMd', 'sumDal', 'amount'))
            if amount <= 0:
                continue

            md_code, md_label = abra_account_parts(row.get('mdUcet'), row.get('mdUcet@showAs'))
            dal_code, dal_label = abra_account_parts(row.get('dalUcet'), row.get('dalUcet@showAs'))
            is_expense = md_code.startswith('5')
            is_revenue = dal_code.startswith('6')

            if is_expense:
                expense_total += amount
                class_totals[md_code[:2]] += amount
                account_totals[md_code] += amount
                account_labels[md_code] = md_label or md_code

            if is_revenue:
                revenue_total += amount

            vendor = abra_text(abra_pick(row, 'nazFirmy', 'firma@showAs'))
            if is_expense and vendor:
                vendor_totals[vendor] += amount

            if is_expense or is_revenue:
                entry_dt = parse_dt(row.get('datUcto'))
                month_entries.append({
                    'month': label,
                    'date': entry_dt.strftime('%d.%m.%Y') if entry_dt else '',
                    'dateSort': entry_dt.isoformat() if entry_dt else '',
                    'document': abra_text(abra_pick(row, 'doklad', 'kod', 'idDokl')) or 'Bez dokladu',
                    'amount': round(amount, 2),
                    'side': 'náklad' if is_expense else 'výnos',
                    'accountCode': md_code if is_expense else dal_code,
                    'accountLabel': md_label if is_expense else dal_label,
                    'counterCode': dal_code if is_expense else md_code,
                    'counterLabel': dal_label if is_expense else md_label,
                    'description': abra_text(abra_pick(row, 'popis', 'nazFirmy', 'firma@showAs', 'varSym')) or 'Bez popisu',
                    'vendor': vendor,
                    'module': abra_text(abra_pick(row, 'modulK@showAs', 'modulK')),
                    'costCenter': abra_text(abra_pick(row, 'stredisko@showAs', 'stredisko')),
                })

        top_class = max(class_totals.items(), key=lambda item: item[1]) if class_totals else None
        top_accounts = []
        for code, amount in sorted(account_totals.items(), key=lambda item: item[1], reverse=True)[:12]:
            top_accounts.append({
                'code': code,
                'label': account_labels.get(code) or code,
                'amount': round(amount, 2),
                'classCode': code[:2],
                'classLabel': account_class_label(code),
            })

        top_classes = []
        for code, amount in sorted(class_totals.items(), key=lambda item: item[1], reverse=True):
            top_classes.append({
                'code': code,
                'label': account_class_label(code),
                'amount': round(amount, 2),
            })

        top_vendors = []
        for name, amount in sorted(vendor_totals.items(), key=lambda item: item[1], reverse=True)[:12]:
            top_vendors.append({
                'name': name,
                'amount': round(amount, 2),
            })

        month_entries = sorted(month_entries, key=lambda row: (row['dateSort'], row['amount']), reverse=True)[:120]
        monthly.append({
            'label': label,
            'expenseTotal': round(expense_total, 2),
            'revenueTotal': round(revenue_total, 2),
            'topExpenseClass': {
                'code': top_class[0],
                'label': account_class_label(top_class[0]),
                'amount': round(top_class[1], 2),
            } if top_class else None,
            'topExpenseAccounts': top_accounts,
            'topExpenseClasses': top_classes,
            'topVendors': top_vendors,
            'recentEntries': month_entries,
        })

    current_month = next((row for row in monthly if row['label'] == month_label(current_start)), None) or {
        'label': month_label(current_start),
        'topExpenseAccounts': [],
        'topExpenseClasses': [],
        'topVendors': [],
        'recentEntries': [],
    }
    return {
        'source': {
            'status': 'live',
            'message': 'Účetní deník pro poslední 3 měsíce je tahán živě z ABRA API a agregovaný do srozumitelnějšího přehledu.',
        },
        'monthly': monthly,
        'currentMonth': current_month,
    }


def fetch_abra_live_snapshot(now_local):
    config = abra_config()
    if not config.get('enabled'):
        return None

    try:
        payload = abra_get(config, 'faktura-prijata', {
            'detail': 'full',
            'limit': 200,
            'order': 'datSplat@D',
        })
    except Exception as exc:
        return {
            'source': {
                'status': 'error',
                'message': f'Živé ABRA API se nepodařilo načíst ({exc}).',
            },
            'cash': {},
        }

    payable_rows = []
    overdue_count = 0
    overdue_amount = 0.0
    unpaid_total = 0.0

    for row in abra_records(payload, 'faktura-prijata'):
        amount_total = abra_money(abra_pick(row, 'sumCelkem', 'celkem', 'sumCelkemMen', 'sumOsv'))
        amount_due = abra_money(abra_pick(row, 'zbyvaUhradit', 'sumZbyvaUhradit', 'sumUhrZbyva', 'castkaZbyva', 'amountDue'))
        paid_flag = abra_bool(abra_pick(row, 'uhrazeno', 'zaplaceno'))
        status_code = abra_text(abra_pick(row, 'stavUhrK', 'stavUhr'))
        status_lower = status_code.lower()

        if paid_flag is True:
            continue
        if status_lower and 'uhrazeno' in status_lower and 'neuhrazeno' not in status_lower and 'cast' not in status_lower and 'část' not in status_lower:
            continue

        if amount_due <= 0:
            if status_lower and ('neuhrazeno' in status_lower or 'po splatnosti' in status_lower or 'do splatnosti' in status_lower):
                amount_due = amount_total
            else:
                continue

        due_dt = parse_dt(abra_pick(row, 'datSplat', 'dueDate'))
        vendor = abra_text(abra_pick(row, 'nazFirmy', 'firma@showAs', 'firma', 'supplier', 'vendor')) or 'Neznámý dodavatel'
        code = abra_text(abra_pick(row, 'kod', 'cisDosle', 'varSym', 'id')) or 'Bez kódu'

        unpaid_total += amount_due
        if due_dt and due_dt.date() < now_local.date():
            overdue_count += 1
            overdue_amount += amount_due

        payable_rows.append({
            'code': code,
            'vendor': vendor,
            'dueDate': due_dt.strftime('%d.%m.%Y') if due_dt else '',
            'amountTotal': round(amount_total, 2),
            'amountDue': round(amount_due, 2),
            'status': abra_due_status(due_dt, amount_total, amount_due, now_local),
        })

    journal = {}
    try:
        journal = build_live_journal_snapshot(config, now_local)
    except Exception as exc:
        journal = {
            'source': {
                'status': 'error',
                'message': f'Live účetní deník se nepodařilo načíst ({exc}).',
            },
            'monthly': [],
            'currentMonth': {
                'label': month_label(month_floor(now_local)),
                'topExpenseAccounts': [],
                'topExpenseClasses': [],
                'topVendors': [],
                'recentEntries': [],
            },
        }

    return {
        'source': {
            'status': 'live_payables',
            'message': 'Závazky z přijatých faktur jsou tahány živě z ABRA API. Měsíční P&L zatím zůstává na legacy snapshotu.',
        },
        'cash': {
            'unpaidInvoices': round(unpaid_total, 2),
            'overdueInvoicesCount': overdue_count,
            'overdueInvoicesAmount': round(overdue_amount, 2),
            'largestPayables': sorted(payable_rows, key=lambda row: row['amountDue'], reverse=True)[:8],
        },
        'journal': journal,
    }


def extract_legacy_abra_model(path: Path):
    if not path.exists():
        return None

    text = path.read_text(encoding='utf-8', errors='ignore')
    start = text.find('const D = ')
    if start == -1:
        return None
    start += len('const D = ')
    end = text.find('};', start)
    if end == -1:
        return None

    model = json.loads(text[start:end + 1])

    def metric(label):
        match = re.search(rf'{re.escape(label)}</div>\s*<div class="insight-value"[^>]*>([^<]+)</div>', text)
        return parse_czk_text(match.group(1)) if match else 0.0

    overdue_match = re.search(r'⚠\s*(\d+) faktury po splatnosti za ([^<]+)</p>', text)
    unpaid_table_match = re.search(r'<!-- Unpaid Invoices Table -->(.*?)</table>', text, re.S)
    unpaid_invoices = []
    if unpaid_table_match:
        row_pattern = re.compile(
            r'<tr[^>]*><td class="category-name">(.*?)</td><td>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td><td[^>]*>(.*?)</td></tr>',
            re.S,
        )
        for code, vendor, due_date, amount_total, amount_due, status in row_pattern.findall(unpaid_table_match.group(1)):
            unpaid_invoices.append({
                'code': clean_html_cell(code),
                'vendor': clean_html_cell(vendor),
                'dueDate': clean_html_cell(due_date),
                'amountTotal': parse_czk_text(amount_total),
                'amountDue': parse_czk_text(amount_due),
                'status': clean_html_cell(status),
            })

    source_note = 'Zatím čerpáno z posledního dostupného ABRA Flexi výřezu v původním dashboardu, ne z živého API.'
    return {
        'source': {
            'status': 'legacy_snapshot',
            'message': source_note,
            'file': str(path),
        },
        'model': model,
        'cash': {
            'cashOnAccounts': metric('Cash na účtech'),
            'unpaidInvoices': metric('Neuhrazené FP'),
            'netCashPosition': metric('Čistá cash pozice'),
            'overdueInvoicesCount': int(overdue_match.group(1)) if overdue_match else 0,
            'overdueInvoicesAmount': parse_czk_text(overdue_match.group(2)) if overdue_match else 0.0,
            'largestPayables': sorted(unpaid_invoices, key=lambda row: row['amountDue'], reverse=True)[:8],
        },
    }


def calc_legacy_finance_series(pnl):
    rev = pnl['rev']
    cogs = [pnl['cogs'][i] + pnl['bankfees'][i] for i in range(len(rev))]
    logistics = [pnl['transport'][i] + pnl['warehouse'][i] for i in range(len(rev))]
    marketing = [pnl['ppc'][i] + pnl['mkt'][i] for i in range(len(rev))]
    opex = [pnl['wages'][i] + pnl['assets'][i] + pnl['overhead'][i] + pnl['software'][i] for i in range(len(rev))]
    depreciation = pnl['depreciation']
    other = [
        pnl['svc_income'][i] + pnl['other_income'][i] + pnl['int_income'][i] + pnl['fx_gain'][i] - pnl['int_cost'][i] - pnl['fx_loss'][i]
        for i in range(len(rev))
    ]
    gross_margin = [rev[i] - cogs[i] for i in range(len(rev))]
    after_logistics = [gross_margin[i] - logistics[i] for i in range(len(rev))]
    after_marketing = [after_logistics[i] - marketing[i] for i in range(len(rev))]
    operating_margin = [after_marketing[i] - opex[i] for i in range(len(rev))]
    ebit = [operating_margin[i] - depreciation[i] for i in range(len(rev))]
    profit = [ebit[i] + other[i] for i in range(len(rev))]
    return {
        'revenue': rev,
        'cogsAndFees': cogs,
        'logistics': logistics,
        'marketing': marketing,
        'opex': opex,
        'depreciation': depreciation,
        'other': other,
        'grossMargin': gross_margin,
        'afterLogistics': after_logistics,
        'afterMarketing': after_marketing,
        'operatingMargin': operating_margin,
        'ebit': ebit,
        'profit': profit,
    }


def build_finance_snapshot(legacy_abra_payload, live_abra_payload, report_payload, generated_at):
    report_rows = [row.get('parsed') for row in (report_payload or {}).get('exports') or [] if row.get('parsed')]
    if report_rows:
        months = [row['label'] for row in report_rows]
        monthly = []
        for row in report_rows:
            metrics = row.get('metrics') or {}
            revenue = metrics.get('revenue', 0)
            monthly.append({
                'label': row['label'],
                'revenue': round(revenue, 2),
                'cogsAndFees': round(metrics.get('cogsAndFees', 0), 2),
                'logistics': round(metrics.get('logistics', 0), 2),
                'marketing': round(metrics.get('marketing', 0), 2),
                'opex': round(metrics.get('opex', 0), 2),
                'depreciation': round(metrics.get('depreciation', 0), 2),
                'other': round(metrics.get('other', 0), 2),
                'grossMargin': round(metrics.get('grossMargin', 0), 2),
                'afterLogistics': round(metrics.get('afterLogistics', 0), 2),
                'afterMarketing': round(metrics.get('afterMarketing', 0), 2),
                'operatingMargin': round(metrics.get('operatingMargin', 0), 2),
                'ebit': round(metrics.get('ebit', 0), 2),
                'profit': round(metrics.get('profit', 0), 2),
                'grossMarginPct': safe_ratio(metrics.get('grossMargin', 0), revenue),
                'marketingPct': safe_ratio(metrics.get('marketing', 0), revenue),
                'logisticsPct': safe_ratio(metrics.get('logistics', 0), revenue),
                'operatingMarginPct': safe_ratio(metrics.get('operatingMargin', 0), revenue),
                'profitPct': safe_ratio(metrics.get('profit', 0), revenue),
            })
        source = {
            'status': 'live_report',
            'message': 'Měsíční finance se tahají přímo z ABRA reportu Výkaz hospodaření za měsíc za všechna střediska.',
        }
        cash = dict(legacy_abra_payload.get('cash') or {}) if legacy_abra_payload else {}
    elif legacy_abra_payload:
        model = legacy_abra_payload['model']
        series = calc_legacy_finance_series(model['pnl_all'])
        months = model.get('months') or []
        monthly = []
        for i, label in enumerate(months):
            monthly.append({
                'label': label,
                'revenue': round(series['revenue'][i], 2),
                'cogsAndFees': round(series['cogsAndFees'][i], 2),
                'logistics': round(series['logistics'][i], 2),
                'marketing': round(series['marketing'][i], 2),
                'opex': round(series['opex'][i], 2),
                'depreciation': round(series['depreciation'][i], 2),
                'other': round(series['other'][i], 2),
                'grossMargin': round(series['grossMargin'][i], 2),
                'afterLogistics': round(series['afterLogistics'][i], 2),
                'afterMarketing': round(series['afterMarketing'][i], 2),
                'operatingMargin': round(series['operatingMargin'][i], 2),
                'ebit': round(series['ebit'][i], 2),
                'profit': round(series['profit'][i], 2),
                'grossMarginPct': safe_ratio(series['grossMargin'][i], series['revenue'][i]),
                'marketingPct': safe_ratio(series['marketing'][i], series['revenue'][i]),
                'logisticsPct': safe_ratio(series['logistics'][i], series['revenue'][i]),
                'operatingMarginPct': safe_ratio(series['operatingMargin'][i], series['revenue'][i]),
                'profitPct': safe_ratio(series['profit'][i], series['revenue'][i]),
            })
        source = dict(legacy_abra_payload['source'])
        cash = dict(legacy_abra_payload.get('cash') or {})
    else:
        months = []
        monthly = []
        source = {'status': 'missing', 'message': 'ABRA finance report nebyl nalezen.'}
        cash = {}
    journal = {
        'source': {'status': 'missing', 'message': 'Live účetní deník zatím není k dispozici.'},
        'monthly': [],
        'currentMonth': {
            'label': '',
            'topExpenseAccounts': [],
            'topExpenseClasses': [],
            'recentEntries': [],
        },
    }

    if live_abra_payload:
        live_status = live_abra_payload.get('source', {}).get('status')
        if live_status == 'live_payables':
            live_cash = live_abra_payload.get('cash') or {}
            cash.update(live_cash)
            journal = live_abra_payload.get('journal') or journal
            if report_rows:
                has_live_cash_position = live_cash.get('cashOnAccounts') is not None and live_cash.get('netCashPosition') is not None
                if has_live_cash_position:
                    source = {
                        'status': 'live_report',
                        'message': 'Měsíční finance se tahají přímo z ABRA reportu Výkaz hospodaření za měsíc za všechna střediska. Cash a závazky jsou také live z ABRA API.',
                    }
                elif cash.get('cashOnAccounts') is not None and cash.get('netCashPosition') is not None:
                    source = {
                        'status': 'mixed_live_legacy',
                        'message': 'Měsíční finance se tahají přímo z ABRA reportu Výkaz hospodaření za měsíc za všechna střediska. Závazky jsou live z ABRA API, cash na účtech a čistá cash pozice zatím zůstávají z posledního ABRA snapshotu.',
                    }
                else:
                    source = {
                        'status': 'live_report',
                        'message': 'Měsíční finance se tahají přímo z ABRA reportu Výkaz hospodaření za měsíc za všechna střediska. Závazky jsou live z ABRA API.',
                    }
            else:
                source = {
                    'status': 'mixed_live_legacy' if legacy_abra_payload else 'live_payables_only',
                    'message': live_abra_payload['source']['message'],
                }
        elif live_status == 'error' and legacy_abra_payload:
            source = {
                'status': 'legacy_with_live_error',
                'message': f"{legacy_abra_payload['source']['message']} Live ABRA adapter selhal: {live_abra_payload['source']['message']}",
            }
        elif live_status == 'error':
            source = dict(live_abra_payload['source'])

    current_month = monthly[-1] if monthly else {}
    previous_month = monthly[-2] if len(monthly) > 1 else None
    return {
        'generatedAt': generated_at,
        'source': source,
        'months': months,
        'monthly': monthly,
        'currentMonth': current_month,
        'previousMonth': previous_month,
        'cash': cash,
        'journal': journal,
    }


def build_marketing_snapshot(legacy_abra_payload, report_payload, finance_snapshot, generated_at):
    sklik_overview = load_optional_current_json('sklik_overview.json') or {}
    sklik_current = ((sklik_overview.get('currentMonth') or {}).get('total') or {})
    sklik_direct = {
        'ready': bool(sklik_overview),
        'label': 'Sklik',
        'source': (sklik_overview.get('source') or {}).get('status'),
        'account': sklik_overview.get('account') or {},
        'currentMonth': sklik_overview.get('currentMonth') or {},
        'campaignSummary': sklik_overview.get('campaignSummary') or {},
        'campaignsCurrentMonth': sklik_overview.get('campaignPerformanceCurrentMonth') or [],
        'topCampaigns': sorted(
            sklik_overview.get('campaignPerformanceCurrentMonth') or [],
            key=lambda row: float(row.get('priceCzk') or 0),
            reverse=True,
        )[:5],
    }
    meta_overview = load_optional_current_json('meta_ads_overview.json') or {}
    meta_summary = meta_overview.get('summary') or {}
    meta_direct = {
        'ready': bool(meta_overview),
        'label': 'Meta Ads',
        'source': (meta_overview.get('source') or {}).get('status'),
        'accounts': meta_overview.get('accounts') or [],
        'currentMonth': meta_summary,
        'campaignsCurrentMonth': meta_overview.get('campaignsCurrentMonth') or [],
        'topCampaigns': meta_overview.get('topCampaignsCurrentMonth') or [],
        'dailySummary': meta_overview.get('dailySummary') or [],
    }
    google_overview = load_optional_current_json('google_ads_overview.json') or {}
    google_summary = google_overview.get('summary') or {}
    google_direct = {
        'ready': bool(google_overview),
        'label': 'Google Ads',
        'source': (google_overview.get('source') or {}).get('status'),
        'accounts': google_overview.get('accounts') or [],
        'currentMonth': google_summary,
        'campaignsCurrentMonth': google_overview.get('campaignsCurrentMonth') or [],
        'topCampaigns': google_overview.get('topCampaignsCurrentMonth') or [],
        'dailySummary': google_overview.get('dailySummary') or [],
    }
    klaviyo_overview = load_optional_current_json('klaviyo_overview.json') or {}
    klaviyo_current = klaviyo_overview.get('currentMonth') or {}
    klaviyo_direct = {
        'ready': bool(klaviyo_overview),
        'label': 'Klaviyo',
        'source': (klaviyo_overview.get('source') or {}).get('status'),
        'account': klaviyo_overview.get('account') or {},
        'currentMonth': klaviyo_current,
        'dailySummary': klaviyo_overview.get('dailySummary') or [],
        'flowsCurrentMonth': klaviyo_overview.get('flowsCurrentMonth') or [],
        'topFlows': klaviyo_overview.get('topFlowsCurrentMonth') or [],
        'recentCampaigns': klaviyo_overview.get('recentCampaigns') or [],
    }

    active_campaigns = {
        'sklik': sorted(
            [row for row in (sklik_direct.get('campaignsCurrentMonth') or []) if str(row.get('status') or '').lower() == 'active'],
            key=lambda row: float(row.get('priceCzk') or 0),
            reverse=True,
        ),
        'meta': sorted(
            [row for row in (meta_direct.get('campaignsCurrentMonth') or []) if str(row.get('effectiveStatus') or row.get('status') or '').upper() == 'ACTIVE'],
            key=lambda row: float(row.get('spendCzk') or 0),
            reverse=True,
        ),
        'google': sorted(
            [row for row in (google_direct.get('campaignsCurrentMonth') or []) if str(row.get('status') or '').upper() == 'ENABLED'],
            key=lambda row: float(row.get('spendCzk') or 0),
            reverse=True,
        ),
    }
    report_rows = [row.get('parsed') for row in (report_payload or {}).get('exports') or [] if row.get('parsed')]
    if report_rows:
        monthly = []
        revenue_by_label = {row.get('label'): row for row in (finance_snapshot.get('monthly') or [])}
        for row in report_rows:
            accounts = row.get('accounts') or {}
            performance_spend = round((accounts.get('518900') or {}).get('month', 0), 2)
            brand_spend = round((accounts.get('518901') or {}).get('month', 0), 2)
            total_spend = round(performance_spend + brand_spend, 2)
            revenue = (revenue_by_label.get(row['label']) or {}).get('revenue', 0)
            monthly.append({
                'label': row['label'],
                'performanceSpend': performance_spend,
                'brandSpend': brand_spend,
                'totalSpend': total_spend,
                'revenue': revenue,
                'spendShareOfRevenuePct': safe_ratio(total_spend, revenue),
            })

        journal_current = (finance_snapshot.get('journal') or {}).get('currentMonth') or {}
        current_entries = [
            {
                'date': row.get('date'),
                'supplier': row.get('vendor'),
                'description': row.get('description'),
                'amount': round(float(row.get('amount') or 0), 2),
                'module': row.get('module'),
                'account': row.get('accountCode'),
                'costCenter': row.get('costCenter'),
            }
            for row in (journal_current.get('recentEntries') or [])
            if row.get('accountCode') in LIVE_FINANCE_MARKETING_ACCOUNTS
        ]
        supplier_totals = defaultdict(float)
        for row in current_entries:
            supplier_totals[row.get('supplier') or 'Neznámý dodavatel'] += float(row.get('amount') or 0)

        current_month = monthly[-1] if monthly else {}
        direct_sources = {'sklik': sklik_direct, 'meta': meta_direct, 'google': google_direct, 'klaviyo': klaviyo_direct}
        channel_rows = []
        if sklik_direct['ready']:
            channel_rows.append({
                'name': 'Sklik',
                'amount': round(float(sklik_current.get('priceCzk') or 0), 2),
                'clicks': int(sklik_current.get('clicks') or 0),
                'conversions': round(float(sklik_current.get('conversions') or 0), 2),
                'roas': None,
                'source': 'live_api',
            })
        if meta_direct['ready']:
            channel_rows.append({
                'name': 'Meta Ads',
                'amount': round(float(meta_summary.get('spendCzk') or 0), 2),
                'clicks': int(meta_summary.get('clicks') or 0),
                'conversions': round(float(meta_summary.get('purchaseConversions') or 0), 2),
                'roas': meta_summary.get('roas'),
                'source': 'live_api',
            })
        if google_direct['ready']:
            channel_rows.append({
                'name': 'Google Ads',
                'amount': round(float(google_summary.get('spendCzk') or 0), 2),
                'clicks': int(google_summary.get('clicks') or 0),
                'conversions': round(float(google_summary.get('conversions') or 0), 2),
                'roas': google_summary.get('roas'),
                'source': 'live_api',
            })
        if klaviyo_direct['ready']:
            channel_rows.append({
                'name': 'Klaviyo',
                'amount': round(float(klaviyo_current.get('totalAttributedRevenueCzk') or 0), 2),
                'clicks': int(klaviyo_current.get('totalClicks') or 0),
                'conversions': round(float(klaviyo_current.get('totalAttributedOrders') or 0), 2),
                'roas': None,
                'source': 'live_api',
            })
        source_message = 'Marketing se skládá z live ABRA reportu a aktuálních položek z účetního deníku.'
        live_labels = []
        if sklik_direct['ready']:
            live_labels.append('Sklik')
        if meta_direct['ready']:
            live_labels.append('Meta Ads')
        if google_direct['ready']:
            live_labels.append('Google Ads')
        if klaviyo_direct['ready']:
            live_labels.append('Klaviyo')
        if live_labels:
            source_message += ' Přímé platformy přes API: ' + ', '.join(live_labels) + '.'
        return {
            'generatedAt': generated_at,
            'source': {'status': 'live_report', 'message': source_message},
            'monthly': monthly,
            'currentMonth': current_month,
            'topSuppliersCurrentMonth': [
                {'name': name, 'amount': round(amount, 2)}
                for name, amount in sorted(supplier_totals.items(), key=lambda item: item[1], reverse=True)[:8]
            ],
            'entriesCurrentMonth': current_entries[:20],
            'directSources': direct_sources,
            'channelsCurrentMonth': channel_rows,
            'activeCampaignsBySource': active_campaigns,
        }

    if not legacy_abra_payload:
        return {
            'generatedAt': generated_at,
            'source': {'status': 'missing', 'message': 'Legacy marketing snapshot nebyl nalezen.'},
            'monthly': [],
            'currentMonth': {},
            'topSuppliersCurrentMonth': [],
            'entriesCurrentMonth': [],
            'directSources': {'sklik': sklik_direct, 'meta': meta_direct, 'google': google_direct, 'klaviyo': klaviyo_direct},
            'channelsCurrentMonth': [],
            'activeCampaignsBySource': active_campaigns,
        }

    model = legacy_abra_payload['model']
    marketing_group = next((group for group in model.get('groups') or [] if group.get('id') == 'marketing'), None)
    if not marketing_group:
        return {
            'generatedAt': generated_at,
            'source': {'status': 'missing', 'message': 'Marketing skupina ve legacy ABRA modelu chybí.'},
            'monthly': [],
            'currentMonth': {},
            'topSuppliersCurrentMonth': [],
            'entriesCurrentMonth': [],
            'directSources': {'sklik': sklik_direct, 'meta': meta_direct, 'google': google_direct, 'klaviyo': klaviyo_direct},
            'channelsCurrentMonth': [],
            'activeCampaignsBySource': active_campaigns,
        }

    accounts = {account['acc']: account for account in marketing_group.get('accounts') or []}
    ppc_account = accounts.get('518900', {})
    brand_account = accounts.get('518901', {})
    monthly = []
    for index, month_key in enumerate(LEGACY_MONTH_KEYS, start=1):
        revenue = ((finance_snapshot.get('monthly') or [{}] * (index + 1))[index]).get('revenue', 0)
        performance_spend = round(sum(row['amount'] for row in ppc_account.get(month_key) or []), 2)
        brand_spend = round(sum(row['amount'] for row in brand_account.get(month_key) or []), 2)
        total_spend = round(performance_spend + brand_spend, 2)
        monthly.append({
            'label': model['months'][index] if len(model.get('months') or []) > index else month_key,
            'performanceSpend': performance_spend,
            'brandSpend': brand_spend,
            'totalSpend': total_spend,
            'revenue': revenue,
            'spendShareOfRevenuePct': safe_ratio(total_spend, revenue),
        })

    current_month_key = LEGACY_MONTH_KEYS[-1]
    current_entries = sorted(
        (ppc_account.get(current_month_key) or []) + (brand_account.get(current_month_key) or []),
        key=lambda row: row.get('amount', 0),
        reverse=True,
    )
    supplier_totals = defaultdict(float)
    for row in current_entries:
        supplier_totals[row.get('company') or 'Neznámý dodavatel'] += float(row.get('amount') or 0)

    current_month = monthly[-1] if monthly else {}
    channel_rows = []
    if sklik_direct['ready']:
        channel_rows.append({
            'name': 'Sklik',
            'amount': round(float(sklik_current.get('priceCzk') or 0), 2),
            'clicks': int(sklik_current.get('clicks') or 0),
            'conversions': round(float(sklik_current.get('conversions') or 0), 2),
            'roas': None,
            'source': 'live_api',
        })
    if meta_direct['ready']:
        channel_rows.append({
            'name': 'Meta Ads',
            'amount': round(float(meta_summary.get('spendCzk') or 0), 2),
            'clicks': int(meta_summary.get('clicks') or 0),
            'conversions': round(float(meta_summary.get('purchaseConversions') or 0), 2),
            'roas': meta_summary.get('roas'),
            'source': 'live_api',
        })
    if google_direct['ready']:
        channel_rows.append({
            'name': 'Google Ads',
            'amount': round(float(google_summary.get('spendCzk') or 0), 2),
            'clicks': int(google_summary.get('clicks') or 0),
            'conversions': round(float(google_summary.get('conversions') or 0), 2),
            'roas': google_summary.get('roas'),
            'source': 'live_api',
        })
    if klaviyo_direct['ready']:
        channel_rows.append({
            'name': 'Klaviyo',
            'amount': round(float(klaviyo_current.get('totalAttributedRevenueCzk') or 0), 2),
            'clicks': int(klaviyo_current.get('totalClicks') or 0),
            'conversions': round(float(klaviyo_current.get('totalAttributedOrders') or 0), 2),
            'roas': None,
            'source': 'live_api',
        })
    return {
        'generatedAt': generated_at,
        'source': legacy_abra_payload['source'],
        'monthly': monthly,
        'currentMonth': current_month,
        'topSuppliersCurrentMonth': [
            {'name': name, 'amount': round(amount, 2)}
            for name, amount in sorted(supplier_totals.items(), key=lambda item: item[1], reverse=True)[:8]
        ],
        'entriesCurrentMonth': [
            {
                'date': row.get('date'),
                'supplier': row.get('company'),
                'description': row.get('desc'),
                'amount': round(float(row.get('amount') or 0), 2),
                'module': row.get('module'),
                'account': row.get('md'),
                'costCenter': row.get('stredisko'),
            }
            for row in current_entries[:20]
        ],
        'directSources': {'sklik': sklik_direct, 'meta': meta_direct, 'google': google_direct, 'klaviyo': klaviyo_direct},
        'channelsCurrentMonth': channel_rows,
        'activeCampaignsBySource': active_campaigns,
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
            'oldestTimestamp': outbound.get('oldestTimestamp'),
            'newestTimestamp': outbound.get('newestTimestamp'),
        },
    }


def latest_snapshot_dir():
    latest = SNAPSHOT_DIR / 'latest'
    if latest.is_symlink():
        target = latest.resolve()
        if target.exists():
            return target
    if latest.exists() and latest.is_dir():
        return latest
    return None


def load_previous_snapshot_json(name):
    prev_dir = latest_snapshot_dir()
    if not prev_dir:
        return None
    path = prev_dir / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def load_optional_current_json(name):
    path = CURRENT_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def ensure_daily_sklik_snapshot(now_local):
    token = (os.environ.get('SKLIK_API_TOKEN') or '').strip()
    if not token:
        return {'ready': False, 'reason': 'missing_token'}

    snapshot_path = CURRENT_DIR / 'sklik_overview.json'
    if snapshot_path.exists():
        modified_at = datetime.fromtimestamp(snapshot_path.stat().st_mtime, PRAGUE_TZ)
        if modified_at.date() == now_local.date():
            return {'ready': True, 'refreshed': False, 'path': str(snapshot_path)}

    script_path = ROOT / 'scripts' / 'fetch_sklik.py'
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except Exception as exc:
        return {'ready': False, 'reason': 'run_error', 'message': str(exc)}

    if result.returncode != 0:
        message = (result.stderr or result.stdout or '').strip()
        return {'ready': False, 'reason': 'fetch_failed', 'message': message[:500]}

    return {'ready': True, 'refreshed': True, 'path': str(snapshot_path)}


def ensure_daily_meta_snapshot(now_local):
    token = (os.environ.get('META_ACCESS_TOKEN') or '').strip()
    account_ids = (os.environ.get('META_AD_ACCOUNT_IDS') or '').strip()
    if not token or not account_ids:
        return {'ready': False, 'reason': 'missing_token'}

    snapshot_path = CURRENT_DIR / 'meta_ads_overview.json'
    if snapshot_path.exists():
        modified_at = datetime.fromtimestamp(snapshot_path.stat().st_mtime, PRAGUE_TZ)
        if modified_at.date() == now_local.date():
            return {'ready': True, 'refreshed': False, 'path': str(snapshot_path)}

    script_path = ROOT / 'scripts' / 'fetch_meta_ads.py'
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except Exception as exc:
        return {'ready': False, 'reason': 'run_error', 'message': str(exc)}

    if result.returncode != 0:
        message = (result.stderr or result.stdout or '').strip()
        return {'ready': False, 'reason': 'fetch_failed', 'message': message[:500]}

    return {'ready': True, 'refreshed': True, 'path': str(snapshot_path)}


def ensure_daily_google_snapshot(now_local):
    required = [
        (os.environ.get('GOOGLE_ADS_DEVELOPER_TOKEN') or '').strip(),
        (os.environ.get('GOOGLE_ADS_LOGIN_CUSTOMER_ID') or '').strip(),
        (os.environ.get('GOOGLE_ADS_OAUTH_CLIENT_ID') or '').strip(),
        (os.environ.get('GOOGLE_ADS_OAUTH_CLIENT_SECRET') or '').strip(),
        (os.environ.get('GOOGLE_ADS_REFRESH_TOKEN') or '').strip(),
    ]
    if not all(required):
        return {'ready': False, 'reason': 'missing_token'}

    snapshot_path = CURRENT_DIR / 'google_ads_overview.json'
    if snapshot_path.exists():
        modified_at = datetime.fromtimestamp(snapshot_path.stat().st_mtime, PRAGUE_TZ)
        if modified_at.date() == now_local.date():
            return {'ready': True, 'refreshed': False, 'path': str(snapshot_path)}

    script_path = ROOT / 'scripts' / 'fetch_google_ads.py'
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except Exception as exc:
        return {'ready': False, 'reason': 'run_error', 'message': str(exc)}

    if result.returncode != 0:
        message = (result.stderr or result.stdout or '').strip()
        return {'ready': False, 'reason': 'fetch_failed', 'message': message[:500]}

    return {'ready': True, 'refreshed': True, 'path': str(snapshot_path)}


def ensure_daily_klaviyo_snapshot(now_local):
    token = (os.environ.get('KLAVIYO_PRIVATE_API_KEY') or '').strip()
    if not token:
        return {'ready': False, 'reason': 'missing_token'}

    snapshot_path = CURRENT_DIR / 'klaviyo_overview.json'
    if snapshot_path.exists():
        modified_at = datetime.fromtimestamp(snapshot_path.stat().st_mtime, PRAGUE_TZ)
        if modified_at.date() == now_local.date():
            return {'ready': True, 'refreshed': False, 'path': str(snapshot_path)}

    script_path = ROOT / 'scripts' / 'fetch_klaviyo.py'
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except Exception as exc:
        return {'ready': False, 'reason': 'run_error', 'message': str(exc)}

    if result.returncode != 0:
        message = (result.stderr or result.stdout or '').strip()
        return {'ready': False, 'reason': 'fetch_failed', 'message': message[:500]}

    return {'ready': True, 'refreshed': True, 'path': str(snapshot_path)}


def main():
    load_env_file(ENV_FILE)
    manual_overrides = load_manual_sku_overrides(SKU_MAPPING_OVERRIDE_FILE)
    pos_admin_views = load_pos_admin_view_overrides(POS_ADMIN_VIEW_OVERRIDE_FILE)
    warehouse_code = os.environ.get('FOURPX_WAREHOUSE_CODE', 'CZPRGA')
    max_pages = int(os.environ.get('FOURPX_OUTBOUND_MAX_PAGES', '20'))
    now_local = current_local_time()
    stamp = now_local.strftime('%Y%m%d-%H%M%S')
    generated_at = now_local.isoformat()
    report_start, report_end = previous_day_window(now_local)
    report_date = report_start.date()

    required = [
        'FOURPX_CZ_APP_KEY', 'FOURPX_CZ_APP_SECRET',
        'FOURPX_SK_APP_KEY', 'FOURPX_SK_APP_SECRET',
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise SystemExit(f'Missing required env keys: {", ".join(missing)}')

    previous_wpj_products = None
    previous_snapshot = load_previous_snapshot_json('wpj_products.json')
    if previous_snapshot:
        previous_wpj_products = previous_snapshot.get('items') or []

    cz_inventory = fetch_inventory(os.environ['FOURPX_CZ_APP_KEY'], os.environ['FOURPX_CZ_APP_SECRET'], warehouse_code)
    sk_inventory = fetch_inventory(os.environ['FOURPX_SK_APP_KEY'], os.environ['FOURPX_SK_APP_SECRET'], warehouse_code)
    cz_inventory_detail = fetch_inventory_details(os.environ['FOURPX_CZ_APP_KEY'], os.environ['FOURPX_CZ_APP_SECRET'], warehouse_code, cz_inventory['items'])
    sk_inventory_detail = fetch_inventory_details(os.environ['FOURPX_SK_APP_KEY'], os.environ['FOURPX_SK_APP_SECRET'], warehouse_code, sk_inventory['items'])
    cz_expiry_summary = summarize_expiry_details('CZ', cz_inventory_detail['items'])
    sk_expiry_summary = summarize_expiry_details('SK', sk_inventory_detail['items'])
    cz_outbound = fetch_recent_outbound(
        os.environ['FOURPX_CZ_APP_KEY'],
        os.environ['FOURPX_CZ_APP_SECRET'],
        warehouse_code,
        max_pages=max_pages,
        stop_before=report_start,
    )
    sk_outbound = fetch_recent_outbound(
        os.environ['FOURPX_SK_APP_KEY'],
        os.environ['FOURPX_SK_APP_SECRET'],
        warehouse_code,
        max_pages=max_pages,
        stop_before=report_start,
    )

    warnings = []
    wpj_ready = bool(wpj_endpoint() and os.environ.get('WPJ_ACCESS_TOKEN'))
    wpj_summary = {
        'orders': 0,
        'revenueWithVat': 0,
        'averageOrderValue': 0,
        'cancelledOrders': 0,
        'problematicOrders': 0,
        'statuses': [],
        'paymentMethods': [],
        'deliveryMethods': [],
        'topProductsByUnits': [],
        'topProductsByRevenue': [],
        'soldProductCodes': [],
    }
    wpj_orders_payload = {'generatedAt': generated_at, 'items': []}
    wpj_products_payload = {'generatedAt': generated_at, 'items': []}
    wpj_history_payload = {'generatedAt': generated_at, 'days': []}
    inventory_analytics_payload = {'generatedAt': generated_at, 'summary': {}, 'topTurnover': [], 'deadStock': [], 'slowMovers': [], 'overstocked': [], 'fastLowCover': []}
    expiry_overview_payload = {'generatedAt': generated_at, 'summary': {}, 'topExpiring': []}
    combined_index_payload = {'generatedAt': generated_at, 'items': [], 'counts': {}}
    combined_overview_payload = {'generatedAt': generated_at, 'counts': {}}
    legacy_abra_payload = extract_legacy_abra_model(LEGACY_ABRA_HTML)
    live_abra_payload = fetch_abra_live_snapshot(now_local)
    abra_vykaz_hospodareni_reports = fetch_abra_vykaz_hospodareni_reports(now_local)
    sklik_status = ensure_daily_sklik_snapshot(now_local)
    meta_status = ensure_daily_meta_snapshot(now_local)
    google_status = ensure_daily_google_snapshot(now_local)
    klaviyo_status = ensure_daily_klaviyo_snapshot(now_local)
    finance_snapshot = build_finance_snapshot(legacy_abra_payload, live_abra_payload, abra_vykaz_hospodareni_reports, generated_at)
    marketing_snapshot = build_marketing_snapshot(legacy_abra_payload, abra_vykaz_hospodareni_reports, finance_snapshot, generated_at)
    baseline_orders = None
    baseline_revenue = None
    stock_summary = {
        'lowStockSoldYesterday': [],
        'lowStockOverall': [],
        'negativeStoreStock': [],
        'largestMovesSinceLastSnapshot': [],
    }

    if wpj_ready:
        wpj_url = wpj_endpoint()
        wpj_token = os.environ['WPJ_ACCESS_TOKEN']
        history_start = report_start - timedelta(days=7)
        year_start = report_start - timedelta(days=364)
        history_orders = fetch_wpj_orders(wpj_url, wpj_token, history_start, report_end, limit=1000, detailed=False)
        yesterday_orders = fetch_wpj_orders(wpj_url, wpj_token, report_start, report_end, limit=250, detailed=True)
        wpj_products = fetch_wpj_products(wpj_url, wpj_token)

        wpj_summary = summarize_orders(yesterday_orders, pos_admin_views=pos_admin_views)
        history_days, baseline_orders, baseline_revenue = summarize_daily_history(history_orders, report_date, pos_admin_views=pos_admin_views)
        stock_summary = summarize_stock(wpj_products, wpj_summary['soldProductCodes'], previous_products=previous_wpj_products)
        combined_index_payload, combined_overview_payload = build_combined_product_views(
            wpj_products,
            yesterday_orders,
            cz_inventory,
            sk_inventory,
            cz_outbound,
            sk_outbound,
            report_start,
            report_end,
            generated_at,
            manual_overrides,
            pos_admin_views,
        )
        expiry_overview_payload = build_expiry_overview(generated_at, combined_index_payload, cz_expiry_summary, sk_expiry_summary)

        analytics_cache_path = CURRENT_DIR / 'inventory_analytics_365d.json'
        inventory_analytics_payload = load_json_if_fresh(analytics_cache_path, max_age_hours=24)
        if not inventory_analytics_payload or not inventory_analytics_payload.get('items'):
            year_orders = fetch_wpj_year_order_metrics(wpj_url, wpj_token, year_start, report_end, limit=1000)
            inventory_analytics_payload = build_inventory_analytics_365d(
                combined_index_payload,
                year_orders,
                year_start,
                report_end,
                generated_at,
                {item.get('code'): item for item in wpj_products if item.get('code')},
                manual_overrides,
                pos_admin_views,
            )

        wpj_orders_payload = {
            'generatedAt': generated_at,
            'window': {'from': report_start.isoformat(), 'to': report_end.isoformat()},
            'items': yesterday_orders,
            'summary': wpj_summary,
        }
        wpj_products_payload = {'generatedAt': generated_at, 'items': wpj_products}
        wpj_history_payload = {
            'generatedAt': generated_at,
            'window': {'from': history_start.isoformat(), 'to': report_end.isoformat()},
            'days': history_days,
        }
    else:
        warnings.append('WPJ část není připojená, ranní report nebude mít e-shop výkon.')

    if finance_snapshot.get('source', {}).get('status') == 'legacy_with_live_error':
        warnings.append('ABRA live adapter selhal, finance fallbacknuly na legacy snapshot.')
    if abra_vykaz_hospodareni_reports.get('source', {}).get('status') == 'error':
        warnings.append('ABRA report Výkaz hospodaření za měsíc se nepodařilo stáhnout.')
    if not sklik_status.get('ready') and sklik_status.get('reason') != 'missing_token':
        warnings.append('Sklik denní refresh selhal, marketing používá poslední dostupný snapshot.')
    if not meta_status.get('ready') and meta_status.get('reason') != 'missing_token':
        warnings.append('Meta Ads denní refresh selhal, marketing používá poslední dostupný snapshot.')
    if not google_status.get('ready') and google_status.get('reason') != 'missing_token':
        warnings.append('Google Ads denní refresh selhal, marketing používá poslední dostupný snapshot.')
    if not klaviyo_status.get('ready') and klaviyo_status.get('reason') != 'missing_token':
        warnings.append('Klaviyo denní refresh selhal, marketing používá poslední dostupný snapshot.')

    if not expiry_overview_payload.get('topExpiring'):
        expiry_overview_payload = build_expiry_overview(generated_at, combined_index_payload, cz_expiry_summary, sk_expiry_summary)

    cz_daily = summarize_4px_window('CZ', cz_outbound, report_start, report_end)
    sk_daily = summarize_4px_window('SK', sk_outbound, report_start, report_end)
    inventory_summary = {
        'availableStockTotal': round(cz_inventory['availableStockTotal'] + sk_inventory['availableStockTotal'], 2),
        'itemsTotal': len(cz_inventory['items']) + len(sk_inventory['items']),
        'byAccount': {
            'CZ': round(cz_inventory['availableStockTotal'], 2),
            'SK': round(sk_inventory['availableStockTotal'], 2),
        },
        'itemsByAccount': {
            'CZ': len(cz_inventory['items']),
            'SK': len(sk_inventory['items']),
        },
    }
    logistics_summary = {
        'shipmentsTotal': cz_daily['shipments'] + sk_daily['shipments'],
        'byAccount': {'CZ': cz_daily['shipments'], 'SK': sk_daily['shipments']},
        'carrierCounts': [
            {'name': name, 'count': count}
            for name, count in (Counter({}) + Counter({row['name']: row['count'] for row in cz_daily['carrierCounts']}) + Counter({row['name']: row['count'] for row in sk_daily['carrierCounts']})).most_common()
        ],
        'logisticsCounts': [
            {'name': name, 'count': count}
            for name, count in (Counter({}) + Counter({row['name']: row['count'] for row in cz_daily['logisticsCounts']}) + Counter({row['name']: row['count'] for row in sk_daily['logisticsCounts']})).most_common()
        ],
        'statusCounts': [
            {'name': name, 'count': count}
            for name, count in (Counter({}) + Counter({row['name']: row['count'] for row in cz_daily['statusCounts']}) + Counter({row['name']: row['count'] for row in sk_daily['statusCounts']})).most_common()
        ],
        'coverageWarnings': [warning for warning in [cz_daily['coverageWarning'], sk_daily['coverageWarning']] if warning],
        'expiringProducts': (expiry_overview_payload.get('topExpiring') or [])[:5],
        'notes': [],
    }
    warnings.extend(logistics_summary['coverageWarnings'])

    alerts = build_alerts(wpj_summary, stock_summary, logistics_summary, warnings)
    priorities = build_priorities(wpj_summary, stock_summary, logistics_summary)

    report_json = build_morning_report(
        report_date,
        wpj_summary,
        baseline_orders,
        baseline_revenue,
        stock_summary,
        inventory_summary,
        logistics_summary,
        alerts,
        priorities,
        warnings,
    )
    report_text = format_morning_report_text(report_json)
    report_telegram_text = format_morning_report_telegram_text(report_json)

    CURRENT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / stamp
    snapshot_path.mkdir(parents=True, exist_ok=True)

    payloads = {
        '4px_cz_inventory.json': {'generatedAt': generated_at, **cz_inventory},
        '4px_sk_inventory.json': {'generatedAt': generated_at, **sk_inventory},
        '4px_cz_inventory_detail.json': {'generatedAt': generated_at, **cz_inventory_detail},
        '4px_sk_inventory_detail.json': {'generatedAt': generated_at, **sk_inventory_detail},
        '4px_cz_outbound_recent.json': {'generatedAt': generated_at, **cz_outbound},
        '4px_sk_outbound_recent.json': {'generatedAt': generated_at, **sk_outbound},
        '4px_expiry_overview.json': expiry_overview_payload,
        'combined_product_index.json': combined_index_payload,
        'combined_inventory_overview.json': combined_overview_payload,
        'inventory_analytics_365d.json': inventory_analytics_payload,
        'finance_overview.json': finance_snapshot,
        'marketing_overview.json': marketing_snapshot,
        'wpj_orders_previous_day.json': wpj_orders_payload,
        'wpj_products.json': wpj_products_payload,
        'wpj_history_8_days.json': wpj_history_payload,
        'morning_report_previous_day.json': report_json,
    }

    for name, payload in payloads.items():
        write_json(CURRENT_DIR / name, payload)
        write_json(snapshot_path / name, payload)

    report_manifest = {
        'generatedAt': generated_at,
        'source': abra_vykaz_hospodareni_reports.get('source') or {},
        'months': [
            {
                'label': row.get('label'),
                'monthKey': row.get('monthKey'),
                'fileName': row.get('fileName'),
                'contentType': row.get('contentType'),
                'url': row.get('url'),
                'parsed': (row.get('parsed') or {}).get('metrics') or {},
            }
            for row in (abra_vykaz_hospodareni_reports.get('exports') or [])
        ],
    }
    write_json(CURRENT_DIR / 'abra_vykaz_hospodareni_reports.json', report_manifest)
    write_json(snapshot_path / 'abra_vykaz_hospodareni_reports.json', report_manifest)
    for row in (abra_vykaz_hospodareni_reports.get('exports') or []):
        body = row.get('bytes')
        if not body:
            continue
        write_bytes(CURRENT_DIR / row['fileName'], body)
        write_bytes(snapshot_path / row['fileName'], body)

    write_text(CURRENT_DIR / 'morning_report_previous_day.txt', report_text)
    write_text(snapshot_path / 'morning_report_previous_day.txt', report_text)
    write_text(CURRENT_DIR / 'morning_report_previous_day_telegram.txt', report_telegram_text)
    write_text(snapshot_path / 'morning_report_previous_day_telegram.txt', report_telegram_text)

    portal_summary = {
        'generatedAt': generated_at,
        'config': {
            'warehouseCode': warehouse_code,
            'outboundMaxPages': max_pages,
            'reportWindow': {
                'from': report_start.isoformat(),
                'to': report_end.isoformat(),
            },
        },
        'warnings': warnings,
        'accounts': {
            'cz': account_payload('CZ', cz_inventory, cz_outbound),
            'sk': account_payload('SK', sk_inventory, sk_outbound),
        },
        'wpJ': {
            'ready': wpj_ready,
            'message': 'WPJ připojeno a ranní report je vygenerovaný.' if wpj_ready else 'WPJ zatím není připojené. Chybí token nebo URL.',
            'orders': wpj_summary['orders'],
            'revenueWithVat': wpj_summary['revenueWithVat'],
            'averageOrderValue': wpj_summary['averageOrderValue'],
            'problematicOrders': wpj_summary['problematicOrders'],
        },
        'report': {
            'date': report_date.isoformat(),
            'shipments': logistics_summary['shipmentsTotal'],
            'alerts': alerts,
            'priorities': priorities,
        },
        'expiries': expiry_overview_payload.get('summary') or {},
        'pairing': combined_overview_payload.get('counts') or {},
        'finance': {
            'ready': finance_snapshot.get('source', {}).get('status') != 'missing',
            'mode': finance_snapshot.get('source', {}).get('status'),
            'message': finance_snapshot.get('source', {}).get('message'),
            'currentMonth': finance_snapshot.get('currentMonth') or {},
            'cash': finance_snapshot.get('cash') or {},
            'reportExport': abra_vykaz_hospodareni_reports.get('source') or {},
        },
        'marketing': {
            'ready': marketing_snapshot.get('source', {}).get('status') != 'missing',
            'mode': marketing_snapshot.get('source', {}).get('status'),
            'message': marketing_snapshot.get('source', {}).get('message'),
            'currentMonth': marketing_snapshot.get('currentMonth') or {},
            'topSupplier': (marketing_snapshot.get('topSuppliersCurrentMonth') or [None])[0],
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
    print(f'WPJ previous-day orders: {wpj_summary["orders"]} | Revenue with VAT: {wpj_summary["revenueWithVat"]}')
    print(f'Morning report file: {CURRENT_DIR / "morning_report_previous_day.txt"}')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise
