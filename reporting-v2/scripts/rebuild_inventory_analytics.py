#!/usr/bin/env python3
import json
import os
from datetime import timedelta
from pathlib import Path

from refresh_data import (
    CURRENT_DIR,
    ENV_FILE,
    SKU_MAPPING_OVERRIDE_FILE,
    build_inventory_analytics_365d,
    current_local_time,
    fetch_wpj_products,
    fetch_wpj_year_order_metrics,
    load_env_file,
    load_json_if_fresh,
    load_manual_sku_overrides,
    write_json,
    wpj_endpoint,
)


def read_json(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    load_env_file(ENV_FILE)
    manual_overrides = load_manual_sku_overrides(SKU_MAPPING_OVERRIDE_FILE)

    wpj_url = wpj_endpoint()
    wpj_token = os.environ.get('WPJ_ACCESS_TOKEN')
    if not wpj_url or not wpj_token:
        raise SystemExit('WPJ není připravené, chybí URL nebo X-Access-Token.')

    combined_index_path = CURRENT_DIR / 'combined_product_index.json'
    if not combined_index_path.exists():
        raise SystemExit(f'Chybí {combined_index_path}, nejdřív spusť běžný refresh_data.py')

    now_local = current_local_time()
    generated_at = now_local.isoformat()
    report_start = now_local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    report_end = report_start + timedelta(days=1) - timedelta(seconds=1)
    year_start = report_start - timedelta(days=364)

    combined_index = read_json(combined_index_path)
    wpj_products_payload = load_json_if_fresh(CURRENT_DIR / 'wpj_products.json', max_age_hours=24)
    if not wpj_products_payload:
        wpj_products_payload = {
            'generatedAt': generated_at,
            'items': fetch_wpj_products(wpj_url, wpj_token),
        }
    wpj_by_code = {item.get('code'): item for item in (wpj_products_payload.get('items') or []) if item.get('code')}

    print('Fetching 365d WPJ order metrics...')
    year_orders = fetch_wpj_year_order_metrics(wpj_url, wpj_token, year_start, report_end, limit=1000)
    print(f'Fetched {len(year_orders)} yearly orders, rebuilding analytics...')

    analytics = build_inventory_analytics_365d(
        combined_index,
        year_orders,
        year_start,
        report_end,
        generated_at,
        wpj_by_code,
        manual_overrides,
    )

    output_path = CURRENT_DIR / 'inventory_analytics_365d.json'
    write_json(output_path, analytics)
    print(f'Rebuilt analytics at {generated_at}')
    print(f'Tracked items: {analytics.get("summary", {}).get("trackedItems")}')
    print(f'Output: {output_path}')


if __name__ == '__main__':
    main()
