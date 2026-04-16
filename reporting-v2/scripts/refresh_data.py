#!/usr/bin/env python3
import hashlib
import html
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / '.env.local'
CURRENT_DIR = ROOT / 'data' / 'current'
SNAPSHOT_DIR = ROOT / 'data' / 'snapshots'
BASE_URL = 'https://open.eu.4px.com/router/api/service'
PRAGUE_TZ = ZoneInfo('Europe/Prague')
LEGACY_ABRA_HTML = ROOT.parent / 'portals' / 'diamond-plus-report' / 'index.html'
LEGACY_MONTH_KEYS = ['jan', 'feb', 'mar']

WPJ_ORDERS_HISTORY_QUERY = '''
query ($offset:Int,$limit:Int,$sort:OrderSortInput,$filter:OrderFilterInput){
  orders(offset:$offset, limit:$limit, sort:$sort, filter:$filter) {
    items {
      id
      code
      dateCreated
      cancelled
      isPaid
      status { id name }
      totalPrice { withVat withoutVat }
    }
    hasNextPage
    hasPreviousPage
  }
}
'''

WPJ_ORDERS_DETAIL_QUERY = '''
query ($offset:Int,$limit:Int,$sort:OrderSortInput,$filter:OrderFilterInput){
  orders(offset:$offset, limit:$limit, sort:$sort, filter:$filter) {
    items {
      id
      code
      dateCreated
      cancelled
      isPaid
      status { id name }
      totalPrice { withVat withoutVat }
      deliveryType {
        id
        delivery { id name }
        payment { id name type }
        price { withVat }
      }
      items {
        type
        productId
        code
        name
        ean
        pieces
        totalPrice { withVat withoutVat }
      }
    }
    hasNextPage
    hasPreviousPage
  }
}
'''

WPJ_ORDERS_YEAR_METRICS_QUERY = '''
query ($offset:Int,$limit:Int,$sort:OrderSortInput,$filter:OrderFilterInput){
  orders(offset:$offset, limit:$limit, sort:$sort, filter:$filter) {
    items {
      id
      dateCreated
      items {
        type
        code
        name
        pieces
      }
    }
    hasNextPage
    hasPreviousPage
  }
}
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
    return f'{round(float(value or 0), 1):g} ks'


def previous_day_window(now_local: datetime):
    target_date = now_local.date() - timedelta(days=1)
    start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=PRAGUE_TZ)
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


def summarize_orders(orders):
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
        revenue += money((order.get('totalPrice') or {}).get('withVat'))
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
            product_revenue[key] += money((item.get('totalPrice') or {}).get('withVat'))

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
    return {
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


def summarize_daily_history(orders, target_date):
    by_day = defaultdict(list)
    for order in orders:
        dt = parse_dt(order.get('dateCreated'))
        if not dt:
            continue
        by_day[dt.date()].append(order)

    days = []
    for index in range(7, -1, -1):
        day = target_date - timedelta(days=index)
        summary = summarize_orders(by_day.get(day, []))
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


def collect_wpj_order_product_metrics(orders):
    metrics = {}
    for order in orders:
        for item in order.get('items') or []:
            if item.get('type') != 'product':
                continue
            code = item.get('code') or str(item.get('productId') or item.get('name') or '–')
            row = metrics.setdefault(code, {
                'code': item.get('code') or code,
                'name': item.get('name') or 'Bez názvu',
                'units': 0.0,
                'revenueWithVat': 0.0,
            })
            row['units'] += num(item.get('pieces'))
            row['revenueWithVat'] += money((item.get('totalPrice') or {}).get('withVat'))
    return metrics


def aggregate_4px_inventory(items):
    grouped = {}
    for item in items or []:
        code = (item.get('sku_code') or '').strip()
        if not code:
            continue
        row = grouped.setdefault(code, {
            'code': code,
            'skuIds': set(),
            'batchNos': set(),
            'availableStock': 0.0,
            'pendingStock': 0.0,
            'freezeStock': 0.0,
            'onwayStock': 0.0,
        })
        if item.get('sku_id'):
            row['skuIds'].add(item['sku_id'])
        if item.get('batch_no'):
            row['batchNos'].add(item['batch_no'])
        row['availableStock'] += num(item.get('available_stock'))
        row['pendingStock'] += num(item.get('pending_stock'))
        row['freezeStock'] += num(item.get('freeze_stock'))
        row['onwayStock'] += num(item.get('onway_stock'))
    for row in grouped.values():
        row['skuIds'] = sorted(row['skuIds'])
        row['batchNos'] = sorted(row['batchNos'])
    return grouped


def aggregate_4px_outbound_by_sku(outbound_payload, start_dt, end_dt, account_label):
    grouped = {}
    for consignment in outbound_payload.get('items') or []:
        ts = outbound_timestamp(consignment)
        if not ts or ts < start_dt or ts > end_dt:
            continue
        consignment_no = consignment.get('consignment_no') or ''
        logistics = consignment.get('logistics_product_code') or ''
        carrier = consignment.get('carrier_brand_name') or consignment.get('carrier_code') or ''
        for sku in consignment.get('outboundlist_sku') or []:
            code = (sku.get('sku_code') or '').strip()
            if not code:
                continue
            row = grouped.setdefault(code, {
                'code': code,
                'name': sku.get('sku_name') or 'Bez názvu',
                'units': 0.0,
                'shipments': set(),
                'accounts': set(),
                'logisticsProducts': Counter(),
                'carriers': Counter(),
            })
            row['units'] += num(sku.get('qty'))
            if consignment_no:
                row['shipments'].add(consignment_no)
            row['accounts'].add(account_label)
            if logistics:
                row['logisticsProducts'][logistics] += 1
            if carrier:
                row['carriers'][carrier] += 1
    for row in grouped.values():
        row['shipments'] = sorted(row['shipments'])
        row['accounts'] = sorted(row['accounts'])
        row['logisticsProducts'] = [{'name': k, 'count': v} for k, v in row['logisticsProducts'].most_common(5)]
        row['carriers'] = [{'name': k, 'count': v} for k, v in row['carriers'].most_common(5)]
    return grouped


def build_combined_product_views(wpj_products, yesterday_orders, cz_inventory, sk_inventory, cz_outbound, sk_outbound, start_dt, end_dt, generated_at):
    wpj_by_code = {item.get('code'): item for item in wpj_products if item.get('code')}
    order_metrics = collect_wpj_order_product_metrics(yesterday_orders)
    cz_inventory_by_code = aggregate_4px_inventory(cz_inventory.get('items') or [])
    sk_inventory_by_code = aggregate_4px_inventory(sk_inventory.get('items') or [])
    cz_outbound_by_code = aggregate_4px_outbound_by_sku(cz_outbound, start_dt, end_dt, 'CZ')
    sk_outbound_by_code = aggregate_4px_outbound_by_sku(sk_outbound, start_dt, end_dt, 'SK')

    all_codes = set(wpj_by_code) | set(cz_inventory_by_code) | set(sk_inventory_by_code) | set(cz_outbound_by_code) | set(sk_outbound_by_code) | set(order_metrics)
    items = []

    for code in sorted(all_codes):
        wpj = wpj_by_code.get(code)
        stores = store_stock_breakdown(wpj) if wpj else []
        has_wpj_4px_context = any((store.get('storeName') or '').startswith('4PX') for store in stores)
        wpj_fourpx_total = round(sum(store['inStore'] for store in stores if (store.get('storeName') or '').startswith('4PX')), 2)
        fourpx_cz = cz_inventory_by_code.get(code, {'availableStock': 0.0, 'pendingStock': 0.0, 'freezeStock': 0.0, 'onwayStock': 0.0, 'batchNos': [], 'skuIds': []})
        fourpx_sk = sk_inventory_by_code.get(code, {'availableStock': 0.0, 'pendingStock': 0.0, 'freezeStock': 0.0, 'onwayStock': 0.0, 'batchNos': [], 'skuIds': []})
        outbound_cz = cz_outbound_by_code.get(code, {'units': 0.0, 'shipments': [], 'logisticsProducts': [], 'carriers': [], 'name': None, 'accounts': []})
        outbound_sk = sk_outbound_by_code.get(code, {'units': 0.0, 'shipments': [], 'logisticsProducts': [], 'carriers': [], 'name': None, 'accounts': []})
        sales = order_metrics.get(code, {'units': 0.0, 'revenueWithVat': 0.0, 'name': None})

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
            },
            'yesterdaySales': {
                'units': round(sales['units'], 2),
                'revenueWithVat': round(sales['revenueWithVat'], 2),
            },
            'yesterdayOutbound': {
                'czUnits': round(outbound_cz['units'], 2),
                'skUnits': round(outbound_sk['units'], 2),
                'shipments': len(outbound_cz['shipments']) + len(outbound_sk['shipments']),
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

        if 'only_in_4px' in item['flags'] and '/' in item['code']:
            base_code = item['code'].split('/')[0]
            wpj_candidate = wpj_by_code.get(base_code)
            if wpj_candidate:
                mapping_suggestions.append({
                    'orphanCode': item['code'],
                    'orphanTitle': item['title'],
                    'suggestedWpjCode': base_code,
                    'suggestedWpjTitle': wpj_candidate.get('title') or 'Bez názvu',
                    'confidence': 'high',
                    'rule': 'strip_/variant_suffix',
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
        },
        'items': items,
    }

    combined_overview = {
        'generatedAt': generated_at,
        'window': {'from': start_dt.isoformat(), 'to': end_dt.isoformat()},
        'counts': combined_index['counts'],
        'priorityShortlist': priority_shortlist[:25],
        'mappingSuggestions': mapping_suggestions[:50],
        'lowAfterSales': low_after_sales[:20],
        'stockMismatches': stock_mismatches[:20],
        'onlyIn4px': only_in_4px[:20],
        'topOutboundProducts': shipped_yesterday[:20],
    }
    return combined_index, combined_overview


def build_inventory_analytics_365d(combined_index, year_orders, start_dt, end_dt, generated_at):
    metrics = {}
    for order in year_orders:
        dt = parse_dt(order.get('dateCreated'))
        if not dt:
            continue
        days_ago = (end_dt.date() - dt.date()).days
        for item in order.get('items') or []:
            if item.get('type') != 'product':
                continue
            code = item.get('code') or item.get('name') or '–'
            row = metrics.setdefault(code, {
                'code': code,
                'name': item.get('name') or 'Bez názvu',
                'units365d': 0.0,
                'units180d': 0.0,
                'units90d': 0.0,
                'units30d': 0.0,
                'lastSaleDate': None,
            })
            units = num(item.get('pieces'))
            row['units365d'] += units
            if days_ago <= 180:
                row['units180d'] += units
            if days_ago <= 90:
                row['units90d'] += units
            if days_ago <= 30:
                row['units30d'] += units
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
            'avgMonthlyUnits365d': round(metric['units365d'] / 12, 1) if metric['units365d'] else 0,
            'dailyRunRate365d': round(daily_run_rate, 3),
            'daysOfCover365d': days_of_cover,
            'lastSaleDate': metric['lastSaleDate'],
            'daysSinceLastSale': days_since_last_sale,
            'stockValueSelling': selling_value,
            'tags': tags,
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
        'topTurnover': turnover[:50],
        'deadStock': dead_stock[:100],
        'slowMovers': slow_movers[:100],
        'overstocked': overstocked[:100],
        'fastLowCover': fast_low_cover[:100],
    }


def build_expiry_overview(generated_at, combined_index, cz_expiry_rows, sk_expiry_rows):
    title_by_code = {row['code']: row['title'] for row in combined_index.get('items') or []}
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


def build_morning_report(report_date, wpj_summary, baseline_orders, baseline_revenue, stock_summary, logistics_summary, alerts, priorities, warnings):
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
        'window': {
            'from': datetime(report_date.year, report_date.month, report_date.day, 0, 0, 0, tzinfo=PRAGUE_TZ).isoformat(),
            'to': datetime(report_date.year, report_date.month, report_date.day, 23, 59, 59, tzinfo=PRAGUE_TZ).isoformat(),
        },
        'warnings': warnings,
        'quickSummary': quick,
        'eshop': wpj_summary,
        'stock': stock_summary,
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


def format_morning_report_text(report):
    report_date = parse_dt(report['window']['from']).strftime('%-d. %-m. %Y')
    quick = report['quickSummary']
    eshop = report['eshop']
    stock = report['stock']
    logistics = report['logistics']
    warnings = report.get('warnings') or []

    header = [f'**Ranní report, včerejšek ({report_date})**']
    if warnings:
        header.extend([f'⚠️ {warning}' for warning in warnings])

    orders_line = f'• Objednávky: {eshop["orders"]}'
    if quick['orders']['baseline'] is not None:
        delta = quick['orders']['deltaPct']
        sign = '+' if delta and delta > 0 else ''
        orders_line += f' ({sign}{delta:.1f} % vs 7denní průměr {quick["orders"]["baseline"]:.1f})'

    revenue_line = f'• Tržby s DPH: {format_czk(eshop["revenueWithVat"])}'
    if quick['revenueWithVat']['baseline'] is not None:
        delta = quick['revenueWithVat']['deltaPct']
        sign = '+' if delta and delta > 0 else ''
        revenue_line += f' ({sign}{delta:.1f} % vs 7denní průměr {format_czk(quick["revenueWithVat"]["baseline"])})'

    sections = []
    sections.append('\n'.join([
        *header,
        '',
        '**1. Rychlý souhrn**',
        orders_line,
        revenue_line,
        f'• Expedice 4PX: {logistics["shipmentsTotal"]} zásilek (CZ {logistics["byAccount"].get("CZ", 0)}, SK {logistics["byAccount"].get("SK", 0)})',
        *( [f'• Alert: {alert}' for alert in quick['alerts']] if quick['alerts'] else ['• Alerty: bez zásadního varování'] ),
    ]))

    section2 = [
        '**2. E-shop výkon za včerejšek**',
        f'• Počet objednávek: {eshop["orders"]}',
        f'• Obrat s DPH: {format_czk(eshop["revenueWithVat"])}',
        f'• Průměrná hodnota objednávky: {format_czk(eshop["averageOrderValue"])}',
        f'• Stornované / problematické: {eshop["cancelledOrders"]} / {eshop["problematicOrders"]}',
        '• Top 5 prodaných produktů podle kusů:',
        *top_rows_text(eshop['topProductsByUnits'], 'formatted'),
        '• Top 5 produktů podle obratu:',
        *top_rows_text(eshop['topProductsByRevenue'], 'formatted'),
        '• Platební metody:',
        *counts_text(eshop['paymentMethods'][:5]),
        '• Dopravní metody:',
        *counts_text(eshop['deliveryMethods'][:5]),
    ]
    sections.append('\n'.join(section2))

    section3 = [
        '**3. Sklad a dostupnost**',
        '• Produkty s nízkým skladem z včera prodaných:',
    ]
    if stock['lowStockSoldYesterday']:
        for row in stock['lowStockSoldYesterday']:
            section3.append(f'• {row["code"]} · {row["title"]}: {format_units(row["stock"])}')
    else:
        section3.append('• žádný včera prodaný produkt teď není na hraně ≤ 10 ks')
    section3.append('• Produkty do mínusu / rezervované nad fyzický stav:')
    if stock['negativeStoreStock']:
        for row in stock['negativeStoreStock'][:5]:
            section3.append(f'• {row["code"]} · {row["title"]} / {row["storeName"]}: {format_units(row["inStore"])}')
    else:
        section3.append('• bez záporných skladových pozic')
    section3.append('• Největší pohyby od posledního snapshotu:')
    if stock['largestMovesSinceLastSnapshot']:
        for row in stock['largestMovesSinceLastSnapshot'][:5]:
            sign = '+' if row['delta'] > 0 else ''
            section3.append(f'• {row["code"]} · {row["title"]}: {sign}{format_units(row["delta"])}')
    else:
        section3.append('• baseline zatím chybí, první srovnání vznikne po dalším refreshi')
    sections.append('\n'.join(section3))

    section4 = [
        '**4. 4PX logistika za včerejšek**',
        f'• Počet zásilek celkem: {logistics["shipmentsTotal"]}',
        f'• Rozpad podle účtu: CZ {logistics["byAccount"].get("CZ", 0)} / SK {logistics["byAccount"].get("SK", 0)}',
        '• Rozpad podle dopravce:',
        *counts_text(logistics['carrierCounts'][:5]),
        '• 5 produktů s nejvyšším expiračním rizikem:',
    ]
    if logistics['expiringProducts']:
        for row in logistics['expiringProducts'][:5]:
            section4.append(f'• {row["label"]}: expirace {row["dateExpiry"]}, expirující sklad {format_units(row["datedStock"])}')
    else:
        section4.append('• zatím bez dat, dostupné zdroje vrací batch_no bez data expirace')
    if logistics['coverageWarnings']:
        section4.extend(f'• {warning}' for warning in logistics['coverageWarnings'])
    sections.append('\n'.join(section4))

    section5 = ['**5. Dnešní priority**']
    section5.extend(f'• {item}' for item in report.get('priorities') or ['Bez nové priority.'])
    sections.append('\n\n'.join(['', '\n'.join(section5)]).strip())

    return '\n\n'.join(sections).strip() + '\n'


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


def build_finance_snapshot(legacy_abra_payload, generated_at):
    if not legacy_abra_payload:
        return {
            'generatedAt': generated_at,
            'source': {'status': 'missing', 'message': 'Legacy ABRA snapshot nebyl nalezen.'},
            'months': [],
            'monthly': [],
            'currentMonth': {},
            'cash': {},
        }

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

    current_month = monthly[-1] if monthly else {}
    previous_month = monthly[-2] if len(monthly) > 1 else None
    return {
        'generatedAt': generated_at,
        'source': legacy_abra_payload['source'],
        'months': months,
        'monthly': monthly,
        'currentMonth': current_month,
        'previousMonth': previous_month,
        'cash': legacy_abra_payload.get('cash') or {},
    }


def build_marketing_snapshot(legacy_abra_payload, finance_snapshot, generated_at):
    if not legacy_abra_payload:
        return {
            'generatedAt': generated_at,
            'source': {'status': 'missing', 'message': 'Legacy marketing snapshot nebyl nalezen.'},
            'monthly': [],
            'currentMonth': {},
            'topSuppliersCurrentMonth': [],
            'entriesCurrentMonth': [],
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


def main():
    load_env_file(ENV_FILE)
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
    finance_snapshot = build_finance_snapshot(legacy_abra_payload, generated_at)
    marketing_snapshot = build_marketing_snapshot(legacy_abra_payload, finance_snapshot, generated_at)
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

        wpj_summary = summarize_orders(yesterday_orders)
        history_days, baseline_orders, baseline_revenue = summarize_daily_history(history_orders, report_date)
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
        )
        expiry_overview_payload = build_expiry_overview(generated_at, combined_index_payload, cz_expiry_summary, sk_expiry_summary)

        analytics_cache_path = CURRENT_DIR / 'inventory_analytics_365d.json'
        inventory_analytics_payload = load_json_if_fresh(analytics_cache_path, max_age_hours=24)
        if not inventory_analytics_payload:
            year_orders = fetch_wpj_year_order_metrics(wpj_url, wpj_token, year_start, report_end, limit=1000)
            inventory_analytics_payload = build_inventory_analytics_365d(
                combined_index_payload,
                year_orders,
                year_start,
                report_end,
                generated_at,
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

    if not expiry_overview_payload.get('topExpiring'):
        expiry_overview_payload = build_expiry_overview(generated_at, combined_index_payload, cz_expiry_summary, sk_expiry_summary)

    cz_daily = summarize_4px_window('CZ', cz_outbound, report_start, report_end)
    sk_daily = summarize_4px_window('SK', sk_outbound, report_start, report_end)
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
        logistics_summary,
        alerts,
        priorities,
        warnings,
    )
    report_text = format_morning_report_text(report_json)

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

    write_text(CURRENT_DIR / 'morning_report_previous_day.txt', report_text)
    write_text(snapshot_path / 'morning_report_previous_day.txt', report_text)

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
