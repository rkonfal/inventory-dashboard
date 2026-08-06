#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import csv
import io
import posixpath
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

from adapters.abra import AbraAdapter
from adapters.fourpx import FourPxAdapter
from adapters.marketing_sources import MarketingSourcesAdapter
from adapters.wpj import WpjAdapter

ROOT = Path(__file__).resolve().parents[1]
VENDOR_PY_DIR = ROOT / 'vendor_py'
if VENDOR_PY_DIR.exists() and str(VENDOR_PY_DIR) not in sys.path:
    sys.path.insert(0, str(VENDOR_PY_DIR))

try:
    import xlrd
    if not hasattr(xlrd, 'open_workbook'):
        raise ImportError('xlrd package is incomplete')
except ImportError:
    xlrd = None

ENV_FILE = ROOT / '.env.local'
REMOTE_STORAGE_ENV_FILE = ROOT / '.env.remote-storage'
CONFIG_DIR = ROOT / 'config'
SKU_MAPPING_OVERRIDE_FILE = CONFIG_DIR / 'sku_mapping_overrides.json'
POS_ADMIN_VIEW_OVERRIDE_FILE = CONFIG_DIR / 'pos_admin_view_overrides.json'
ORDERING_REFERENCE_OVERRIDE_FILE = CONFIG_DIR / 'ordering_reference_overrides.json'
ORDERING_ACTIONS_OVERRIDE_FILE = CONFIG_DIR / 'ordering_actions_overrides.json'
STORE_EXPIRY_BATCHES_FILE = CONFIG_DIR / 'store_expiry_batches.json'
ORDERING_PACKAGING_MATCH_FILE = ROOT.parent / 'knowledge' / 'tiande_order_packaging_catalog_match.json'
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
LIVE_CASH_ACCOUNT_PREFIXES = ('221', '211')
ORDERING_TARGET_COVER_DAYS = 30
ABRA_STOCK_CARD_PAGE_SIZE = 5000
ABRA_STOCK_CARD_MAX_PAGES = 20

class Settings:
    FALSE_VALUES = {'0', 'false', 'no'}

    def __init__(self, env: dict[str, str] | None = None):
        self.env = env if env is not None else os.environ
        self.reload()

    def reload(self):
        self.reporting_remote_storage_mode = self.get_stripped('REPORTING_REMOTE_STORAGE_MODE', 'off').lower()
        self.reporting_remote_storage_root = self.get_stripped('REPORTING_REMOTE_STORAGE_ROOT')
        self.reporting_remote_storage_ssh_target = self.get_stripped('REPORTING_REMOTE_STORAGE_SSH_TARGET')
        self.reporting_remote_storage_ssh_key = self.get_stripped('REPORTING_REMOTE_STORAGE_SSH_KEY')
        self.reporting_remote_storage_ssh_identities_only = self.is_enabled('REPORTING_REMOTE_STORAGE_SSH_IDENTITIES_ONLY', True)

        self.wpj_graphql_url = self.get_stripped('WPJ_GRAPHQL_URL')
        self.wpj_proxy_url = self.get_stripped('WPJ_PROXY_URL')
        self.wpj_access_token = self.get_stripped('WPJ_ACCESS_TOKEN')

        self.affiliate_admin_key = self.get_stripped('AFFILIATE_ADMIN_KEY', 'admin123') or 'admin123'
        self.morning_report_detail_url = (
            self.get_stripped(
                'MORNING_REPORT_DETAIL_URL',
                'https://rkonfal.github.io/diamond-plus-reporting-preview/site/index.html',
            )
            or 'https://rkonfal.github.io/diamond-plus-reporting-preview/site/index.html'
        )

        self.abra_api_url = self.first('ABRA_API_URL', 'FLEXI_API_URL') or ''
        self.abra_company = self.first('ABRA_COMPANY', 'FLEXI_COMPANY') or ''
        self.abra_username = self.first('ABRA_USERNAME', 'FLEXI_USERNAME') or ''
        self.abra_password = self.first('ABRA_PASSWORD', 'FLEXI_PASSWORD') or ''

        self.sklik_api_token = self.get_stripped('SKLIK_API_TOKEN')
        self.meta_access_token = self.get_stripped('META_ACCESS_TOKEN')
        self.meta_ad_account_ids = self.get_stripped('META_AD_ACCOUNT_IDS')

        self.google_ads_developer_token = self.get_stripped('GOOGLE_ADS_DEVELOPER_TOKEN')
        self.google_ads_login_customer_id = self.get_stripped('GOOGLE_ADS_LOGIN_CUSTOMER_ID')
        self.google_ads_oauth_client_id = self.get_stripped('GOOGLE_ADS_OAUTH_CLIENT_ID')
        self.google_ads_oauth_client_secret = self.get_stripped('GOOGLE_ADS_OAUTH_CLIENT_SECRET')
        self.google_ads_refresh_token = self.get_stripped('GOOGLE_ADS_REFRESH_TOKEN')

        self.ga4_property_id = self.get_stripped('GA4_PROPERTY_ID', '220403487') or '220403487'
        self.ga4_service_account_file = self.first('GA4_SERVICE_ACCOUNT_FILE', 'GOOGLE_APPLICATION_CREDENTIALS') or ''
        self.ga4_oauth_client_id = self.first('GA4_OAUTH_CLIENT_ID', 'GOOGLE_ADS_OAUTH_CLIENT_ID') or ''
        self.ga4_oauth_client_secret = self.first('GA4_OAUTH_CLIENT_SECRET', 'GOOGLE_ADS_OAUTH_CLIENT_SECRET') or ''
        self.ga4_refresh_token = self.get_stripped('GA4_REFRESH_TOKEN')

        self.ecomail_api_key = self.get_stripped('ECOMAIL_API_KEY')
        self.klaviyo_private_api_key = self.get_stripped('KLAVIYO_PRIVATE_API_KEY')
        self.dpd_geoapi_key = self.get_stripped('DPD_GEOAPI_KEY')
        self.dpd_geoapi_dsw = self.get_stripped('DPD_GEOAPI_DSW')
        self.fourpx_warehouse_code = self.get_stripped('FOURPX_WAREHOUSE_CODE', 'CZPRGA') or 'CZPRGA'
        self.fourpx_outbound_max_pages = self.get_int('FOURPX_OUTBOUND_MAX_PAGES', 20)
        self.store_expiry_sheet_csv_url = self.get_stripped('STORE_EXPIRY_SHEET_CSV_URL')
        self.store_expiry_sheet_timeout_seconds = self.get_int('STORE_EXPIRY_SHEET_TIMEOUT_SECONDS', 30)

        self.reporting_heavy_payloads = self.csv('REPORTING_HEAVY_PAYLOADS')
        self.reporting_skip_heavy_snapshot_writes = self.is_enabled('REPORTING_SKIP_HEAVY_SNAPSHOT_WRITES', True)
        self.reporting_snapshot_keep = self.get_stripped('REPORTING_SNAPSHOT_KEEP', '3') or '3'

    def get(self, key: str, default: str | None = None):
        return self.env.get(key, default)

    def get_stripped(self, key: str, default: str = '') -> str:
        value = self.get(key, default)
        if value is None:
            return ''
        return str(value).strip()

    def get_int(self, key: str, default: int) -> int:
        raw = self.get_stripped(key, str(default))
        try:
            return int(raw)
        except ValueError:
            return default

    def first(self, *keys: str):
        for key in keys:
            value = self.get(key)
            if value:
                return str(value).strip()
        return None

    def csv(self, key: str) -> list[str]:
        raw = self.get(key, '')
        return [item.strip() for item in str(raw).split(',') if item.strip()]

    def is_enabled(self, key: str, default: bool) -> bool:
        fallback = '1' if default else '0'
        return self.get_stripped(key, fallback).lower() not in self.FALSE_VALUES

    def setdefault(self, key: str, value: str):
        self.env.setdefault(key, value)

    def require(self, key: str) -> str:
        return self.env[key]

    def missing(self, *keys: str) -> list[str]:
        return [key for key in keys if not self.get(key)]

    def fourpx_credentials(self, market: str) -> tuple[str, str]:
        prefix = market.upper()
        return (
            self.require(f'FOURPX_{prefix}_APP_KEY'),
            self.require(f'FOURPX_{prefix}_APP_SECRET'),
        )

    def wpj_endpoint(self) -> str:
        return self.wpj_graphql_url or self.wpj_proxy_url

    def abra_config(self):
        return {
            'baseUrl': self.abra_api_url.rstrip('/'),
            'company': self.abra_company,
            'username': self.abra_username,
            'password': self.abra_password,
            'enabled': all([self.abra_api_url, self.abra_company, self.abra_username, self.abra_password]),
        }


SETTINGS = Settings()
WPJ_ADAPTER = WpjAdapter()
ABRA_ADAPTER = AbraAdapter()
MARKETING_SOURCES = MarketingSourcesAdapter(
    root=ROOT,
    current_dir=CURRENT_DIR,
    prague_tz=PRAGUE_TZ,
    settings=SETTINGS,
)


@dataclass
class CombinedProductsBuildContext:
    wpj_products: list[dict[str, Any]]
    yesterday_orders: list[dict[str, Any]]
    cz_inventory: dict[str, Any]
    sk_inventory: dict[str, Any]
    cz_outbound: dict[str, Any]
    sk_outbound: dict[str, Any]
    start_dt: datetime
    end_dt: datetime
    generated_at: str
    manual_overrides: dict[str, Any] | None = None
    pos_admin_views: dict[str, Any] | None = None


@dataclass
class InventoryAnalyticsBuildContext:
    combined_index: dict[str, Any]
    orders: list[dict[str, Any]]
    start_dt: datetime
    end_dt: datetime
    generated_at: str
    window_days: int = 365
    wpj_by_code: dict[str, Any] | None = None
    manual_overrides: dict[str, Any] | None = None
    pos_admin_views: dict[str, Any] | None = None
    ordering_reference_overrides: dict[str, Any] | None = None
    ordering_packaging_map: dict[str, Any] | None = None


@dataclass
class OrderingSalesHistoryBuildContext:
    orders: list[dict[str, Any]]
    start_dt: datetime
    end_dt: datetime
    generated_at: str
    wpj_by_code: dict[str, Any] | None = None
    manual_overrides: dict[str, Any] | None = None
    pos_admin_views: dict[str, Any] | None = None


@dataclass
class OrderingCoreBuildContext:
    analytics_payload: dict[str, Any]
    generated_at: str


@dataclass
class MorningReportBuildContext:
    report_date: date
    wpj_summary: dict[str, Any]
    baseline_orders: float | int | None
    baseline_revenue: float | int | None
    stock_summary: dict[str, Any]
    inventory_summary: dict[str, Any]
    logistics_summary: dict[str, Any]
    alerts: list[str]
    priorities: list[str]
    warnings: list[str]
    mtd_summary: dict[str, Any] | None = None
    inventory_health: dict[str, Any] | None = None


@dataclass
class RefreshRuntimeContext:
    manual_overrides: dict[str, Any]
    pos_admin_views: dict[int, str]
    pos_view_filters: dict[str, list[int]]
    ordering_reference_overrides: dict[str, Any]
    ordering_packaging_map: dict[str, Any]
    store_expiry_input: dict[str, Any]
    warehouse_code: str
    max_pages: int
    now_local: datetime
    stamp: str
    generated_at: str
    report_start: datetime
    report_end: datetime
    report_date: date
    cz_app_key: str
    cz_app_secret: str
    sk_app_key: str
    sk_app_secret: str
    previous_wpj_products: list[dict[str, Any]] | None


@dataclass
class RefreshFetchResult:
    cz_inventory: dict[str, Any]
    sk_inventory: dict[str, Any]
    cz_inventory_detail: dict[str, Any]
    sk_inventory_detail: dict[str, Any]
    cz_expiry_summary: list[dict[str, Any]]
    sk_expiry_summary: list[dict[str, Any]]
    cz_outbound: dict[str, Any]
    sk_outbound: dict[str, Any]
    wpj_ready: bool
    legacy_abra_payload: Any
    live_abra_payload: Any
    abra_vykaz_hospodareni_reports: dict[str, Any]
    sklik_status: dict[str, Any]
    meta_status: dict[str, Any]
    google_status: dict[str, Any]
    ga4_status: dict[str, Any]
    klaviyo_status: dict[str, Any]
    finance_snapshot: dict[str, Any]
    affiliate_overview: dict[str, Any]
    marketing_snapshot: dict[str, Any]


@dataclass
class RefreshBuildResult:
    warnings: list[str]
    wpj_summary: dict[str, Any]
    wpj_orders_payload: dict[str, Any]
    wpj_products_payload: dict[str, Any]
    wpj_history_payload: dict[str, Any]
    eshop_ytd_payload: dict[str, Any]
    customer_fact_payload: dict[str, Any]
    order_fact_payload: dict[str, Any]
    inventory_analytics_payload: dict[str, Any]
    inventory_analytics_730_payload: dict[str, Any]
    inventory_analytics_730_cz_payload: dict[str, Any]
    inventory_analytics_730_sk_payload: dict[str, Any]
    ordering_core_payload: dict[str, Any]
    ordering_core_cz_payload: dict[str, Any]
    ordering_core_sk_payload: dict[str, Any]
    ordering_reference_payload: dict[str, Any]
    ordering_reference_cz_payload: dict[str, Any]
    ordering_reference_sk_payload: dict[str, Any]
    ordering_sales_history_payload: dict[str, Any]
    expiry_overview_payload: dict[str, Any]
    store_expiry_watchdog_payload: dict[str, Any]
    combined_index_payload: dict[str, Any]
    combined_overview_payload: dict[str, Any]
    baseline_orders: float | int | None
    baseline_revenue: float | int | None
    stock_summary: dict[str, Any]
    inventory_summary: dict[str, Any]
    logistics_summary: dict[str, Any]
    inventory_health_summary: dict[str, Any]
    alerts: list[str]
    priorities: list[str]
    report_json: dict[str, Any]
    report_text: str
    report_telegram_text: str
    heavy_payloads: set[str]
    skip_snapshot_for_heavy: bool
    report_manifest: dict[str, Any]


@dataclass
class RefreshBuildState:
    warnings: list[str]
    wpj_summary: dict[str, Any]
    wpj_orders_payload: dict[str, Any]
    wpj_products_payload: dict[str, Any]
    wpj_history_payload: dict[str, Any]
    eshop_ytd_payload: dict[str, Any]
    customer_fact_payload: dict[str, Any]
    order_fact_payload: dict[str, Any]
    inventory_analytics_payload: dict[str, Any]
    inventory_analytics_730_payload: dict[str, Any]
    inventory_analytics_730_cz_payload: dict[str, Any]
    inventory_analytics_730_sk_payload: dict[str, Any]
    ordering_core_payload: dict[str, Any]
    ordering_core_cz_payload: dict[str, Any]
    ordering_core_sk_payload: dict[str, Any]
    ordering_reference_payload: dict[str, Any]
    ordering_reference_cz_payload: dict[str, Any]
    ordering_reference_sk_payload: dict[str, Any]
    ordering_sales_history_payload: dict[str, Any]
    expiry_overview_payload: dict[str, Any]
    store_expiry_watchdog_payload: dict[str, Any]
    combined_index_payload: dict[str, Any]
    combined_overview_payload: dict[str, Any]
    stock_summary: dict[str, Any]
    baseline_orders: float | int | None = None
    baseline_revenue: float | int | None = None
    mtd_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RefreshOutputSpec:
    name: str
    payload: Any
    writer: str = 'json'
    snapshot_policy: str = 'always'


def load_env_file(path: Path):
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        SETTINGS.setdefault(key.strip(), value.strip())
    SETTINGS.reload()


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
        raise RuntimeError(f'Neplatný JSON v override mapě prodejen: {path}') from exc

    for view, pos_ids in (payload or {}).items():
        if view not in {'ltm', 'mecin'}:
            continue
        for pos_id in pos_ids or []:
            try:
                overrides[int(pos_id)] = view
            except (TypeError, ValueError):
                continue
    return overrides


def load_pos_view_filter_ids(path: Path):
    filters = {'ltm': [], 'mecin': []}
    if not path.exists():
        return filters
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'Neplatný JSON v override mapě POS adminů: {path}') from exc

    for view, pos_ids in (payload or {}).items():
        if view not in filters:
            continue
        for pos_id in pos_ids or []:
            try:
                filters[view].append(int(pos_id))
            except (TypeError, ValueError):
                continue
    return filters


def load_ordering_reference_overrides(path: Path):
    payload = {
        'skus': {},
        'titles': {},
        'titleStems': {},
        'prefixes': [],
        'titleContains': [],
    }
    if not path.exists():
        return payload
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'Neplatný JSON v ordering reference override mapě: {path}') from exc

    for raw_code, meta in (raw.get('skus') or {}).items():
        code = normalize_product_code(raw_code)
        if code and isinstance(meta, dict):
            payload['skus'][code] = dict(meta)

    for raw_title, meta in (raw.get('titles') or {}).items():
        title_key = normalize_lookup_key(raw_title)
        if title_key and isinstance(meta, dict):
            payload['titles'][title_key] = dict(meta)
            stem_key = normalize_title_stem_key(raw_title)
            if stem_key:
                existing = payload['titleStems'].get(stem_key)
                if existing is None:
                    payload['titleStems'][stem_key] = dict(meta)
                else:
                    payload['titleStems'][stem_key] = None

    for entry in (raw.get('prefixes') or []):
        if not isinstance(entry, dict):
            continue
        prefix = normalize_product_code(entry.get('prefix'))
        meta = entry.get('meta') if isinstance(entry.get('meta'), dict) else {}
        if prefix and meta:
            payload['prefixes'].append({'prefix': prefix, 'meta': dict(meta)})

    for entry in (raw.get('titleContains') or []):
        if not isinstance(entry, dict):
            continue
        needle = str(entry.get('needle') or '').strip().lower()
        meta = entry.get('meta') if isinstance(entry.get('meta'), dict) else {}
        if needle and meta:
            payload['titleContains'].append({'needle': needle, 'meta': dict(meta)})

    return payload


def normalize_lookup_key(value):
    text = str(value or '').strip().lower()
    text = ''.join(ch for ch in unicodedata.normalize('NFD', text) if unicodedata.category(ch) != 'Mn')
    return re.sub(r'[^a-z0-9]+', '_', text).strip('_')


def normalize_title_stem_key(value):
    text = str(value or '').strip().lower()
    text = re.sub(r'\s*\([^)]*\)\s*$', '', text)
    text = re.sub(r'\s*,\s*\d+[\d\s.,/]*\s*(g|kg|mg|ml|l|ks|cm|mm|m)\s*$', '', text)
    return normalize_lookup_key(text)


def parse_boolish(value, default=True):
    if value in (None, ''):
        return default
    key = normalize_lookup_key(value)
    if key in {'0', 'false', 'ne', 'no', 'off', 'inactive', 'disabled'}:
        return False
    if key in {'1', 'true', 'ano', 'yes', 'on', 'active', 'enabled'}:
        return True
    return default


def parse_decimal(value):
    if value in (None, ''):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace('\u00a0', '').replace(' ', '')
    if not text:
        return 0.0
    if text.count(',') == 1 and text.count('.') == 0:
        text = text.replace(',', '.')
    elif text.count(',') and text.count('.'):
        text = text.replace(',', '')
    try:
        return float(text)
    except ValueError:
        return 0.0


def normalize_google_sheet_csv_url(value):
    url = str(value or '').strip()
    if not url:
        return ''
    if 'docs.google.com/spreadsheets/d/' not in url:
        return url
    sheet_id_match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
    if not sheet_id_match:
        return url
    sheet_id = sheet_id_match.group(1)
    gid_match = re.search(r'[?#&]gid=(\d+)', url)
    gid = gid_match.group(1) if gid_match else '0'
    return f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}'


def normalize_google_sheet_xlsx_url(value):
    url = str(value or '').strip()
    if not url or 'docs.google.com/spreadsheets/d/' not in url:
        return ''
    sheet_id_match = re.search(r'/spreadsheets/d/([a-zA-Z0-9-_]+)', url)
    if not sheet_id_match:
        return ''
    sheet_id = sheet_id_match.group(1)
    return f'https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=xlsx'


STORE_EXPIRY_HEADER_ALIASES = {
    'store': 'store',
    'view': 'store',
    'prodejna': 'store',
    'prodejna_kod': 'store',
    'prodejna_code': 'store',
    'sku': 'sku',
    'kod': 'sku',
    'code': 'sku',
    'product_code': 'sku',
    'produkt': 'title',
    'product': 'title',
    'nazev': 'title',
    'title': 'title',
    'sarze': 'batch',
    'saze': 'batch',
    'batch': 'batch',
    'batch_no': 'batch',
    'batch_code': 'batch',
    'expirace': 'expiryDate',
    'datum_expirace': 'expiryDate',
    'expiry': 'expiryDate',
    'expiry_date': 'expiryDate',
    'datum_naskladneni': 'receivedDate',
    'naskladneno_dne': 'receivedDate',
    'received_date': 'receivedDate',
    'prijem_dne': 'receivedDate',
    'naskladneno': 'receivedUnits',
    'pocet': 'receivedUnits',
    'received_units': 'receivedUnits',
    'received_qty': 'receivedUnits',
    'kusu_naskladneno': 'receivedUnits',
    'vyrazeno': 'discardedUnits',
    'discarded_units': 'discardedUnits',
    'discarded_qty': 'discardedUnits',
    'presunuto': 'transferredUnits',
    'transferred_units': 'transferredUnits',
    'transfered_units': 'transferredUnits',
    'transferred_qty': 'transferredUnits',
    'poznamka': 'note',
    'note': 'note',
    'poznamky': 'note',
    'aktivni': 'active',
    'active': 'active',
    'enabled': 'active',
}

STORE_EXPIRY_VIEW_LABELS = {
    'ltm': 'Litomerice',
    'mecin': 'Mecin',
}


def parse_store_expiry_view(value):
    key = normalize_lookup_key(value)
    if key in {'ltm', 'litomerice', 'litomerice_prodejna', 'prodejna_litomerice'}:
        return 'ltm'
    if key in {'mecin', 'mecin_prodejna', 'prodejna_mecin'}:
        return 'mecin'
    return None


def normalize_store_expiry_row(raw_row, index, *, source_mode='local_json', sheet_title=''):
    if not isinstance(raw_row, dict):
        return None, f'Radek {index}: vstup neni objekt.'

    row = {}
    for key, value in raw_row.items():
        canonical = STORE_EXPIRY_HEADER_ALIASES.get(normalize_lookup_key(key))
        if canonical:
            row[canonical] = value

    if sheet_title and not row.get('store'):
        row['store'] = sheet_title

    store_view = parse_store_expiry_view(row.get('store'))
    sku = normalize_product_code(row.get('sku'))
    batch = str(row.get('batch') or '').strip()
    title = str(row.get('title') or '').strip()
    expiry_dt = parse_dt(row.get('expiryDate'))
    received_dt = parse_dt(row.get('receivedDate'))
    received_units = parse_decimal(row.get('receivedUnits'))
    discarded_units = parse_decimal(row.get('discardedUnits'))
    transferred_units = parse_decimal(row.get('transferredUnits'))
    note = str(row.get('note') or '').strip()
    active = parse_boolish(row.get('active'), True)

    if not store_view:
        return None, f'Radek {index}: chybi nebo nesedi prodejna.'
    if not sku:
        return None, f'Radek {index}: chybi SKU.'
    if not expiry_dt:
        return None, f'Radek {index}: chybi nebo nesedi expirace.'
    if received_units <= 0:
        return None, f'Radek {index}: naskladneno musi byt vetsi nez 0.'

    return {
        'rowNumber': index,
        'sourceMode': source_mode,
        'storeView': store_view,
        'storeLabel': STORE_EXPIRY_VIEW_LABELS.get(store_view, store_view.upper()),
        'sku': sku,
        'title': title,
        'batch': batch or f'{sku}-{expiry_dt.date().isoformat()}',
        'expiryDate': expiry_dt.date().isoformat(),
        'receivedDate': received_dt.date().isoformat() if received_dt else None,
        'receivedUnits': round(received_units, 2),
        'discardedUnits': round(max(discarded_units, 0.0), 2),
        'transferredUnits': round(max(transferred_units, 0.0), 2),
        'note': note,
        'active': active,
    }, None


def spreadsheet_column_index(cell_ref):
    match = re.match(r'([A-Z]+)', str(cell_ref or '').upper())
    if not match:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - 64)
    return max(value - 1, 0)


def xlsx_cell_text(cell, shared_strings, ns):
    cell_type = cell.get('t')
    if cell_type == 'inlineStr':
        return ''.join(part.text or '' for part in cell.findall('.//main:t', ns))
    value_node = cell.find('main:v', ns)
    if value_node is None:
        return ''
    value = value_node.text or ''
    if cell_type == 's':
        try:
            return shared_strings[int(value)]
        except Exception:
            return ''
    return value


def parse_store_expiry_rows_from_xlsx_bytes(body, *, source_url=''):
    ns = {
        'main': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
        'rel': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'pkgrel': 'http://schemas.openxmlformats.org/package/2006/relationships',
    }
    warnings = []
    rows = []

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        shared_strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            shared_root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            for item in shared_root.findall('main:si', ns):
                shared_strings.append(''.join(part.text or '' for part in item.findall('.//main:t', ns)))

        workbook_root = ET.fromstring(archive.read('xl/workbook.xml'))
        rels_root = ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        relationship_targets = {
            rel.get('Id'): rel.get('Target')
            for rel in rels_root.findall('pkgrel:Relationship', ns)
            if rel.get('Id') and rel.get('Target')
        }

        sheets = []
        for sheet in workbook_root.findall('main:sheets/main:sheet', ns):
            rel_id = sheet.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
            sheets.append((sheet.get('name') or '', relationship_targets.get(rel_id) or ''))

        for sheet_title, target in sheets:
            if not target:
                continue
            worksheet_path = posixpath.normpath(posixpath.join('xl', target))
            if worksheet_path not in archive.namelist():
                continue
            worksheet_root = ET.fromstring(archive.read(worksheet_path))

            matrix = []
            for row_node in worksheet_root.findall('.//main:sheetData/main:row', ns):
                cells = {}
                max_col = -1
                for cell in row_node.findall('main:c', ns):
                    col_index = spreadsheet_column_index(cell.get('r'))
                    cells[col_index] = xlsx_cell_text(cell, shared_strings, ns).strip()
                    max_col = max(max_col, col_index)
                matrix.append([cells.get(index, '') for index in range(max_col + 1)] if max_col >= 0 else [])

            header_index = None
            headers = []
            for idx, candidate in enumerate(matrix[:5]):
                normalized = [normalize_lookup_key(value) for value in candidate if str(value or '').strip()]
                has_sku = 'sku' in normalized
                has_units = any(value in {'pocet', 'naskladneno', 'received_units', 'received_qty'} for value in normalized)
                has_expiry = any(value in {'expirace', 'expiry', 'expiry_date', 'datum_expirace'} for value in normalized)
                if has_sku and has_units and has_expiry:
                    header_index = idx
                    headers = candidate
                    break
            if header_index is None and parse_store_expiry_view(sheet_title):
                for idx, candidate in enumerate(matrix[:5]):
                    normalized = [normalize_lookup_key(value) for value in candidate[:3]]
                    if len(candidate) >= 3 and normalized[2:3] == ['expirace']:
                        header_index = idx
                        headers = ['SKU', 'POCET', 'EXPIRACE']
                        break
            if header_index is None or not headers:
                continue

            for data_index, values in enumerate(matrix[header_index + 1:], start=header_index + 2):
                if not any(str(value or '').strip() for value in values):
                    continue
                raw_row = {}
                for column_index, header in enumerate(headers):
                    header_text = str(header or '').strip()
                    if not header_text:
                        continue
                    raw_row[header_text] = values[column_index] if column_index < len(values) else ''
                normalized, warning = normalize_store_expiry_row(
                    raw_row,
                    data_index,
                    source_mode='google_sheet_workbook',
                    sheet_title=sheet_title,
                )
                if warning:
                    warnings.append(f'{sheet_title}: {warning}')
                    continue
                normalized['sourceSheet'] = sheet_title
                rows.append(normalized)

    if source_url and not rows:
        warnings.append(f'Workbook z {source_url} nevratil zadne platne radky.')
    return rows, warnings


def load_store_expiry_rows_from_csv_text(text, *, source_url=''):
    rows = []
    warnings = []
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return rows, ['CSV nema hlavicku.']
    for index, raw_row in enumerate(reader, start=2):
        normalized, warning = normalize_store_expiry_row(raw_row, index, source_mode='google_sheet')
        if warning:
            warnings.append(warning)
            continue
        rows.append(normalized)
    if source_url and not rows:
        warnings.append(f'CSV z {source_url} nevratilo zadne platne radky.')
    return rows, warnings


def load_store_expiry_rows_from_json(path: Path):
    warnings = []
    if not path.exists():
        return [], warnings
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        return [], [f'Neplatny JSON ve store expiry vstupu: {path} ({exc})']

    raw_rows = payload.get('rows') if isinstance(payload, dict) else payload
    rows = []
    for index, raw_row in enumerate(raw_rows or [], start=1):
        normalized, warning = normalize_store_expiry_row(raw_row, index, source_mode='local_json')
        if warning:
            warnings.append(warning)
            continue
        rows.append(normalized)
    return rows, warnings


def load_store_expiry_input(path: Path, sheet_url: str):
    warnings = []
    rows = []
    source = {
        'status': 'missing',
        'mode': 'none',
        'label': 'Bez zdroje',
        'sheetUrl': '',
        'localPath': str(path),
    }

    normalized_workbook_url = normalize_google_sheet_xlsx_url(sheet_url)
    normalized_sheet_url = normalize_google_sheet_csv_url(sheet_url)
    if normalized_workbook_url:
        source.update({
            'mode': 'google_sheet_workbook',
            'label': 'Google Sheet workbook',
            'sheetUrl': normalized_workbook_url,
        })
        try:
            req = Request(
                normalized_workbook_url,
                headers={'User-Agent': 'reporting-v2/1.0'},
                method='GET',
            )
            with urlopen(req, timeout=SETTINGS.store_expiry_sheet_timeout_seconds) as resp:
                body = resp.read()
            rows, warnings = parse_store_expiry_rows_from_xlsx_bytes(body, source_url=normalized_workbook_url)
            source['status'] = 'ok' if rows else 'warn'
        except Exception as exc:
            warnings.append(f'Google Sheet workbook se nepodarilo nacist: {exc}')
            source['status'] = 'warn'

    if not rows and normalized_sheet_url:
        source.update({
            'mode': 'google_sheet',
            'label': 'Google Sheet CSV',
            'sheetUrl': normalized_sheet_url,
        })
        try:
            req = Request(
                normalized_sheet_url,
                headers={'User-Agent': 'reporting-v2/1.0'},
                method='GET',
            )
            with urlopen(req, timeout=SETTINGS.store_expiry_sheet_timeout_seconds) as resp:
                text = resp.read().decode('utf-8-sig', 'ignore')
            rows, csv_warnings = load_store_expiry_rows_from_csv_text(text, source_url=normalized_sheet_url)
            warnings.extend(csv_warnings)
            source['status'] = 'ok' if rows else 'warn'
        except Exception as exc:
            warnings.append(f'Google Sheet CSV se nepodarilo nacist: {exc}')
            source['status'] = 'warn'

    if not rows:
        local_rows, local_warnings = load_store_expiry_rows_from_json(path)
        warnings.extend(local_warnings)
        if local_rows:
            rows = local_rows
            source.update({
                'status': 'ok' if not warnings else 'warn',
                'mode': 'local_json' if source['mode'] == 'none' else 'google_sheet_fallback_local',
                'label': 'Lokalni JSON',
            })

    if not rows and source['status'] == 'missing':
        source['label'] = 'Vstup chybi'

    return {
        'generatedAt': current_local_time().isoformat(),
        'source': source,
        'warnings': warnings,
        'rows': rows,
    }


def normalize_supplier_sku(value):
    text = normalize_product_code(value)
    return text.upper()


def supplier_sku_aliases(value):
    sku = normalize_supplier_sku(value)
    aliases = []
    for candidate in (
        sku,
        sku[:-2] if sku.endswith('HM') else None,
        sku[:-2] if sku.endswith('-2') else None,
        sku.split('/', 1)[0] if '/' in sku else None,
    ):
        candidate = normalize_supplier_sku(candidate)
        if candidate and candidate not in aliases:
            aliases.append(candidate)
    return aliases


def packaging_confidence_rank(status):
    ranks = {
        'exact_code': 4,
        'base_code': 3,
        'exact_title': 2,
        'ambiguous_title': 1,
        'fuzzy_title': 1,
        'missing': 0,
    }
    return ranks.get(status, 0)


def merge_packaging_entry(base, row):
    supplier_sku = normalize_supplier_sku(row.get('sku'))
    if supplier_sku and supplier_sku not in base['supplierSkus']:
        base['supplierSkus'].append(supplier_sku)

    packaging_raw = str(row.get('packaging_raw') or '').strip()
    if packaging_raw and packaging_raw not in base['packagingRawValues']:
        base['packagingRawValues'].append(packaging_raw)

    for option in row.get('order_options') or []:
        try:
            option_int = int(option)
        except (TypeError, ValueError):
            continue
        if option_int > 0 and option_int not in base['orderPackOptions']:
            base['orderPackOptions'].append(option_int)

    status = row.get('match_status') or 'missing'
    if packaging_confidence_rank(status) > packaging_confidence_rank(base.get('matchStatus')):
        base['matchStatus'] = status

    recommended = row.get('recommended_order_qty')
    try:
        recommended_int = int(recommended)
    except (TypeError, ValueError):
        recommended_int = None
    if recommended_int and recommended_int > (base.get('recommendedOrderStep') or 0):
        base['recommendedOrderStep'] = recommended_int

    catalog_title = row.get('catalog_title') or row.get('name')
    if catalog_title and not base.get('catalogTitle'):
        base['catalogTitle'] = catalog_title


def finalize_packaging_entry(entry):
    options = sorted({int(option) for option in (entry.get('orderPackOptions') or []) if int(option) > 0})
    entry['orderPackOptions'] = options
    entry['supplierSkus'] = sorted(set(entry.get('supplierSkus') or []))
    entry['packagingRawValues'] = sorted(set(entry.get('packagingRawValues') or []))
    if options and not entry.get('recommendedOrderStep'):
        entry['recommendedOrderStep'] = options[-1]
    entry['packagingRaw'] = ' | '.join(entry.get('packagingRawValues') or []) or None
    return entry


def load_ordering_packaging_map(path: Path):
    payload = {
        'byCatalogCode': {},
        'bySupplierSku': {},
        'summary': {'entries': 0, 'matchedEntries': 0, 'catalogCodes': 0, 'supplierSkus': 0},
    }
    if not path.exists():
        return payload
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'Neplatný JSON v mapě balení objednávek: {path}') from exc

    items = raw.get('items') or []
    payload['summary']['entries'] = len(items)
    for row in items:
        supplier_sku = normalize_supplier_sku(row.get('sku'))
        catalog_code = normalize_product_code(row.get('catalog_code'))
        match_status = row.get('match_status') or 'missing'
        if supplier_sku:
            payload['bySupplierSku'][supplier_sku] = {
                'catalogCode': catalog_code or None,
                'matchStatus': match_status,
                'packagingRaw': row.get('packaging_raw'),
                'orderPackOptions': [int(option) for option in (row.get('order_options') or []) if str(option).strip()],
                'recommendedOrderStep': int(row.get('recommended_order_qty') or 0) or None,
                'name': row.get('name'),
                'catalogTitle': row.get('catalog_title'),
            }
        if not catalog_code:
            continue
        payload['summary']['matchedEntries'] += 1
        entry = payload['byCatalogCode'].setdefault(catalog_code, {
            'catalogCode': catalog_code,
            'catalogTitle': None,
            'supplierSkus': [],
            'packagingRawValues': [],
            'orderPackOptions': [],
            'recommendedOrderStep': None,
            'matchStatus': 'missing',
        })
        merge_packaging_entry(entry, row)

    for code, entry in list(payload['byCatalogCode'].items()):
        payload['byCatalogCode'][code] = finalize_packaging_entry(entry)

    payload['summary']['catalogCodes'] = len(payload['byCatalogCode'])
    payload['summary']['supplierSkus'] = len(payload['bySupplierSku'])
    return payload


def load_ordering_actions_overrides(path: Path):
    payload = {
        'defaultStartDate': None,
        'actions': {},
    }
    if not path.exists():
        return payload
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'Neplatný JSON v overridech akcí objednávání: {path}') from exc

    default_start = normalize_iso_date(raw.get('defaultStartDate'))
    if default_start:
        payload['defaultStartDate'] = default_start

    for action_key, row in (raw.get('actions') or {}).items():
        if not isinstance(row, dict):
            continue
        start_date = normalize_iso_date(row.get('startDate'))
        if start_date:
            payload['actions'][str(action_key)] = {
                'startDate': start_date,
            }
    return payload


def compact_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def build_sign(params, body_json, app_secret):
    ordered = ''.join(f'{key}{params[key]}' for key in sorted(params))
    return hashlib.md5((ordered + body_json + app_secret).encode('utf-8')).hexdigest()


def atomic_write_text(path: Path, text: str, encoding: str = 'utf-8'):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_text(text, encoding=encoding)
    tmp_path.replace(path)


def atomic_write_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def write_json(path: Path, data):
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def write_text(path: Path, text: str):
    atomic_write_text(path, text, encoding='utf-8')


def write_bytes(path: Path, data: bytes):
    atomic_write_bytes(path, data)


def parse_csv_env(name: str) -> list[str]:
    return SETTINGS.csv(name)


def sync_remote_heavy_payloads(files: list[str]):
    mode = SETTINGS.reporting_remote_storage_mode
    if mode == 'off' or not files:
        return {'mode': mode, 'synced': [], 'skipped': files, 'status': 'disabled'}

    current_dir = CURRENT_DIR
    synced = []
    if mode == 'filesystem':
        remote_root_raw = SETTINGS.reporting_remote_storage_root
        if not remote_root_raw:
            return {'mode': mode, 'synced': [], 'skipped': files, 'status': 'missing_root'}
        remote_root = Path(remote_root_raw).expanduser()
        remote_current = remote_root / 'current'
        remote_current.mkdir(parents=True, exist_ok=True)
        manifest = []
        for name in files:
            src = current_dir / name
            if not src.exists():
                continue
            dest = remote_current / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(dest, src.read_bytes())
            synced.append(name)
            manifest.append({'name': name, 'bytes': src.stat().st_size})
        write_json(remote_root / 'current_manifest.json', {
            'generatedAt': current_local_time().isoformat(),
            'files': manifest,
        })
        return {'mode': mode, 'synced': synced, 'skipped': [name for name in files if name not in synced], 'status': 'ok'}

    if mode == 'ssh':
        ssh_target = SETTINGS.reporting_remote_storage_ssh_target
        remote_root = SETTINGS.reporting_remote_storage_root
        ssh_key = SETTINGS.reporting_remote_storage_ssh_key
        if not ssh_target or not remote_root:
            return {'mode': mode, 'synced': [], 'skipped': files, 'status': 'missing_ssh_target_or_root'}
        ssh_base = ['ssh']
        scp_base = ['scp']
        if ssh_key:
            ssh_base.extend(['-i', ssh_key])
            scp_base.extend(['-i', ssh_key])
        if SETTINGS.reporting_remote_storage_ssh_identities_only:
            ssh_base.extend(['-o', 'IdentitiesOnly=yes'])
            scp_base.extend(['-o', 'IdentitiesOnly=yes'])
        try:
            subprocess.run([*ssh_base, ssh_target, f'mkdir -p {remote_root}/current'], check=True)
            for name in files:
                src = current_dir / name
                if not src.exists():
                    continue
                subprocess.run([*scp_base, str(src), f'{ssh_target}:{remote_root}/current/{name}'], check=True)
                synced.append(name)
            return {'mode': mode, 'synced': synced, 'skipped': [name for name in files if name not in synced], 'status': 'ok'}
        except Exception as exc:
            return {'mode': mode, 'synced': synced, 'skipped': [name for name in files if name not in synced], 'status': 'error', 'error': str(exc)}

    return {'mode': mode, 'synced': [], 'skipped': files, 'status': 'unsupported'}


def write_finance_payloads(base_dir: Path, finance_snapshot):
    payload = json.loads(json.dumps(finance_snapshot, ensure_ascii=False))
    journal = payload.get('journal') or {}
    monthly = journal.get('monthly') or []
    slim_monthly = []

    for month in monthly:
        label = month.get('label') or ''
        detail_name = f"finance_journal_{label.replace('/', '-')}.json"
        write_json(base_dir / detail_name, month)
        slim_month = {key: value for key, value in month.items() if key != 'recentEntries'}
        slim_month['entryCount'] = len(month.get('recentEntries') or [])
        slim_month['detailFile'] = detail_name
        slim_monthly.append(slim_month)

    current_label = (journal.get('currentMonth') or {}).get('label')
    slim_current = next((row for row in slim_monthly if row.get('label') == current_label), None) or {
        'label': current_label or '',
        'topExpenseAccounts': [],
        'topExpenseClasses': [],
        'topVendors': [],
        'entryCount': 0,
        'detailFile': '',
    }
    payload['journal'] = {
        'source': journal.get('source') or {},
        'monthly': slim_monthly,
        'currentMonth': slim_current,
    }
    write_json(base_dir / 'finance_overview.json', payload)


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


def normalize_iso_date(value):
    if value in (None, ''):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if len(text) >= 10:
        text = text[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def money(value):
    return round(float(value or 0), 2)


def num(value):
    return float(value or 0)


def ordering_target_units(daily_run_rate, effective_stock, target_days=ORDERING_TARGET_COVER_DAYS):
    return max(0, round(max(0.0, float(daily_run_rate or 0)) * max(0, int(target_days or 0)) - max(0.0, float(effective_stock or 0))))


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


FOURPX_ADAPTER = FourPxAdapter(
    base_url=BASE_URL,
    compact_json=compact_json,
    build_sign=build_sign,
    outbound_timestamp=lambda item: outbound_timestamp(item),
    prague_tz=PRAGUE_TZ,
)


def call_4px(method, body, app_key, app_secret, language='en'):
    return FOURPX_ADAPTER.call(method, body, app_key, app_secret, language=language)


def fetch_inventory(app_key, app_secret, warehouse_code):
    return FOURPX_ADAPTER.fetch_inventory(app_key, app_secret, warehouse_code)


def chunked(values, size):
    for idx in range(0, len(values), size):
        yield values[idx:idx + size]


def fetch_inventory_details(app_key, app_secret, warehouse_code, inventory_items, batch_size=100):
    return FOURPX_ADAPTER.fetch_inventory_details(app_key, app_secret, warehouse_code, inventory_items, batch_size=batch_size)


def summarize_expiry_details(label, detail_rows):
    per_sku_expiry = {}
    for row in detail_rows:
        expiry_dt = parse_dt(row.get('expiry_date'))
        stock = num(row.get('warehouse_stock'))
        if not expiry_dt or stock <= 0:
            continue
        sku = row.get('sku_code') or '–'
        if str(sku).upper().startswith('TEST'):
            continue
        expiry_key = expiry_dt.date().isoformat()
        item = per_sku_expiry.setdefault((sku, expiry_key), {
            'account': label,
            'sku': sku,
            'dateExpiry': expiry_key,
            'batchCount': 0,
            'datedStock': 0.0,
        })
        item['batchCount'] += 1
        item['datedStock'] += stock

    results = []
    now_local = current_local_time()
    for (_, _), row in per_sku_expiry.items():
        expiry_date = date.fromisoformat(row['dateExpiry'])
        days_to_expiry = (expiry_date - now_local.date()).days
        results.append({
            'account': row['account'],
            'sku': row['sku'],
            'dateExpiry': row['dateExpiry'],
            'daysToExpiry': days_to_expiry,
            'datedStock': round(row['datedStock'], 2),
            'stockAtNearestExpiry': round(row['datedStock'], 2),
            'batchCount': row['batchCount'],
            'riskScore': round(row['datedStock'] / (max(days_to_expiry + 1, 1) ** 1.3), 2),
        })

    results.sort(key=lambda item: (-item['riskScore'], item['daysToExpiry'], -item['datedStock'], item['sku'], item['dateExpiry']))
    return results


def outbound_timestamp(item):
    return parse_dt(item.get('create_time') or item.get('audit_time') or item.get('update_time'))


def fetch_recent_outbound(app_key, app_secret, warehouse_code, max_pages=20, stop_before=None):
    return FOURPX_ADAPTER.fetch_recent_outbound(app_key, app_secret, warehouse_code, max_pages=max_pages, stop_before=stop_before)


def wpj_endpoint():
    return SETTINGS.wpj_endpoint()


ORDERING_ANALYTICS_DAYS = 548


def call_wpj(query, variables, url, access_token):
    return WPJ_ADAPTER.call(query, variables, url, access_token)


def fetch_wpj_orders(url, access_token, start_dt, end_dt, *, limit=1000, detailed=False, pos_id=None, classified_view=None):
    return WPJ_ADAPTER.fetch_orders(
        url,
        access_token,
        start_dt,
        end_dt,
        limit=limit,
        detailed=detailed,
        pos_id=pos_id,
        classified_view=classified_view,
    )


def fetch_wpj_products(url, access_token, limit=1000):
    return WPJ_ADAPTER.fetch_products(url, access_token, limit=limit)


def fetch_wpj_year_order_metrics(url, access_token, start_dt, end_dt, limit=1000):
    return WPJ_ADAPTER.fetch_year_order_metrics(url, access_token, start_dt, end_dt, limit=limit)


def merge_orders_by_id(base_orders, override_orders):
    merged = []
    seen = set()
    override_map = {str(order.get('id')): order for order in override_orders if order.get('id') is not None}

    for order in base_orders:
        key = str(order.get('id')) if order.get('id') is not None else None
        replacement = override_map.get(key) if key is not None else None
        merged.append(replacement or order)
        if key is not None:
            seen.add(key)

    for order in override_orders:
        key = str(order.get('id')) if order.get('id') is not None else None
        if key is None or key not in seen:
            merged.append(order)

    return merged


def apply_pos_view_overrides_to_orders(orders, url, access_token, start_dt, end_dt, *, detailed=False, pos_view_ids=None, limit=1000):
    pos_view_ids = pos_view_ids or {}
    result = list(orders)
    for view, pos_ids in pos_view_ids.items():
        if view not in {'ltm', 'mecin'}:
            continue
        for pos_id in pos_ids or []:
            tagged_orders = fetch_wpj_orders(
                url,
                access_token,
                start_dt,
                end_dt,
                limit=limit,
                detailed=detailed,
                pos_id=pos_id,
                classified_view=view,
            )
            result = merge_orders_by_id(result, tagged_orders)
    return result


def order_currency(order):
    currency = order.get('currency')
    if isinstance(currency, dict):
        return str(currency.get('code') or '').strip().upper()
    return str(currency or '').strip().upper()


def load_json_if_fresh(path: Path, *, max_age_hours, freshness_key='generatedAt'):
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    freshness_value = data.get(freshness_key) or data.get('generatedAt')
    generated = parse_dt(freshness_value)
    if not generated:
        return None
    age_hours = (current_local_time() - generated).total_seconds() / 3600
    if age_hours > max_age_hours:
        return None
    return data


def parse_affiliate_period_from_url(url):
    if not url:
        return {'dateFrom': None, 'dateTo': None, 'label': ''}
    match = re.search(r'_(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})\.html$', url)
    if not match:
        return {'dateFrom': None, 'dateTo': None, 'label': ''}
    start_date, end_date = match.groups()
    return {
        'dateFrom': start_date,
        'dateTo': end_date,
        'label': f'{start_date} až {end_date}',
    }


def affiliate_period_window(now_local):
    season_year = now_local.year if now_local.month >= 10 else now_local.year - 1
    start_date = f'{season_year}-10-01'
    end_date = now_local.date().isoformat()
    return {
        'dateFrom': start_date,
        'dateTo': end_date,
        'label': f'{start_date} až {end_date}',
    }


def affiliate_month_sort_key(value):
    match = re.match(r'^([a-z_]+)_(\d{4})$', value or '')
    if not match:
        return ('9999', value or '')
    month_name, year = match.groups()
    month_map = {
        'leden': 1,
        'unor': 2,
        'brezen': 3,
        'duben': 4,
        'kveten': 5,
        'cerven': 6,
        'cervenec': 7,
        'srpen': 8,
        'zari': 9,
        'rijen': 10,
        'listopad': 11,
        'prosinec': 12,
    }
    return (year, f'{month_map.get(month_name, 99):02d}')


def affiliate_month_key(dt_value):
    month_names = ['leden', 'unor', 'brezen', 'duben', 'kveten', 'cerven', 'cervenec', 'srpen', 'zari', 'rijen', 'listopad', 'prosinec']
    return f'{month_names[dt_value.month - 1]}_{dt_value.year}'


def empty_affiliate_month_bucket(month_key=''):
    label = month_key.replace('_', ' ').title() if month_key else ''
    return {
        'key': month_key,
        'label': label,
        'contacts': 0.0,
        'orderingContacts': 0.0,
        'networkCustomers': 0.0,
        'orderingCustomers': 0.0,
        'orders': 0.0,
        'revenueCzkEquiv': 0.0,
    }


def finalize_affiliate_months(monthly):
    ordered = []
    for month_key in sorted(monthly.keys(), key=affiliate_month_sort_key):
        row = monthly[month_key]
        ordered.append({
            'key': month_key,
            'label': row.get('label') or month_key.replace('_', ' ').title(),
            'contacts': round(float(row.get('contacts') or 0), 2),
            'orderingContacts': round(float(row.get('orderingContacts') or 0), 2),
            'networkCustomers': round(float(row.get('networkCustomers') or 0), 2),
            'orderingCustomers': round(float(row.get('orderingCustomers') or 0), 2),
            'orders': round(float(row.get('orders') or 0), 2),
            'revenueCzkEquiv': round(float(row.get('revenueCzkEquiv') or 0), 2),
        })
    return ordered


def parse_affiliate_money(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or '').replace('\xa0', ' ').replace('Kč', '').replace('CZK', '').replace(' ', '').replace(',', '.')
    match = re.search(r'-?\d+(?:\.\d+)?', text)
    return float(match.group(0)) if match else 0.0


def affiliate_partner_name(partner):
    first_name = (partner.get('first_name') or '').strip()
    surname = (partner.get('surname') or '').strip()
    full_name = ' '.join(part for part in [first_name, surname] if part).strip()
    if full_name:
        return full_name
    return partner.get('company_name') or partner.get('email') or partner.get('code') or '–'


def fetch_affiliate_partners(admin_key):
    return MARKETING_SOURCES.fetch_affiliate_partners(admin_key)


def fetch_affiliate_contacts(partner_code, admin_key):
    return MARKETING_SOURCES.fetch_affiliate_contacts(partner_code, admin_key)


def fetch_affiliate_commissions_table(partner_code, admin_key):
    return MARKETING_SOURCES.fetch_affiliate_commissions_table(partner_code, admin_key)


def build_affiliate_overview(generated_at, now_local):
    admin_key = SETTINGS.affiliate_admin_key
    period = affiliate_period_window(now_local)
    start_dt = datetime.fromisoformat(f"{period['dateFrom']}T00:00:00")
    partners = fetch_affiliate_partners(admin_key)

    novice_partners = [
        partner for partner in partners
        if parse_dt(partner.get('created_at')) and parse_dt(partner.get('created_at')).replace(tzinfo=None) >= start_dt
    ]

    def build_novice_row(partner):
        partner_code = partner.get('code') or ''
        contacts = fetch_affiliate_contacts(partner_code, admin_key)
        commission_table = fetch_affiliate_commissions_table(partner_code, admin_key)
        commissions = commission_table.get('commissions') or []
        monthly = defaultdict(lambda: empty_affiliate_month_bucket())
        for contact in contacts:
            added_at = parse_dt(contact.get('dateAdded'))
            if not added_at:
                continue
            month_key = affiliate_month_key(added_at)
            bucket = monthly[month_key]
            bucket['key'] = month_key
            bucket['label'] = month_key.replace('_', ' ').title()
            bucket['contacts'] += 1
            if num(contact.get('orderCount')) > 0:
                bucket['orderingContacts'] += 1
        for commission in commissions:
            raw_date = parse_dt(commission.get('raw_date'))
            if not raw_date:
                continue
            month_key = affiliate_month_key(raw_date)
            bucket = monthly[month_key]
            bucket['key'] = month_key
            bucket['label'] = month_key.replace('_', ' ').title()
            bucket['orders'] += 1
            bucket['revenueCzkEquiv'] += parse_affiliate_money(commission.get('amount'))
        return {
            'partner_code': partner_code,
            'partner_name': affiliate_partner_name(partner),
            'novice_contacts': len(contacts),
            'ordering_contacts': sum(1 for contact in contacts if num(contact.get('orderCount')) > 0),
            'orders': len(commissions),
            'revenue_czk_equiv': round(sum(parse_affiliate_money(commission.get('amount')) for commission in commissions), 2),
            'monthly': finalize_affiliate_months(monthly),
        }

    def build_network_row(partner):
        partner_code = partner.get('code') or ''
        commission_table = fetch_affiliate_commissions_table(partner_code, admin_key)
        commissions = commission_table.get('commissions') or []
        summary = commission_table.get('summary') or {}
        customer_stats = summary.get('customer_stats') or {}
        monthly = defaultdict(lambda: empty_affiliate_month_bucket())
        for commission in commissions:
            raw_date = parse_dt(commission.get('raw_date'))
            if not raw_date:
                continue
            month_key = affiliate_month_key(raw_date)
            bucket = monthly[month_key]
            bucket['key'] = month_key
            bucket['label'] = month_key.replace('_', ' ').title()
            bucket['orders'] += 1
            bucket['revenueCzkEquiv'] += parse_affiliate_money(commission.get('amount'))
        return {
            'partner_code': partner_code,
            'partner_name': affiliate_partner_name(partner),
            'partner_tiande_id': None,
            'network_customers': round(float(customer_stats.get('mlm') or 0), 2),
            'ordering_customers': round(float(summary.get('unique_customers') or 0), 2),
            'orders': len(commissions),
            'revenue_czk_equiv': round(sum(parse_affiliate_money(commission.get('amount')) for commission in commissions), 2),
            'monthly': finalize_affiliate_months(monthly),
        }

    novice_rows = []
    network_rows = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        novice_futures = [executor.submit(build_novice_row, partner) for partner in novice_partners]
        for future in as_completed(novice_futures):
            novice_rows.append(future.result())
    with ThreadPoolExecutor(max_workers=12) as executor:
        network_futures = [executor.submit(build_network_row, partner) for partner in partners]
        for future in as_completed(network_futures):
            network_rows.append(future.result())

    novice_rows = sorted(novice_rows, key=lambda row: float(row.get('revenue_czk_equiv') or 0), reverse=True)[:50]
    network_rows = sorted(network_rows, key=lambda row: float(row.get('revenue_czk_equiv') or 0), reverse=True)[:50]

    novice_monthly_buckets = defaultdict(lambda: empty_affiliate_month_bucket())
    for row in novice_rows:
        for month_row in row.get('monthly') or []:
            month_key = month_row.get('key') or ''
            if not month_key:
                continue
            bucket = novice_monthly_buckets[month_key]
            bucket['key'] = month_key
            bucket['label'] = month_row.get('label') or month_key.replace('_', ' ').title()
            bucket['contacts'] += float(month_row.get('contacts') or 0)
            bucket['orderingContacts'] += float(month_row.get('orderingContacts') or 0)
            bucket['orders'] += float(month_row.get('orders') or 0)
            bucket['revenueCzkEquiv'] += float(month_row.get('revenueCzkEquiv') or 0)

    network_monthly_buckets = defaultdict(lambda: empty_affiliate_month_bucket())
    for row in network_rows:
        for month_row in row.get('monthly') or []:
            month_key = month_row.get('key') or ''
            if not month_key:
                continue
            bucket = network_monthly_buckets[month_key]
            bucket['key'] = month_key
            bucket['label'] = month_row.get('label') or month_key.replace('_', ' ').title()
            bucket['orders'] += float(month_row.get('orders') or 0)
            bucket['revenueCzkEquiv'] += float(month_row.get('revenueCzkEquiv') or 0)

    novice_monthly = finalize_affiliate_months(novice_monthly_buckets)
    network_monthly = finalize_affiliate_months(network_monthly_buckets)
    novice_top = novice_rows[:20]
    network_top = network_rows[:20]
    novice_total_revenue = round(sum(float(row.get('revenue_czk_equiv') or 0) for row in novice_rows), 2)
    network_total_revenue = round(sum(float(row.get('revenue_czk_equiv') or 0) for row in network_rows), 2)

    return {
        'generatedAt': generated_at,
        'source': {
            'status': 'live_api',
            'message': 'Affiliate přehled se skládá přímo z živých API endpointů affiliate portálu, bez závislosti na HTML exportech.',
            'errors': [],
        },
        'period': period,
        'reports': {
            'novice': {
                'label': 'Získaní nováčci',
                'url': None,
                'available': True,
                'error': None,
                'partners': len(novice_rows),
                'totals': {
                    'contacts': round(sum(float(row.get('novice_contacts') or 0) for row in novice_rows), 2),
                    'orderingContacts': round(sum(float(row.get('ordering_contacts') or 0) for row in novice_rows), 2),
                    'orders': round(sum(float(row.get('orders') or 0) for row in novice_rows), 2),
                    'revenueCzkEquiv': novice_total_revenue,
                },
                'monthly': novice_monthly,
                'topPartners': [
                    {
                        'partnerCode': row.get('partner_code'),
                        'partnerName': row.get('partner_name'),
                        'contacts': round(float(row.get('novice_contacts') or 0), 2),
                        'orderingContacts': round(float(row.get('ordering_contacts') or 0), 2),
                        'orders': round(float(row.get('orders') or 0), 2),
                        'revenueCzkEquiv': round(float(row.get('revenue_czk_equiv') or 0), 2),
                    }
                    for row in novice_top
                ],
            },
            'network': {
                'label': 'MLM síť a tržby',
                'url': None,
                'available': True,
                'error': None,
                'partners': len(network_rows),
                'totals': {
                    'networkCustomers': round(sum(float(row.get('network_customers') or 0) for row in network_rows), 2),
                    'orderingCustomers': round(sum(float(row.get('ordering_customers') or 0) for row in network_rows), 2),
                    'orders': round(sum(float(row.get('orders') or 0) for row in network_rows), 2),
                    'revenueCzkEquiv': network_total_revenue,
                },
                'monthly': network_monthly,
                'topPartners': [
                    {
                        'partnerCode': row.get('partner_code'),
                        'partnerName': row.get('partner_name'),
                        'partnerTiandeId': row.get('partner_tiande_id'),
                        'networkCustomers': round(float(row.get('network_customers') or 0), 2),
                        'orderingCustomers': round(float(row.get('ordering_customers') or 0), 2),
                        'orders': round(float(row.get('orders') or 0), 2),
                        'revenueCzkEquiv': round(float(row.get('revenue_czk_equiv') or 0), 2),
                    }
                    for row in network_top
                ],
            },
        },
        'summary': {
            'noviceRevenueCzkEquiv': novice_total_revenue,
            'networkRevenueCzkEquiv': network_total_revenue,
            'novicePartners': len(novice_rows),
            'networkPartners': len(network_rows),
            'noviceOrders': round(sum(float(row.get('orders') or 0) for row in novice_rows), 2),
            'networkOrders': round(sum(float(row.get('orders') or 0) for row in network_rows), 2),
            'noviceOrderingContacts': round(sum(float(row.get('ordering_contacts') or 0) for row in novice_rows), 2),
            'networkOrderingCustomers': round(sum(float(row.get('ordering_customers') or 0) for row in network_rows), 2),
            'topNovicePartner': ({
                'partnerCode': novice_top[0].get('partner_code'),
                'partnerName': novice_top[0].get('partner_name'),
                'revenueCzkEquiv': round(float(novice_top[0].get('revenue_czk_equiv') or 0), 2),
            } if novice_top else None),
            'topNetworkPartner': ({
                'partnerCode': network_top[0].get('partner_code'),
                'partnerName': network_top[0].get('partner_name'),
                'revenueCzkEquiv': round(float(network_top[0].get('revenue_czk_equiv') or 0), 2),
            } if network_top else None),
        },
    }


def mark_payload_refreshed(payload, refreshed_at):
    if not isinstance(payload, dict):
        return payload
    original_generated_at = payload.get('generatedAt')
    if original_generated_at and not payload.get('sourceGeneratedAt'):
        payload['sourceGeneratedAt'] = original_generated_at
    payload['generatedAt'] = refreshed_at
    return payload


def ordering_sales_history_needs_rebuild(payload, end_dt):
    if not payload or not payload.get('codes'):
        return True
    window_to = parse_dt(((payload.get('window') or {}).get('to')))
    if not window_to:
        return True
    return window_to.date() < end_dt.date()


def action_sales_start_date(action, overrides):
    action_overrides = ((overrides or {}).get('actions') or {}).get(str(action.get('key')))
    candidate = normalize_iso_date((action_overrides or {}).get('startDate'))
    if candidate:
        return candidate
    return normalize_iso_date((overrides or {}).get('defaultStartDate'))


def sales_since_start(history_row, view_key, start_date):
    if not history_row or not start_date:
        return 0.0, 0
    total_units = 0.0
    sale_days = 0
    for day_key, units in (history_row.get('dailyByView') or {}).get(view_key) or []:
        if str(day_key) < start_date:
            continue
        units_value = round(num(units), 2)
        if units_value:
            total_units += units_value
            sale_days += 1
    return round(total_units, 2), sale_days


def build_action_stock_breakdown(analytics_item, combined_item):
    combined_item = combined_item or {}
    analytics_item = analytics_item or {}
    fourpx = combined_item.get('fourpx') or {}

    return {
        'effectiveStock': round(num(analytics_item.get('fourpxAvailable') or analytics_item.get('effectiveStock')), 2),
        'fourpxTotal': round(num(fourpx.get('availableTotal')), 2),
        'fourpxCz': round(num(((fourpx.get('cz') or {}).get('availableStock'))), 2),
        'fourpxSk': round(num(((fourpx.get('sk') or {}).get('availableStock'))), 2),
    }


def resolve_action_snapshot_row(code, rows_by_code):
    code = str(code or '')
    if not code:
        return {}
    row = (rows_by_code or {}).get(code)
    if row:
        return row
    if '/' in code:
        base_code = normalize_product_code(code.split('/', 1)[0])
        return (rows_by_code or {}).get(base_code) or {}
    return {}


def refresh_action_item_snapshot(item, analytics_by_code, combined_by_code, history_by_code, market_key, start_date):
    code = str(item.get('code') or '')
    analytics_item = resolve_action_snapshot_row(code, analytics_by_code)
    combined_item = resolve_action_snapshot_row(code, combined_by_code)
    history_row = resolve_action_snapshot_row(code, history_by_code)

    next_item = dict(item)
    units_per_action = max(1, int(num(next_item.get('unitsPerAction') or 1)))
    stock_units = round(num(analytics_item.get('fourpxAvailable') or analytics_item.get('effectiveStock')), 2)
    sales_units, sales_days = sales_since_start(history_row, market_key, start_date)

    next_item['stock'] = stock_units
    next_item['capacity'] = max(0, math.floor(max(stock_units, 0.0) / units_per_action))
    next_item['packaging'] = next_item.get('packaging') or analytics_item.get('packagingRaw') or ''
    next_item['price'] = round(num(next_item.get('price') or analytics_item.get('unitSellingPrice')), 2)
    next_item['daysOfCover90d'] = analytics_item.get('daysOfCover90d')
    next_item['reorderRisk'] = analytics_item.get('reorderRisk') or 'none'
    next_item['lastSaleDate'] = history_row.get('lastSaleDate') or analytics_item.get('lastSaleDate')
    next_item['salesWindowStart'] = start_date
    next_item['salesSinceStart'] = sales_units
    next_item['salesDaysSinceStart'] = sales_days
    next_item['stockBreakdown'] = build_action_stock_breakdown(analytics_item, combined_item)
    return next_item


def recalculate_action_summary(action):
    kind = action.get('kind')
    items = action.get('items') or []

    if kind == 'bundle':
        all_groups = []
        required_group_sold_actions = []
        for bucket_name in ('requiredGroups', 'giftGroups'):
            groups = []
            for group in action.get(bucket_name) or []:
                capacities = [max(0, int(num(item.get('capacity')))) for item in (group.get('items') or [])]
                available = 0
                if capacities:
                    available = sum(capacities) if group.get('mode') == 'sum' else min(capacities)
                # Compute sold actions for this group
                sales = [
                    max(0.0, num(item.get('salesSinceStart') or 0) / max(1, int(num(item.get('unitsPerAction') or 1))))
                    for item in (group.get('items') or [])
                ]
                group_sold = 0.0
                if sales:
                    group_sold = sum(sales) if group.get('mode') == 'sum' else min(sales)
                next_group = dict(group)
                next_group['availableActions'] = int(available)
                next_group['soldActions'] = int(group_sold)
                groups.append(next_group)
                all_groups.append(next_group)
                if bucket_name == 'requiredGroups':
                    required_group_sold_actions.append(group_sold)
            action[bucket_name] = groups
        bottleneck = min(all_groups, key=lambda group: group.get('availableActions') or 0) if all_groups else None
        action['availableActions'] = int((bottleneck or {}).get('availableActions') or 0)
        action['bottleneckLabel'] = (bottleneck or {}).get('label') or ''
        action['bottleneckActions'] = int((bottleneck or {}).get('availableActions') or 0)
        action['soldActions'] = int(min(required_group_sold_actions)) if required_group_sold_actions else 0
    elif kind == 'discount':
        total_capacity = int(sum(max(0, int(num(item.get('capacity')))) for item in items))
        action['availableActions'] = total_capacity
        action['bottleneckLabel'] = 'Celkem ve slevě'
        action['bottleneckActions'] = total_capacity
        action['soldActions'] = int(sum(max(0.0, num(item.get('salesSinceStart') or 0)) for item in items))
    elif kind == 'discount_set':
        total_capacity = min((max(0, int(num(item.get('capacity')))) for item in items), default=0)
        required_groups = action.get('requiredGroups') or []
        if required_groups:
            next_group = dict(required_groups[0])
            next_group['availableActions'] = int(total_capacity)
            action['requiredGroups'] = [next_group]
            action['bottleneckLabel'] = next_group.get('label') or ''
        action['availableActions'] = int(total_capacity)
        action['bottleneckActions'] = int(total_capacity)
        # soldActions = min of required-group items (each item must appear once per set)
        set_sales = [
            max(0.0, num(item.get('salesSinceStart') or 0) / max(1, int(num(item.get('unitsPerAction') or 1))))
            for item in items
        ]
        action['soldActions'] = int(min(set_sales)) if set_sales else 0

    action['totalStock'] = round(sum(num(item.get('stock')) for item in items), 2)
    action['salesWindowUnits'] = round(sum(num(item.get('salesSinceStart')) for item in items), 2)
    return action


def refresh_ordering_actions_payload(payload, market_payloads, combined_index_payload, sales_history_payload, generated_at, overrides=None):
    if not payload or not isinstance(payload, dict) or not (payload.get('markets') or {}):
        return payload

    original_generated_at = payload.get('sourceGeneratedAt') or payload.get('generatedAt')
    next_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    next_payload['generatedAt'] = generated_at
    if original_generated_at:
        next_payload['sourceGeneratedAt'] = original_generated_at

    combined_by_code = {
        str(item.get('code')): item
        for item in (combined_index_payload or {}).get('items') or []
        if item.get('code')
    }
    history_by_code = (sales_history_payload or {}).get('codes') or {}
    default_start = normalize_iso_date((overrides or {}).get('defaultStartDate'))
    window_to = ((sales_history_payload or {}).get('window') or {}).get('to')

    for market_key, market_block in (next_payload.get('markets') or {}).items():
        analytics_payload = (market_payloads or {}).get(market_key) or {}
        analytics_by_code = {
            str(item.get('code')): item
            for item in (analytics_payload.get('items') or [])
            if item.get('code')
        }
        actions = []
        for action in market_block.get('actions') or []:
            next_action = dict(action)
            start_date = action_sales_start_date(action, overrides) or default_start
            next_action['salesWindowStart'] = start_date
            if start_date:
                next_action['salesWindow'] = {
                    'start': start_date,
                    'end': window_to,
                }

            refreshed_items = [
                refresh_action_item_snapshot(item, analytics_by_code, combined_by_code, history_by_code, market_key, start_date)
                for item in (action.get('items') or [])
            ]
            next_action['items'] = refreshed_items

            for bucket_name in ('requiredGroups', 'giftGroups'):
                refreshed_groups = []
                for group in action.get(bucket_name) or []:
                    next_group = dict(group)
                    next_group['items'] = [
                        refresh_action_item_snapshot(item, analytics_by_code, combined_by_code, history_by_code, market_key, start_date)
                        for item in (group.get('items') or [])
                    ]
                    refreshed_groups.append(next_group)
                next_action[bucket_name] = refreshed_groups

            actions.append(recalculate_action_summary(next_action))

        bundle_count = sum(1 for action in actions if action.get('kind') == 'bundle')
        discount_count = sum(1 for action in actions if action.get('kind') != 'bundle')
        market_block['actions'] = actions
        market_block['summary'] = {
            'actionCount': len(actions),
            'bundleCount': bundle_count,
            'discountCount': discount_count,
            'discountItemCount': sum(len(action.get('items') or []) for action in actions if action.get('kind') != 'bundle'),
            'totalPotentialActions': int(sum(num(action.get('availableActions')) for action in actions if action.get('kind') != 'discount')),
            'zeroStockActions': sum(1 for action in actions if num(action.get('availableActions')) <= 0),
            'salesWindowStart': default_start,
            'salesWindowUnits': round(sum(num(action.get('salesWindowUnits')) for action in actions), 2),
        }

    return next_payload


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


TOP_PRODUCTS_LIMIT = 30


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

    def top_products(counter, limit=TOP_PRODUCTS_LIMIT, formatter=None):
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


def same_time_previous_year(dt):
    try:
        return dt.replace(year=dt.year - 1)
    except ValueError:
        return dt.replace(year=dt.year - 1, day=28)


def build_eshop_ytd_payload(orders, generated_at, now_local, pos_admin_views=None):
    current_cutoff = now_local.astimezone(PRAGUE_TZ)
    previous_cutoff = same_time_previous_year(current_cutoff)
    current_year = current_cutoff.year
    previous_year = current_year - 1
    month_names = ['Leden', 'Únor', 'Březen', 'Duben', 'Květen', 'Červen', 'Červenec', 'Srpen', 'Září', 'Říjen', 'Listopad', 'Prosinec']

    monthly_orders = defaultdict(list)
    for order in orders:
        if order.get('cancelled'):
            continue
        dt = parse_dt(order.get('dateCreated'))
        if not dt:
            continue
        if dt.year == current_year:
            if dt > current_cutoff:
                continue
        elif dt.year == previous_year:
            if dt > previous_cutoff:
                continue
        else:
            continue
        monthly_orders[(dt.year, dt.month)].append(order)

    months = []
    for month in range(1, current_cutoff.month + 1):
        previous_orders = monthly_orders.get((previous_year, month), [])
        current_orders = monthly_orders.get((current_year, month), [])
        months.append({
            'month': month,
            'label': month_names[month - 1],
            'partial': month == current_cutoff.month,
            'previous': {
                'year': previous_year,
                'count': len(previous_orders),
                'revenueWithVat': round(sum(order_total_czk(order, pos_admin_views) for order in previous_orders), 2),
            },
            'current': {
                'year': current_year,
                'count': len(current_orders),
                'revenueWithVat': round(sum(order_total_czk(order, pos_admin_views) for order in current_orders), 2),
            },
        })

    previous_total_count = sum(row['previous']['count'] for row in months)
    current_total_count = sum(row['current']['count'] for row in months)
    previous_total_revenue = round(sum(row['previous']['revenueWithVat'] for row in months), 2)
    current_total_revenue = round(sum(row['current']['revenueWithVat'] for row in months), 2)

    return {
        'generatedAt': generated_at,
        'currentCutoff': current_cutoff.isoformat(),
        'previousCutoff': previous_cutoff.isoformat(),
        'years': {'previous': previous_year, 'current': current_year},
        'months': months,
        'totals': {
            'previous': {
                'count': previous_total_count,
                'revenueWithVat': previous_total_revenue,
                'averageOrderValue': round(previous_total_revenue / previous_total_count, 2) if previous_total_count else 0,
            },
            'current': {
                'count': current_total_count,
                'revenueWithVat': current_total_revenue,
                'averageOrderValue': round(current_total_revenue / current_total_count, 2) if current_total_count else 0,
            },
        },
    }


def build_mtd_revenue_snapshot(orders, report_date, pos_admin_views=None):
    current_start = datetime(report_date.year, report_date.month, 1, 0, 0, 0, tzinfo=PRAGUE_TZ)
    current_end = datetime(report_date.year, report_date.month, report_date.day, 23, 59, 59, tzinfo=PRAGUE_TZ)
    previous_start = shift_month(current_start, -1)
    previous_month_last_day = (current_start - timedelta(days=1)).day
    previous_end_day = min(report_date.day, previous_month_last_day)
    previous_end = datetime(previous_start.year, previous_start.month, previous_end_day, 23, 59, 59, tzinfo=PRAGUE_TZ)
    pre_previous_start = shift_month(current_start, -2)
    pre_previous_month_last_day = (previous_start - timedelta(days=1)).day
    pre_previous_end_day = min(report_date.day, pre_previous_month_last_day)
    pre_previous_end = datetime(pre_previous_start.year, pre_previous_start.month, pre_previous_end_day, 23, 59, 59, tzinfo=PRAGUE_TZ)

    def in_window(order, start_dt, end_dt):
        dt = parse_dt(order.get('dateCreated'))
        if not dt or order.get('cancelled'):
            return False
        return start_dt <= dt <= end_dt

    current_orders = [order for order in orders if in_window(order, current_start, current_end)]
    previous_orders = [order for order in orders if in_window(order, previous_start, previous_end)]
    pre_previous_orders = [order for order in orders if in_window(order, pre_previous_start, pre_previous_end)]
    current_revenue = round(sum(order_total_czk(order, pos_admin_views) for order in current_orders), 2)
    previous_revenue = round(sum(order_total_czk(order, pos_admin_views) for order in previous_orders), 2)
    pre_previous_revenue = round(sum(order_total_czk(order, pos_admin_views) for order in pre_previous_orders), 2)

    return {
        'current': {
            'label': f'{current_start.day}. {current_start.month}.–{report_date.day}. {report_date.month}.',
            'dateFrom': current_start.isoformat(),
            'dateTo': current_end.isoformat(),
            'orders': len(current_orders),
            'revenueWithVat': current_revenue,
        },
        'previousSamePeriod': {
            'label': f'{previous_start.day}. {previous_start.month}.–{previous_end_day}. {previous_start.month}.',
            'dateFrom': previous_start.isoformat(),
            'dateTo': previous_end.isoformat(),
            'orders': len(previous_orders),
            'revenueWithVat': previous_revenue,
        },
        'prePreviousSamePeriod': {
            'label': f'{pre_previous_start.day}. {pre_previous_start.month}.–{pre_previous_end_day}. {pre_previous_start.month}.',
            'dateFrom': pre_previous_start.isoformat(),
            'dateTo': pre_previous_end.isoformat(),
            'orders': len(pre_previous_orders),
            'revenueWithVat': pre_previous_revenue,
        },
        'changePct': pct_delta(current_revenue, previous_revenue) if previous_revenue else None,
        'prePreviousChangePct': pct_delta(current_revenue, pre_previous_revenue) if pre_previous_revenue else None,
    }


def clean_customer_value(value):
    return ' '.join(str(value or '').split()).strip()


def customer_label_from_order(order):
    inv = order.get('invoiceAddress') or {}
    dlv = order.get('deliveryAddress') or {}
    firm = clean_customer_value(inv.get('firm')) or clean_customer_value(dlv.get('firm'))
    person = ' '.join(
        x for x in [
            clean_customer_value(inv.get('name')) or clean_customer_value(dlv.get('name')),
            clean_customer_value(inv.get('surname')) or clean_customer_value(dlv.get('surname')),
        ] if x
    ).strip()
    email = clean_customer_value(order.get('email')).lower()
    if firm and person:
        return f'{firm} ({person})'
    if firm:
        return firm
    if person:
        return person
    if email:
        return email
    return 'Neznámý zákazník'


def customer_key_from_order(order):
    inv = order.get('invoiceAddress') or {}
    firm = clean_customer_value(inv.get('firm')).lower()
    ico = clean_customer_value(inv.get('ico'))
    email = clean_customer_value(order.get('email')).lower()
    if ico:
        return f'ico:{ico}'
    if email:
        return f'email:{email}'
    if firm:
        return f'firm:{firm}'
    return f'fallback:{customer_label_from_order(order).lower()}'


def build_customer_fact_payload(orders, generated_at, window, pos_admin_views=None):
    aggregated = {}
    processed = 0
    for order in orders:
        if order.get('cancelled'):
            continue
        created = parse_dt(order.get('dateCreated'))
        if not created:
            continue
        revenue = order_total_czk(order, pos_admin_views)
        key = customer_key_from_order(order)
        row = aggregated.setdefault(key, {
            'customerKey': key,
            'label': customer_label_from_order(order),
            'email': clean_customer_value(order.get('email')).lower(),
            'firm': clean_customer_value((order.get('invoiceAddress') or {}).get('firm')) or clean_customer_value((order.get('deliveryAddress') or {}).get('firm')),
            'person': ' '.join(x for x in [clean_customer_value((order.get('invoiceAddress') or {}).get('name')) or clean_customer_value((order.get('deliveryAddress') or {}).get('name')), clean_customer_value((order.get('invoiceAddress') or {}).get('surname')) or clean_customer_value((order.get('deliveryAddress') or {}).get('surname'))] if x).strip(),
            'city': clean_customer_value((order.get('invoiceAddress') or {}).get('city')) or clean_customer_value((order.get('deliveryAddress') or {}).get('city')),
            'ico': clean_customer_value((order.get('invoiceAddress') or {}).get('ico')),
            'countryCode': ((order.get('deliveryAddress') or {}).get('country') or {}).get('code') or 'CZ',
            'orders': 0,
            'revenueWithVat': 0.0,
            'firstOrderAt': '',
            'lastOrderAt': '',
            'channels': set(),
        })
        row['orders'] += 1
        row['revenueWithVat'] += revenue
        iso = created.isoformat()
        row['firstOrderAt'] = min(row['firstOrderAt'], iso) if row['firstOrderAt'] else iso
        row['lastOrderAt'] = max(row['lastOrderAt'], iso) if row['lastOrderAt'] else iso
        source_name = ((order.get('source') or {}).get('name') or '').strip()
        if source_name:
            row['channels'].add(source_name)
        processed += 1
    rows = sorted(aggregated.values(), key=lambda x: (-x['revenueWithVat'], -x['orders'], x['label']))
    for idx, row in enumerate(rows, 1):
        row['rank'] = idx
        row['averageOrderValue'] = round(row['revenueWithVat'] / row['orders'], 2) if row['orders'] else 0.0
        row['revenueWithVat'] = round(row['revenueWithVat'], 2)
        row['channels'] = sorted(row['channels'])
        row['customerType'] = 'returning' if row['orders'] > 1 else 'new'
    return {
        'generatedAt': generated_at,
        'window': window,
        'ordersProcessed': processed,
        'customersCount': len(rows),
        'summary': {
            'newCustomers': sum(1 for row in rows if row['orders'] == 1),
            'returningCustomers': sum(1 for row in rows if row['orders'] > 1),
            'repeatRevenueWithVat': round(sum(row['revenueWithVat'] for row in rows if row['orders'] > 1), 2),
            'newRevenueWithVat': round(sum(row['revenueWithVat'] for row in rows if row['orders'] == 1), 2),
        },
        'customers': rows,
    }


def build_order_fact_payload(orders, generated_at, window, pos_admin_views=None):
    rows = []
    for order in orders:
        created = parse_dt(order.get('dateCreated'))
        if not created:
            continue
        rows.append({
            'id': order.get('id'),
            'code': order.get('code'),
            'dateCreated': created.isoformat(),
            'customerKey': customer_key_from_order(order),
            'customerLabel': customer_label_from_order(order),
            'email': clean_customer_value(order.get('email')).lower(),
            'countryCode': ((order.get('deliveryAddress') or {}).get('country') or {}).get('code') or 'CZ',
            'sourceName': ((order.get('source') or {}).get('name') or '').strip(),
            'statusName': order_status_name(order),
            'cancelled': bool(order.get('cancelled')),
            'problematic': is_problematic_order(order),
            'isPaid': bool(order.get('isPaid')),
            'revenueWithVat': order_total_czk(order, pos_admin_views),
        })
    rows.sort(key=lambda x: x['dateCreated'], reverse=True)
    clean_rows = [row for row in rows if not row['cancelled']]
    return {
        'generatedAt': generated_at,
        'window': window,
        'summary': {
            'orders': len(rows),
            'nonCancelledOrders': len(clean_rows),
            'problematicOrders': sum(1 for row in rows if row['problematic']),
            'cancelledOrders': sum(1 for row in rows if row['cancelled']),
            'revenueWithVat': round(sum(float(row['revenueWithVat'] or 0) for row in clean_rows), 2),
        },
        'orders': rows,
    }


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


def summarize_stock(products, sold_product_codes, previous_products=None, ordering_reference_overrides=None):
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
        wpj_total_stock = round(sum(store['inStore'] for store in stores), 2)
        effective_stock = sum(store['inStore'] for store in fourpx_stores) if fourpx_stores else wpj_total_stock
        row = {
            'code': code,
            'title': product.get('title') or 'Bez názvu',
            'stock': round(effective_stock, 2),
            'reportedStock': num(product.get('inStore')),
            'wpjTotalStock': wpj_total_stock,
            'stores': stores,
            'priceWithVat': money((product.get('price') or {}).get('withVat')),
            'visible': bool(product.get('visible')),
        }
        reference_meta = apply_ordering_reference_overrides(
            infer_ordering_reference(code, row['title'], row['priceWithVat']),
            code,
            row['title'],
            ordering_reference_overrides or {},
        )
        is_stock_alert_candidate = row['visible'] and bool(reference_meta.get('orderable', True))
        if is_stock_alert_candidate and effective_stock <= 10:
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
            previous_total_stock = round(sum(store['inStore'] for store in previous_stores), 2)
            previous_stock = sum(store['inStore'] for store in previous_fourpx) if previous_fourpx else previous_total_stock
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


def filter_non_orderable_stock_rows(stock_summary, ordering_reference_overrides=None):
    summary = dict(stock_summary or {})

    def is_orderable_row(row):
        meta = apply_ordering_reference_overrides(
            infer_ordering_reference(row.get('code'), row.get('title'), row.get('priceWithVat')),
            row.get('code'),
            row.get('title'),
            ordering_reference_overrides or {},
        )
        return bool(meta.get('orderable', True))

    for key in ('lowStockSoldYesterday', 'lowStockOverall'):
        summary[key] = [row for row in (summary.get(key) or []) if is_orderable_row(row)]

    return summary


def normalize_city_name(value):
    text = str(value or '').strip().lower()
    return ''.join(ch for ch in unicodedata.normalize('NFD', text) if unicodedata.category(ch) != 'Mn')


def classify_order_view(order, pos_admin_views=None):
    pos_admin_views = pos_admin_views or {}
    explicit_view = str(order.get('__classifiedView') or '').strip().lower()
    if explicit_view in {'ltm', 'mecin', 'cz', 'sk'}:
        return explicit_view

    pos_id = order.get('__posId')
    if pos_id is None:
        pos_id = order.get('posId')
    try:
        pos_id = int(pos_id) if pos_id is not None else None
    except (TypeError, ValueError):
        pos_id = None
    if pos_id is not None and pos_id in pos_admin_views:
        return pos_admin_views[pos_id]

    source_name = ((order.get('source') or {}).get('name') or '').strip().lower()
    delivery_city = ((order.get('deliveryAddress') or {}).get('city') or '').strip().lower()
    invoice_city = ((order.get('invoiceAddress') or {}).get('city') or '').strip().lower()
    country = (((order.get('deliveryAddress') or {}).get('country') or {}).get('code') or '').strip().upper()

    if source_name in {'pokladna', 'administrace'}:
        for city in (delivery_city, invoice_city):
            city_ascii = normalize_city_name(city)
            if 'mecin' in city_ascii:
                return 'mecin'
            if 'litomer' in city_ascii:
                return 'ltm'
    if country == 'SK':
        return 'sk'
    return 'cz'


def order_total_czk(order, pos_admin_views=None):
    total = money((order.get('totalPrice') or {}).get('withVat'))
    currency = order_currency(order)
    if currency == 'EUR':
        return round(total * SK_EUR_TO_CZK_RATE, 2)
    return round(total * SK_EUR_TO_CZK_RATE, 2) if classify_order_view(order, pos_admin_views) == 'sk' else total


def order_item_revenue_czk(order, item, pos_admin_views=None):
    revenue = money((item.get('totalPrice') or {}).get('withVat'))
    currency = order_currency(order)
    if currency == 'EUR':
        return round(revenue * SK_EUR_TO_CZK_RATE, 2)
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


def collect_exact_order_metrics(orders, end_dt, pos_admin_views=None, windows=(90, 30, 14)):
    metrics = {}
    view_keys = ('complete', 'cz', 'sk', 'ltm', 'mecin')
    max_window = max(windows) if windows else 0

    for order in orders or []:
        dt = parse_dt(order.get('dateCreated'))
        if not dt:
            continue
        days_ago = (end_dt.date() - dt.date()).days
        if days_ago < 0 or (max_window and days_ago > max_window - 1):
            continue
        view = classify_order_view(order, pos_admin_views)
        for item in order.get('items') or []:
            if item.get('type') != 'product':
                continue
            raw_code = normalize_product_code(item.get('code') or item.get('name') or '–')
            row = metrics.setdefault(raw_code, {
                'code': raw_code,
                'lastSaleDate': None,
                'byView': {
                    key: {f'units{days}d': 0.0 for days in windows}
                    for key in view_keys
                },
                **{f'units{days}d': 0.0 for days in windows},
            })
            units = num(item.get('pieces'))
            for days in windows:
                if days_ago <= days - 1:
                    row[f'units{days}d'] += units
                    row['byView']['complete'][f'units{days}d'] += units
                    row['byView'][view][f'units{days}d'] += units
            if not row['lastSaleDate'] or dt.isoformat() > row['lastSaleDate']:
                row['lastSaleDate'] = dt.isoformat()

    for row in metrics.values():
        for days in windows:
            row[f'units{days}d'] = round(row.get(f'units{days}d', 0.0), 2)
        row['byView'] = {
            key: {
                metric_key: round(metric_value, 2)
                for metric_key, metric_value in value.items()
            }
            for key, value in row['byView'].items()
        }
    return metrics


def aggregate_exact_inventory(items):
    grouped = {}
    for item in items or []:
        raw_code = normalize_product_code(item.get('sku_code'))
        if not raw_code:
            continue
        row = grouped.setdefault(raw_code, {
            'code': raw_code,
            'availableStock': 0.0,
            'pendingStock': 0.0,
            'freezeStock': 0.0,
            'onwayStock': 0.0,
        })
        row['availableStock'] += num(item.get('available_stock'))
        row['pendingStock'] += num(item.get('pending_stock'))
        row['freezeStock'] += num(item.get('freeze_stock'))
        row['onwayStock'] += num(item.get('onway_stock'))
    for row in grouped.values():
        row['availableStock'] = round(row['availableStock'], 2)
        row['pendingStock'] = round(row['pendingStock'], 2)
        row['freezeStock'] = round(row['freezeStock'], 2)
        row['onwayStock'] = round(row['onwayStock'], 2)
    return grouped


def fetch_expiry_exact_sales_orders(url, access_token, end_dt, *, pos_view_ids=None, window_days=90, limit=1000):
    if not url or not access_token or end_dt is None:
        return []

    start_dt = (end_dt - timedelta(days=max(int(window_days or 0) - 1, 0))).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    orders = fetch_wpj_year_order_metrics(url, access_token, start_dt, end_dt, limit=limit)
    return apply_pos_view_overrides_to_orders(
        orders,
        url,
        access_token,
        start_dt,
        end_dt,
        detailed=False,
        pos_view_ids=pos_view_ids,
        limit=limit,
    )


def fetch_store_expiry_sales_orders(ctx: RefreshRuntimeContext, rows: list[dict[str, Any]]):
    if not rows or not wpj_endpoint() or not SETTINGS.wpj_access_token:
        return [], []

    active_rows = [row for row in rows if row.get('active')]
    if not active_rows:
        return [], []

    warnings = []
    received_dates = [parse_dt(row.get('receivedDate')) for row in active_rows if row.get('receivedDate')]
    if received_dates:
        start_dt = min(received_dates).replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        start_dt = ctx.report_end - timedelta(days=365)
        warnings.append('Cast batchu nema datum naskladneni, prodeje se proto pocitaji jen z poslednich 365 dnu.')

    fetched = []
    for view in {'ltm', 'mecin'}:
        if not any(row.get('storeView') == view for row in active_rows):
            continue
        pos_ids = ctx.pos_view_filters.get(view) or []
        if not pos_ids:
            warnings.append(f'Chybi POS mapping pro prodejnu {STORE_EXPIRY_VIEW_LABELS.get(view, view)}.')
            continue
        for pos_id in pos_ids:
            fetched.extend(fetch_wpj_orders(
                wpj_endpoint(),
                SETTINGS.wpj_access_token,
                start_dt,
                ctx.report_end,
                limit=1000,
                detailed=True,
                pos_id=pos_id,
                classified_view=view,
            ))

    return fetched, warnings


def build_store_expiry_watchdog(
    generated_at,
    now_local,
    store_expiry_input,
    wpj_products=None,
    sales_orders=None,
    manual_overrides=None,
    pos_admin_views=None,
):
    wpj_products = wpj_products or []
    sales_orders = sales_orders or []
    manual_overrides = manual_overrides or {'aliases': {}, 'ignore': set()}
    pos_admin_views = pos_admin_views or {}
    source = dict(store_expiry_input.get('source') or {})
    warnings = list(store_expiry_input.get('warnings') or [])
    raw_rows = [row for row in (store_expiry_input.get('rows') or []) if row.get('active', True)]
    wpj_by_code = {item.get('code'): item for item in wpj_products if item.get('code')}
    sales_metrics = collect_wpj_order_product_metrics(
        sales_orders,
        wpj_by_code=wpj_by_code,
        manual_overrides=manual_overrides,
        pos_admin_views=pos_admin_views,
    )

    rows_by_group = defaultdict(list)
    for row in raw_rows:
        rows_by_group[(row.get('storeView'), normalize_product_code(row.get('sku')))].append(dict(row))

    items = []
    group_summaries = []
    today = now_local.date()
    for (store_view, sku), group_rows in rows_by_group.items():
        group_rows.sort(key=lambda row: (
            row.get('receivedDate') or row.get('expiryDate') or '9999-12-31',
            row.get('expiryDate') or '9999-12-31',
            row.get('batch') or '',
        ))
        sales = sales_metrics.get(sku) or {}
        sold_units = round(((sales.get('byView') or {}).get(store_view) or {}).get('units', 0.0), 2)
        unmatched_sales_units = sold_units
        group_remaining = 0.0
        group_received = 0.0
        group_adjustments = 0.0

        for row in group_rows:
            received_units = round(num(row.get('receivedUnits')), 2)
            discarded_units = round(num(row.get('discardedUnits')), 2)
            transferred_units = round(num(row.get('transferredUnits')), 2)
            available_before_sales = round(max(received_units - discarded_units - transferred_units, 0.0), 2)
            allocated_sold_units = round(min(available_before_sales, unmatched_sales_units), 2)
            remaining_units = round(max(available_before_sales - allocated_sold_units, 0.0), 2)
            unmatched_sales_units = round(max(unmatched_sales_units - allocated_sold_units, 0.0), 2)
            expiry_dt = parse_dt(row.get('expiryDate'))
            days_to_expiry = (expiry_dt.date() - today).days if expiry_dt else None

            severity = 'ok'
            if remaining_units > 0 and days_to_expiry is not None:
                if days_to_expiry < 0:
                    severity = 'expired'
                elif days_to_expiry <= 14:
                    severity = 'critical'
                elif days_to_expiry <= 30:
                    severity = 'soon'
                elif days_to_expiry <= 60:
                    severity = 'watch'

            product = wpj_by_code.get(sku) or {}
            title = row.get('title') or product.get('title') or sku
            items.append({
                **row,
                'title': title,
                'soldUnitsFifo': allocated_sold_units,
                'remainingUnits': remaining_units,
                'availableBeforeSales': available_before_sales,
                'daysToExpiry': days_to_expiry,
                'severity': severity,
                'salesSourceUnits': sold_units,
                'sourceCodes': sorted(set((sales.get('sourceCodes') or []) + [sku])),
            })
            group_remaining += remaining_units
            group_received += received_units
            group_adjustments += discarded_units + transferred_units

        group_summaries.append({
            'storeView': store_view,
            'storeLabel': STORE_EXPIRY_VIEW_LABELS.get(store_view, store_view.upper()),
            'sku': sku,
            'title': (wpj_by_code.get(sku) or {}).get('title') or group_rows[0].get('title') or sku,
            'receivedUnits': round(group_received, 2),
            'adjustedUnits': round(group_adjustments, 2),
            'soldUnits': round(sold_units, 2),
            'remainingUnits': round(group_remaining, 2),
            'unmatchedSalesUnits': round(unmatched_sales_units, 2),
        })
        if unmatched_sales_units > 0:
            warnings.append(
                f'{STORE_EXPIRY_VIEW_LABELS.get(store_view, store_view)} / {sku}: prodeje presahuji sledovane batche o {format_units(unmatched_sales_units)}.'
            )

    visible_items = [item for item in items if item.get('remainingUnits', 0) > 0]
    severity_order = {'expired': 0, 'critical': 1, 'soon': 2, 'watch': 3, 'ok': 4}
    visible_items.sort(key=lambda item: (
        severity_order.get(item.get('severity'), 9),
        item.get('daysToExpiry') if item.get('daysToExpiry') is not None else 9999,
        -(item.get('remainingUnits') or 0),
        item.get('storeView') or '',
        item.get('sku') or '',
    ))
    group_summaries.sort(key=lambda item: (item.get('storeView') or '', -(item.get('remainingUnits') or 0), item.get('sku') or ''))

    summary = {
        'inputRows': len(store_expiry_input.get('rows') or []),
        'activeRows': len(raw_rows),
        'trackedGroups': len(group_summaries),
        'visibleRows': len(visible_items),
        'totalRemainingUnits': round(sum(item.get('remainingUnits') or 0 for item in visible_items), 2),
        'expiredRows': sum(1 for item in visible_items if item.get('severity') == 'expired'),
        'criticalRows': sum(1 for item in visible_items if item.get('severity') == 'critical'),
        'soonRows': sum(1 for item in visible_items if item.get('severity') == 'soon'),
        'watchRows': sum(1 for item in visible_items if item.get('severity') == 'watch'),
        'storesTracked': sorted({item.get('storeView') for item in raw_rows if item.get('storeView')}),
        'salesOrdersProcessed': len(sales_orders),
        'sourceWarnings': len(warnings),
    }
    alerts = []
    if summary['expiredRows']:
        alerts.append(f'{summary["expiredRows"]} batchu po expiraci stale drzi zbyvajici kusy.')
    if summary['criticalRows']:
        alerts.append(f'{summary["criticalRows"]} batchu je do 14 dnu.')
    if not visible_items and raw_rows:
        alerts.append('Vsechny sledovane batchy jsou uz odprodane nebo uzavrene.')
    if not raw_rows:
        alerts.append('Store expiry vstup zatim nema zadne aktivni radky.')

    return {
        'generatedAt': generated_at,
        'source': source,
        'summary': summary,
        'alerts': alerts,
        'warnings': warnings,
        'groups': group_summaries,
        'items': visible_items,
        'allItems': items,
    }


def normalize_product_code(value):
    return str(value or '').strip()


def merge_reference_meta(base, override):
    merged = dict(base or {})
    for key, value in (override or {}).items():
        if value is None:
            continue
        merged[key] = value
    return merged


def infer_ordering_reference(code, title, unit_price=None):
    title_text = str(title or '')
    title_lower = title_text.lower()
    code_text = normalize_product_code(code)

    meta = {
        'itemType': 'product',
        'orderable': True,
        'sourceChannel': 'unknown',
        'strategicPriority': 'standard',
        'giftCandidate': False,
        'excludeFromOrderingReason': None,
        'referenceSource': 'default',
        'referenceFlags': [],
    }

    def mark(update, source, flag=None):
        nonlocal meta
        meta = merge_reference_meta(meta, update)
        meta['referenceSource'] = source
        if flag and flag not in meta['referenceFlags']:
            meta['referenceFlags'].append(flag)

    if code_text.startswith('BIOANALYZA'):
        mark({
            'itemType': 'service',
            'orderable': False,
            'sourceChannel': 'praha',
            'strategicPriority': 'risky',
            'excludeFromOrderingReason': 'servisní položka / biorezonance',
        }, 'heuristic:prefix', 'service_prefix')

    if code_text.startswith('DOPL'):
        mark({
            'itemType': 'internal',
            'orderable': False,
            'sourceChannel': 'praha',
            'strategicPriority': 'risky',
            'excludeFromOrderingReason': 'interní nebo doplňková položka',
        }, 'heuristic:prefix', 'internal_prefix')

    if code_text.startswith('SET'):
        mark({
            'itemType': 'promo',
            'orderable': False,
            'sourceChannel': 'unknown',
            'strategicPriority': 'supplement',
            'excludeFromOrderingReason': 'promo / set candidate k ruční revizi',
        }, 'heuristic:prefix', 'set_prefix')

    if any(token in title_lower for token in ('brož', 'leták', 'katalog', 'diář', 'lexikon', 'zpravodaj', 'tiskovin', 'pexeso')):
        mark({
            'itemType': 'print',
            'orderable': False,
            'sourceChannel': 'praha',
            'strategicPriority': 'supplement',
            'excludeFromOrderingReason': 'tiskovina / katalog / brožura',
        }, 'heuristic:title', 'print_title')

    if any(token in title_lower for token in ('dárek', 'zdarma', 'překvapení')):
        mark({
            'itemType': 'gift',
            'orderable': False,
            'sourceChannel': 'praha',
            'strategicPriority': 'supplement',
            'giftCandidate': True,
            'excludeFromOrderingReason': 'dárková nebo bonusová položka',
        }, 'heuristic:title', 'gift_title')

    if any(token in title_lower for token in ('tester', 'testerů', 'vzorek')):
        mark({
            'itemType': 'promo',
            'orderable': False,
            'sourceChannel': 'praha',
            'strategicPriority': 'supplement',
            'excludeFromOrderingReason': 'tester / vzorek / promo materiál',
        }, 'heuristic:title', 'promo_title')

    if meta['itemType'] == 'product':
        if unit_price is not None and float(unit_price or 0) <= 0:
            meta['referenceFlags'].append('nonpositive_price')
            meta['strategicPriority'] = 'risky'
            meta['referenceSource'] = 'heuristic:price'
        if meta['sourceChannel'] == 'unknown':
            meta['sourceChannel'] = 'both'

    return meta


def apply_ordering_reference_overrides(reference_meta, code, title, overrides):
    meta = dict(reference_meta or {})
    code_text = normalize_product_code(code)
    title_lower = str(title or '').lower()
    title_key = normalize_lookup_key(title)
    title_stem_key = normalize_title_stem_key(title)

    title_override = (overrides.get('titles') or {}).get(title_key)
    if title_override:
        meta = merge_reference_meta(meta, title_override)
        meta['referenceSource'] = 'override:title_exact'

    stem_override = (overrides.get('titleStems') or {}).get(title_stem_key)
    if stem_override and meta.get('referenceSource') != 'override:title_exact':
        meta = merge_reference_meta(meta, stem_override)
        meta['referenceSource'] = 'override:title_stem'

    sku_override = (overrides.get('skus') or {}).get(code_text)
    if sku_override:
        meta = merge_reference_meta(meta, sku_override)
        meta['referenceSource'] = 'override:sku'

    for entry in (overrides.get('prefixes') or []):
        prefix = entry.get('prefix')
        if prefix and code_text.startswith(prefix):
            meta = merge_reference_meta(meta, entry.get('meta'))
            meta['referenceSource'] = 'override:prefix'

    for entry in (overrides.get('titleContains') or []):
        needle = entry.get('needle')
        if needle and needle in title_lower:
            meta = merge_reference_meta(meta, entry.get('meta'))
            meta['referenceSource'] = 'override:title'

    if not meta.get('orderable') and not meta.get('excludeFromOrderingReason'):
        meta['excludeFromOrderingReason'] = 'ručně vyloučeno z objednávání'

    return meta


def reapply_ordering_reference_to_analytics(payload, overrides):
    if not payload or not payload.get('items'):
        return payload, False

    changed = False
    for item in payload.get('items') or []:
        next_meta = apply_ordering_reference_overrides(
            infer_ordering_reference(item.get('code'), item.get('title'), item.get('unitSellingPrice')),
            item.get('code'),
            item.get('title'),
            overrides or {},
        )
        next_flags = sorted(set(next_meta.get('referenceFlags') or []))
        fields = {
            'itemType': next_meta.get('itemType'),
            'orderable': bool(next_meta.get('orderable')),
            'sourceChannel': next_meta.get('sourceChannel'),
            'strategicPriority': next_meta.get('strategicPriority'),
            'giftCandidate': bool(next_meta.get('giftCandidate')),
            'excludeFromOrderingReason': next_meta.get('excludeFromOrderingReason'),
            'referenceSource': next_meta.get('referenceSource'),
            'referenceFlags': next_flags,
        }
        for key, value in fields.items():
            if item.get(key) != value:
                item[key] = value
                changed = True

    return payload, changed


def reapply_ordering_packaging_to_analytics(payload, ordering_packaging_map):
    if not payload or not payload.get('items'):
        return payload, False

    changed = False
    next_items = []
    for item in payload.get('items') or []:
        next_item = enrich_item_with_packaging(item, ordering_packaging_map)
        if next_item != item:
            changed = True
        next_items.append(next_item)
    if changed:
        payload['items'] = next_items
    return payload, changed


def reapply_combined_stock_to_analytics(payload, combined_index, market_key='complete'):
    if not payload or not payload.get('items'):
        return payload, False

    combined_by_code = {item.get('code'): item for item in (combined_index or {}).get('items') or [] if item.get('code')}
    changed = False
    for item in payload.get('items') or []:
        combined_item = combined_by_code.get(item.get('code')) or {}
        if market_key == 'cz':
            next_stock = round(num((((combined_item.get('fourpx') or {}).get('cz') or {}).get('availableStock'))), 2)
        elif market_key == 'sk':
            next_stock = round(num((((combined_item.get('fourpx') or {}).get('sk') or {}).get('availableStock'))), 2)
        else:
            next_stock = round(num(((combined_item.get('fourpx') or {}).get('availableTotal'))), 2)
            if next_stock <= 0:
                next_stock = round(num(item.get('effectiveStock')), 2)
        next_wpj_total = round(num(((combined_item.get('wpj') or {}).get('fourpxStoreTotal'))), 2)

        if round(num(item.get('effectiveStock')), 2) != next_stock:
            item['effectiveStock'] = next_stock
            changed = True
        if round(num(item.get('fourpxAvailable')), 2) != next_stock:
            item['fourpxAvailable'] = next_stock
            changed = True
        if 'wpj4pxStoreTotal' in item and round(num(item.get('wpj4pxStoreTotal')), 2) != next_wpj_total:
            item['wpj4pxStoreTotal'] = next_wpj_total
            changed = True
        next_selling_value = round(next_stock * money(item.get('unitSellingPrice')), 2) if item.get('unitSellingPrice') else None
        if item.get('stockValueSelling') != next_selling_value:
            item['stockValueSelling'] = next_selling_value
            changed = True
        next_abra_value = round(next_stock * money(item.get('unitCostAbraAvg')), 2) if item.get('unitCostAbraAvg') else None
        if item.get('stockValueAbraAvg') != next_abra_value:
            item['stockValueAbraAvg'] = next_abra_value
            changed = True
    return payload, changed


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
            'variantBreakdown': {},
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
        variant = row['variantBreakdown'].setdefault(raw_code, {
            'sourceCode': raw_code,
            'skuIds': set(),
            'batchNos': set(),
            'availableStock': 0.0,
            'pendingStock': 0.0,
            'freezeStock': 0.0,
            'onwayStock': 0.0,
        })
        if item.get('sku_id'):
            variant['skuIds'].add(item['sku_id'])
        if item.get('batch_no'):
            variant['batchNos'].add(item['batch_no'])
        variant['availableStock'] += num(item.get('available_stock'))
        variant['pendingStock'] += num(item.get('pending_stock'))
        variant['freezeStock'] += num(item.get('freeze_stock'))
        variant['onwayStock'] += num(item.get('onway_stock'))
    for row in grouped.values():
        row['sourceCodes'] = sorted(row['sourceCodes'])
        row['mappedSourceCodes'] = sorted(row['mappedSourceCodes'])
        row['manualMappedSourceCodes'] = sorted(row['manualMappedSourceCodes'])
        row['autoMappedSourceCodes'] = sorted(row['autoMappedSourceCodes'])
        row['mappingRules'] = sorted(row['mappingRules'])
        row['skuIds'] = sorted(row['skuIds'])
        row['batchNos'] = sorted(row['batchNos'])
        row['variantBreakdown'] = [
            {
                **variant,
                'skuIds': sorted(variant['skuIds']),
                'batchNos': sorted(variant['batchNos']),
            }
            for _, variant in sorted(row['variantBreakdown'].items())
        ]
    return grouped


def merge_4px_variant_breakdown(cz_variants, sk_variants):
    grouped = {}
    for market, variants in (('cz', cz_variants or []), ('sk', sk_variants or [])):
        for variant in variants:
            code = normalize_product_code(variant.get('sourceCode'))
            if not code:
                continue
            row = grouped.setdefault(code, {
                'sourceCode': code,
                'availableTotal': 0.0,
                'pendingTotal': 0.0,
                'freezeTotal': 0.0,
                'onwayTotal': 0.0,
                'czAvailableStock': 0.0,
                'skAvailableStock': 0.0,
                'czPendingStock': 0.0,
                'skPendingStock': 0.0,
                'czFreezeStock': 0.0,
                'skFreezeStock': 0.0,
                'czOnwayStock': 0.0,
                'skOnwayStock': 0.0,
                'skuIds': set(),
                'batchNos': set(),
            })
            available = num(variant.get('availableStock'))
            pending = num(variant.get('pendingStock'))
            freeze = num(variant.get('freezeStock'))
            onway = num(variant.get('onwayStock'))
            row['availableTotal'] += available
            row['pendingTotal'] += pending
            row['freezeTotal'] += freeze
            row['onwayTotal'] += onway
            row[f'{market}AvailableStock'] += available
            row[f'{market}PendingStock'] += pending
            row[f'{market}FreezeStock'] += freeze
            row[f'{market}OnwayStock'] += onway
            row['skuIds'].update(variant.get('skuIds') or [])
            row['batchNos'].update(variant.get('batchNos') or [])
    return [
        {
            **variant,
            'skuIds': sorted(variant['skuIds']),
            'batchNos': sorted(variant['batchNos']),
        }
        for _, variant in sorted(grouped.items())
    ]


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


def build_combined_product_views(ctx: CombinedProductsBuildContext):
    wpj_by_code = {item.get('code'): item for item in ctx.wpj_products if item.get('code')}
    order_metrics = collect_wpj_order_product_metrics(ctx.yesterday_orders, wpj_by_code, ctx.manual_overrides, ctx.pos_admin_views)
    cz_inventory_by_code = aggregate_4px_inventory(ctx.cz_inventory.get('items') or [], wpj_by_code, ctx.manual_overrides)
    sk_inventory_by_code = aggregate_4px_inventory(ctx.sk_inventory.get('items') or [], wpj_by_code, ctx.manual_overrides)
    cz_outbound_by_code = aggregate_4px_outbound_by_sku(ctx.cz_outbound, ctx.start_dt, ctx.end_dt, 'CZ', wpj_by_code, ctx.manual_overrides)
    sk_outbound_by_code = aggregate_4px_outbound_by_sku(ctx.sk_outbound, ctx.start_dt, ctx.end_dt, 'SK', wpj_by_code, ctx.manual_overrides)

    all_codes = set(wpj_by_code) | set(cz_inventory_by_code) | set(sk_inventory_by_code) | set(cz_outbound_by_code) | set(sk_outbound_by_code) | set(order_metrics)
    items = []
    auto_mapped_aliases = set()
    manual_mapped_aliases = set()

    for code in sorted(all_codes):
        wpj = wpj_by_code.get(code)
        stores = store_stock_breakdown(wpj) if wpj else []
        has_wpj_4px_context = any((store.get('storeName') or '').startswith('4PX') for store in stores)
        wpj_fourpx_total = round(sum(store['inStore'] for store in stores if (store.get('storeName') or '').startswith('4PX')), 2)
        wpj_total_store = round(sum(store['inStore'] for store in stores), 2)
        fourpx_cz = cz_inventory_by_code.get(code, {'availableStock': 0.0, 'pendingStock': 0.0, 'freezeStock': 0.0, 'onwayStock': 0.0, 'batchNos': [], 'skuIds': [], 'sourceCodes': [], 'mappedSourceCodes': [], 'mappingRules': [], 'variantBreakdown': []})
        fourpx_sk = sk_inventory_by_code.get(code, {'availableStock': 0.0, 'pendingStock': 0.0, 'freezeStock': 0.0, 'onwayStock': 0.0, 'batchNos': [], 'skuIds': [], 'sourceCodes': [], 'mappedSourceCodes': [], 'mappingRules': [], 'variantBreakdown': []})
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
        variant_breakdown = merge_4px_variant_breakdown(
            fourpx_cz.get('variantBreakdown'),
            fourpx_sk.get('variantBreakdown'),
        )
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
                'totalStore': wpj_total_store,
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
                'variantBreakdown': variant_breakdown,
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
        'generatedAt': ctx.generated_at,
        'window': {'from': ctx.start_dt.isoformat(), 'to': ctx.end_dt.isoformat()},
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
        'generatedAt': ctx.generated_at,
        'window': {'from': ctx.start_dt.isoformat(), 'to': ctx.end_dt.isoformat()},
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


def build_inventory_analytics_window(ctx: InventoryAnalyticsBuildContext):
    wpj_by_code = ctx.wpj_by_code or {}
    window_days = ctx.window_days
    metrics = {}
    view_keys = ('complete', 'cz', 'sk', 'ltm', 'mecin')
    metric_windows = tuple(dict.fromkeys((ctx.window_days, 365, 180, 90, 30, 14)))
    abra_costs_by_code = fetch_abra_average_cost_map([
        item.get('code')
        for item in (ctx.combined_index.get('items') or [])
        if item.get('code')
    ])

    for order in ctx.orders:
        dt = parse_dt(order.get('dateCreated'))
        if not dt:
            continue
        days_ago = (ctx.end_dt.date() - dt.date()).days
        if days_ago < 0 or days_ago > max(metric_windows) - 1:
            continue
        view = classify_order_view(order, ctx.pos_admin_views)
        for item in order.get('items') or []:
            if item.get('type') != 'product':
                continue
            raw_code = item.get('code') or item.get('name') or '–'
            code, _mapping = resolve_4px_code_alias(raw_code, wpj_by_code, ctx.manual_overrides)
            row = metrics.setdefault(code, {
                'code': code,
                'name': (wpj_by_code.get(code) or {}).get('title') or item.get('name') or 'Bez názvu',
                'lastSaleDate': None,
                'byView': {
                    key: {f'units{days}d': 0.0 for days in metric_windows}
                    for key in view_keys
                },
                **{f'units{days}d': 0.0 for days in metric_windows},
            })
            units = num(item.get('pieces'))
            for days in metric_windows:
                if days_ago <= days - 1:
                    row[f'units{days}d'] += units
                    row['byView']['complete'][f'units{days}d'] += units
                    row['byView'][view][f'units{days}d'] += units
            if not row['lastSaleDate'] or dt.isoformat() > row['lastSaleDate']:
                row['lastSaleDate'] = dt.isoformat()

    items = []
    for item in ctx.combined_index.get('items') or []:
        code = item['code']
        metric = metrics.get(code, {
            **{f'units{days}d': 0.0 for days in metric_windows},
            'lastSaleDate': None,
            'name': item['title'],
            'byView': {key: {f'units{days}d': 0.0 for days in metric_windows} for key in view_keys},
        })
        effective_stock = item['fourpx']['availableTotal'] if item['fourpx']['availableTotal'] > 0 else item['wpj']['fourpxStoreTotal']
        if effective_stock <= 0:
            effective_stock = item['wpj'].get('totalStore') or 0
        units_window = round(metric.get(f'units{ctx.window_days}d', 0.0), 2)
        units730d = units_window
        units365d = round(metric.get('units365d', 0.0), 2)
        units180d = round(metric.get('units180d', 0.0), 2)
        units90d = round(metric.get('units90d', 0.0), 2)
        units30d = round(metric.get('units30d', 0.0), 2)
        units14d = round(metric.get('units14d', 0.0), 2)
        prev365d = round(max(units730d - units365d, 0), 2)

        daily_run_rate_730 = units_window / window_days if units_window else 0.0
        daily_run_rate_365 = units365d / 365 if units365d else 0.0
        daily_run_rate_90 = units90d / 90 if units90d else 0.0
        days_of_cover_730 = round(effective_stock / daily_run_rate_730, 1) if daily_run_rate_730 > 0 else None
        days_of_cover_365 = round(effective_stock / daily_run_rate_365, 1) if daily_run_rate_365 > 0 else None
        days_of_cover_90 = round(effective_stock / daily_run_rate_90, 1) if daily_run_rate_90 > 0 else None
        stock_months_on_hand = round((days_of_cover_90 or days_of_cover_365 or 0) / 30.4, 1) if (days_of_cover_90 or days_of_cover_365) else None

        last_sale_dt = parse_dt(metric.get('lastSaleDate'))
        days_since_last_sale = (ctx.end_dt.date() - last_sale_dt.date()).days if last_sale_dt else None
        selling_value = round(effective_stock * money(item.get('wpj', {}).get('priceWithVat')), 2) if item.get('wpj', {}).get('priceWithVat') else None

        trend_pct = pct_delta(daily_run_rate_90, daily_run_rate_365) if daily_run_rate_365 else None
        yoy_pct = pct_delta(units365d, prev365d) if prev365d else None
        if stock_months_on_hand is None:
            turnover_zone = 'no_sales'
        elif stock_months_on_hand <= 1:
            turnover_zone = 'green'
        elif stock_months_on_hand <= 4:
            turnover_zone = 'orange'
        else:
            turnover_zone = 'red'

        recommended_min_units = ordering_target_units(daily_run_rate_90, effective_stock, ORDERING_TARGET_COVER_DAYS)
        recommended_order_units = ordering_target_units(daily_run_rate_90, effective_stock, ORDERING_TARGET_COVER_DAYS)
        reorder_risk = 'none'
        if units90d > 0:
            if days_of_cover_90 is None or days_of_cover_90 <= 14:
                reorder_risk = 'critical'
            elif days_of_cover_90 <= 30:
                reorder_risk = 'soon'
            elif days_of_cover_90 <= 60:
                reorder_risk = 'watch'

        tags = []
        if effective_stock > 0 and units365d == 0:
            tags.append('dead_stock')
        if effective_stock > 0 and days_since_last_sale is not None and days_since_last_sale >= 90:
            tags.append('slow_mover')
        if effective_stock > 0 and days_of_cover_365 is not None and days_of_cover_365 >= 365:
            tags.append('overstocked')
        if effective_stock > 0 and days_of_cover_365 is not None and days_of_cover_365 <= 30 and units365d > 0:
            tags.append('fast_mover_low_cover')
        if reorder_risk in {'critical', 'soon'}:
            tags.append('reorder_candidate')
        if turnover_zone == 'red':
            tags.append('turnover_red')
        elif turnover_zone == 'orange':
            tags.append('turnover_orange')
        elif turnover_zone == 'green':
            tags.append('turnover_green')

        by_view = {}
        for view_key in view_keys:
            view_metric = (metric.get('byView') or {}).get(view_key) or {}
            view_units_730d = round(view_metric.get(f'units{window_days}d', 0.0), 2)
            view_units_365d = round(view_metric.get('units365d', 0.0), 2)
            by_view[view_key] = {
                'units730d': view_units_730d,
                'units365d': view_units_365d,
                'units180d': round(view_metric.get('units180d', 0.0), 2),
                'units90d': round(view_metric.get('units90d', 0.0), 2),
                'units30d': round(view_metric.get('units30d', 0.0), 2),
                'units14d': round(view_metric.get('units14d', 0.0), 2),
                'avgMonthlyUnits365d': round(view_units_365d / 12, 1) if view_units_365d else 0,
                'avgMonthlyUnits730d': round(view_units_730d / 24, 1) if view_units_730d else 0,
            }

        reference_meta = apply_ordering_reference_overrides(
            infer_ordering_reference(code, item['title'], item.get('wpj', {}).get('priceWithVat')),
            code,
            item['title'],
            ctx.ordering_reference_overrides or {},
        )
        abra_cost_meta = abra_costs_by_code.get(code) or {}
        unit_cost_abra = money(abra_cost_meta.get('unitCostAbraAvg')) if abra_cost_meta.get('unitCostAbraAvg') else None

        analytics_item = {
            'code': code,
            'title': item['title'],
            'effectiveStock': round(effective_stock, 2),
            'unitSellingPrice': money(item.get('wpj', {}).get('priceWithVat')) if item.get('wpj', {}).get('priceWithVat') else None,
            'unitCostAbraAvg': unit_cost_abra,
            'fourpxAvailable': item['fourpx']['availableTotal'],
            'wpj4pxStoreTotal': item['wpj']['fourpxStoreTotal'],
            'units730d': units730d,
            'unitsWindow': units_window,
            'units365d': units365d,
            'units180d': units180d,
            'units90d': units90d,
            'units30d': units30d,
            'units14d': units14d,
            'prev365d': prev365d,
            'czUnits365d': by_view['cz']['units365d'],
            'skUnits365d': by_view['sk']['units365d'],
            'ltmUnits365d': by_view['ltm']['units365d'],
            'mecinUnits365d': by_view['mecin']['units365d'],
            'avgMonthlyUnits365d': round(units365d / 12, 1) if units365d else 0,
            'avgMonthlyUnits730d': round(units730d / 24, 1) if units730d else 0,
            'avgMonthlyUnitsWindow': round(units_window / max(window_days / 30.4167, 1), 1) if units_window else 0,
            'dailyRunRate730d': round(daily_run_rate_730, 3),
            'dailyRunRate365d': round(daily_run_rate_365, 3),
            'dailyRunRate90d': round(daily_run_rate_90, 3),
            'daysOfCover730d': days_of_cover_730,
            'daysOfCoverWindow': days_of_cover_730,
            'daysOfCover365d': days_of_cover_365,
            'daysOfCover90d': days_of_cover_90,
            'stockMonthsOnHand': stock_months_on_hand,
            'turnoverZone': turnover_zone,
            'reorderRisk': reorder_risk,
            'recommendedMinUnits': recommended_min_units,
            'recommendedOrderUnits': recommended_order_units,
            'trend90v365Pct': trend_pct,
            'seasonalityYoYPct': yoy_pct,
            'lastSaleDate': metric['lastSaleDate'],
            'daysSinceLastSale': days_since_last_sale,
            'stockValueSelling': selling_value,
            'stockValueAbraAvg': round(effective_stock * unit_cost_abra, 2) if unit_cost_abra else None,
            'tags': tags,
            'byView': by_view,
            'itemType': reference_meta.get('itemType'),
            'orderable': bool(reference_meta.get('orderable')),
            'sourceChannel': reference_meta.get('sourceChannel'),
            'strategicPriority': reference_meta.get('strategicPriority'),
            'giftCandidate': bool(reference_meta.get('giftCandidate')),
            'excludeFromOrderingReason': reference_meta.get('excludeFromOrderingReason'),
            'referenceSource': reference_meta.get('referenceSource'),
            'referenceFlags': sorted(set(reference_meta.get('referenceFlags') or [])),
        }
        items.append(enrich_item_with_packaging(analytics_item, ctx.ordering_packaging_map))

    abc_by_code = classify_abc_buckets(items)
    for item in items:
        abc_meta = abc_by_code.get(item.get('code'))
        if abc_meta:
            item.update(abc_meta)
        else:
            item.update({
                'abcClass': None if item.get('orderable') is False else 'C',
                'abcRank': None,
                'abcRevenue365d': round(max(0.0, float(item.get('units365d') or 0)) * max(0.0, float(item.get('unitSellingPrice') or 0)), 2),
                'abcRevenueShare': 0.0,
                'abcUnitsShare': 0.0,
                'abcCombinedScore': 0.0,
                'abcCumulativeShare': None,
            })

    turnover = sorted([item for item in items if item['units365d'] > 0 and item['effectiveStock'] > 0], key=lambda item: item['units365d'], reverse=True)
    dead_stock = sorted([item for item in items if 'dead_stock' in item['tags']], key=lambda item: item['effectiveStock'], reverse=True)
    slow_movers = sorted([item for item in items if 'slow_mover' in item['tags'] and item['effectiveStock'] > 0], key=lambda item: ((item['daysSinceLastSale'] or 0), item['effectiveStock']), reverse=True)
    overstocked = sorted([item for item in items if 'overstocked' in item['tags'] and item['effectiveStock'] > 0], key=lambda item: (item['daysOfCover365d'] or 0), reverse=True)
    fast_low_cover = sorted([item for item in items if 'fast_mover_low_cover' in item['tags']], key=lambda item: (item['daysOfCover365d'] or 999, -item['units365d']))

    return {
        'generatedAt': ctx.generated_at,
        'window': {'from': ctx.start_dt.isoformat(), 'to': ctx.end_dt.isoformat(), 'days': ctx.window_days},
        'summary': {
            'trackedItems': len(items),
            'turnoverItems': len(turnover),
            'deadStockItems': len(dead_stock),
            'slowMoverItems': len(slow_movers),
            'overstockedItems': len(overstocked),
            'fastLowCoverItems': len(fast_low_cover),
            'criticalReorderItems': len([item for item in items if item['reorderRisk'] == 'critical']),
            'reorderSoonItems': len([item for item in items if item['reorderRisk'] == 'soon']),
            'redTurnoverItems': len([item for item in items if item['turnoverZone'] == 'red']),
            'orangeTurnoverItems': len([item for item in items if item['turnoverZone'] == 'orange']),
            'greenTurnoverItems': len([item for item in items if item['turnoverZone'] == 'green']),
        },
        'items': items,
        'topTurnover': turnover[:50],
        'deadStock': dead_stock[:100],
        'slowMovers': slow_movers[:100],
        'overstocked': overstocked[:100],
        'fastLowCover': fast_low_cover[:100],
    }


def build_ordering_sales_history_payload(ctx: OrderingSalesHistoryBuildContext):
    wpj_by_code = ctx.wpj_by_code or {}
    view_keys = ('complete', 'cz', 'sk', 'ltm', 'mecin')
    codes = {}
    max_seen_day = None
    summary = {
        'trackedCodes': 0,
        'unitsTotal': 0.0,
        'byView': {
            key: {'unitsTotal': 0.0, 'codesWithSales': 0, 'saleDays': 0}
            for key in view_keys
        },
    }

    for order in ctx.orders:
        dt = parse_dt(order.get('dateCreated'))
        if not dt or dt < ctx.start_dt or dt > ctx.end_dt:
            continue
        view = classify_order_view(order, ctx.pos_admin_views)
        day_key = dt.date().isoformat()
        if max_seen_day is None or day_key > max_seen_day:
            max_seen_day = day_key
        for item in order.get('items') or []:
            if item.get('type') != 'product':
                continue
            raw_code = item.get('code') or item.get('name') or '–'
            code, _mapping = resolve_4px_code_alias(raw_code, wpj_by_code, ctx.manual_overrides)
            row = codes.setdefault(code, {
                'code': code,
                'title': (wpj_by_code.get(code) or {}).get('title') or item.get('name') or 'Bez názvu',
                'lastSaleDate': None,
                'dailyByView': {key: defaultdict(float) for key in view_keys},
                'totalsByView': {key: 0.0 for key in view_keys},
            })
            units = num(item.get('pieces'))
            row['dailyByView']['complete'][day_key] += units
            row['dailyByView'][view][day_key] += units
            row['totalsByView']['complete'] += units
            row['totalsByView'][view] += units
            if not row['lastSaleDate'] or dt.isoformat() > row['lastSaleDate']:
                row['lastSaleDate'] = dt.isoformat()

    for code, row in codes.items():
        has_any_sales = False
        for view_key in view_keys:
            sales_map = row['dailyByView'][view_key]
            row['dailyByView'][view_key] = [
                [day, round(units, 2)]
                for day, units in sorted(sales_map.items())
                if units
            ]
            row['totalsByView'][view_key] = round(row['totalsByView'][view_key], 2)
            if row['dailyByView'][view_key]:
                summary['byView'][view_key]['saleDays'] += len(row['dailyByView'][view_key])
                summary['byView'][view_key]['unitsTotal'] += row['totalsByView'][view_key]
                if row['totalsByView'][view_key] > 0:
                    summary['byView'][view_key]['codesWithSales'] += 1
            if view_key == 'complete' and row['totalsByView'][view_key] > 0:
                has_any_sales = True
                summary['unitsTotal'] += row['totalsByView'][view_key]
        if has_any_sales:
            summary['trackedCodes'] += 1

    summary['unitsTotal'] = round(summary['unitsTotal'], 2)
    for view_key in view_keys:
        summary['byView'][view_key]['unitsTotal'] = round(summary['byView'][view_key]['unitsTotal'], 2)

    effective_end = parse_dt(max_seen_day) if max_seen_day else ctx.end_dt

    return {
        'generatedAt': ctx.generated_at,
        'window': {
            'from': ctx.start_dt.isoformat(),
            'to': effective_end.isoformat(),
            'days': max((effective_end.date() - ctx.start_dt.date()).days + 1, 0),
        },
        'summary': summary,
        'codes': codes,
    }


def build_inventory_analytics_365d(ctx: InventoryAnalyticsBuildContext):
    return build_inventory_analytics_window(replace(ctx, window_days=365))


def build_inventory_analytics_730d(ctx: InventoryAnalyticsBuildContext):
    return build_inventory_analytics_window(replace(ctx, window_days=ORDERING_ANALYTICS_DAYS))


def enrich_inventory_analytics_prices(payload, wpj_by_code):
    if not payload or not payload.get('items'):
        return payload, False

    changed = False
    for item in payload.get('items') or []:
        code = item.get('code')
        wpj_item = (wpj_by_code or {}).get(code) or {}
        next_price = money((wpj_item.get('price') or {}).get('withVat')) if (wpj_item.get('price') or {}).get('withVat') else None
        if item.get('unitSellingPrice') != next_price:
            item['unitSellingPrice'] = next_price
            changed = True
        next_selling_value = round(max(0.0, num(item.get('effectiveStock'))) * max(0.0, money(next_price)), 2) if next_price else None
        if item.get('stockValueSelling') != next_selling_value:
            item['stockValueSelling'] = next_selling_value
            changed = True
    return payload, changed


def enrich_inventory_analytics_abra_costs(payload, abra_costs_by_code):
    if not payload or not payload.get('items'):
        return payload, False

    changed = False
    for item in payload.get('items') or []:
        cost_meta = (abra_costs_by_code or {}).get(normalize_product_code(item.get('code'))) or {}
        next_unit_cost = money(cost_meta.get('unitCostAbraAvg')) if cost_meta.get('unitCostAbraAvg') else None
        next_stock_value = round(max(0.0, num(item.get('effectiveStock'))) * max(0.0, money(next_unit_cost)), 2) if next_unit_cost else None
        if item.get('unitCostAbraAvg') != next_unit_cost:
            item['unitCostAbraAvg'] = next_unit_cost
            changed = True
        if item.get('stockValueAbraAvg') != next_stock_value:
            item['stockValueAbraAvg'] = next_stock_value
            changed = True
    return payload, changed


def reapply_inventory_recommendation_targets(payload):
    if not payload or not payload.get('items'):
        return payload, False

    changed = False
    for item in payload.get('items') or []:
        next_min = ordering_target_units(item.get('dailyRunRate90d'), item.get('effectiveStock'), ORDERING_TARGET_COVER_DAYS)
        next_recommended = ordering_target_units(item.get('dailyRunRate90d'), item.get('effectiveStock'), ORDERING_TARGET_COVER_DAYS)
        if int(item.get('recommendedMinUnits') or 0) != next_min:
            item['recommendedMinUnits'] = next_min
            changed = True
        if int(item.get('recommendedOrderUnits') or 0) != next_recommended:
            item['recommendedOrderUnits'] = next_recommended
            changed = True
    return payload, changed


def build_inventory_analytics_market_view(base_payload, combined_index, generated_at, market_key='complete'):
    if not base_payload or not base_payload.get('items'):
        return {'generatedAt': generated_at, 'market': market_key, 'window': (base_payload or {}).get('window') or {}, 'summary': {}, 'items': [], 'topTurnover': [], 'deadStock': [], 'slowMovers': [], 'overstocked': [], 'fastLowCover': []}

    combined_by_code = {item.get('code'): item for item in (combined_index.get('items') or []) if item.get('code')}
    window_days = int(((base_payload.get('window') or {}).get('days')) or ORDERING_ANALYTICS_DAYS)
    items = []

    for base_item in (base_payload.get('items') or []):
        code = base_item.get('code')
        combined_item = combined_by_code.get(code) or {}
        if market_key == 'cz':
            effective_stock = round(num((((combined_item.get('fourpx') or {}).get('cz') or {}).get('availableStock'))), 2)
        elif market_key == 'sk':
            effective_stock = round(num((((combined_item.get('fourpx') or {}).get('sk') or {}).get('availableStock'))), 2)
        else:
            effective_stock = round(num(((combined_item.get('fourpx') or {}).get('availableTotal'))), 2)
            if effective_stock <= 0:
                effective_stock = round(num(base_item.get('effectiveStock')), 2)

        market_view = ((base_item.get('byView') or {}).get(market_key) or {}) if market_key != 'complete' else ((base_item.get('byView') or {}).get('complete') or {})
        units_window = round(num(market_view.get('units730d' if window_days == ORDERING_ANALYTICS_DAYS else f'units{window_days}d')), 2)
        units730d = round(num(market_view.get('units730d')), 2)
        units365d = round(num(market_view.get('units365d')), 2)
        units180d = round(num(market_view.get('units180d')), 2)
        units90d = round(num(market_view.get('units90d')), 2)
        units30d = round(num(market_view.get('units30d')), 2)
        units14d = round(num(market_view.get('units14d')), 2)
        prev365d = round(max(units730d - units365d, 0), 2)

        daily_run_rate_730 = units_window / window_days if units_window else 0.0
        daily_run_rate_365 = units365d / 365 if units365d else 0.0
        daily_run_rate_90 = units90d / 90 if units90d else 0.0
        days_of_cover_730 = round(effective_stock / daily_run_rate_730, 1) if daily_run_rate_730 > 0 else None
        days_of_cover_365 = round(effective_stock / daily_run_rate_365, 1) if daily_run_rate_365 > 0 else None
        days_of_cover_90 = round(effective_stock / daily_run_rate_90, 1) if daily_run_rate_90 > 0 else None
        stock_months_on_hand = round((days_of_cover_90 or days_of_cover_365 or 0) / 30.4, 1) if (days_of_cover_90 or days_of_cover_365) else None

        trend_pct = pct_delta(daily_run_rate_90, daily_run_rate_365) if daily_run_rate_365 else None
        yoy_pct = pct_delta(units365d, prev365d) if prev365d else None
        if stock_months_on_hand is None:
            turnover_zone = 'no_sales'
        elif stock_months_on_hand <= 1:
            turnover_zone = 'green'
        elif stock_months_on_hand <= 4:
            turnover_zone = 'orange'
        else:
            turnover_zone = 'red'

        recommended_min_units = ordering_target_units(daily_run_rate_90, effective_stock, ORDERING_TARGET_COVER_DAYS)
        recommended_order_units = ordering_target_units(daily_run_rate_90, effective_stock, ORDERING_TARGET_COVER_DAYS)
        reorder_risk = 'none'
        if units90d > 0:
            if days_of_cover_90 is None or days_of_cover_90 <= 14:
                reorder_risk = 'critical'
            elif days_of_cover_90 <= 30:
                reorder_risk = 'soon'
            elif days_of_cover_90 <= 60:
                reorder_risk = 'watch'

        cover_metric = days_of_cover_90 if days_of_cover_90 is not None else days_of_cover_365

        tags = []
        if effective_stock > 0 and cover_metric is not None and cover_metric > 180:
            tags.append('dead_stock')
        elif effective_stock > 0 and cover_metric is not None and cover_metric > 90:
            tags.append('slow_mover')
        if effective_stock > 0 and days_of_cover_365 is not None and days_of_cover_365 >= 365:
            tags.append('overstocked')
        if effective_stock > 0 and days_of_cover_365 is not None and days_of_cover_365 <= 30 and units365d > 0:
            tags.append('fast_mover_low_cover')
        if reorder_risk in {'critical', 'soon'}:
            tags.append('reorder_candidate')
        if turnover_zone == 'red':
            tags.append('turnover_red')
        elif turnover_zone == 'orange':
            tags.append('turnover_orange')
        elif turnover_zone == 'green':
            tags.append('turnover_green')

        item = dict(base_item)
        item.update({
            'effectiveStock': effective_stock,
            'fourpxAvailable': effective_stock,
            'units730d': units730d,
            'unitsWindow': units_window,
            'units365d': units365d,
            'units180d': units180d,
            'units90d': units90d,
            'units30d': units30d,
            'units14d': units14d,
            'prev365d': prev365d,
            'avgMonthlyUnits365d': round(units365d / 12, 1) if units365d else 0,
            'avgMonthlyUnits730d': round(units730d / 24, 1) if units730d else 0,
            'avgMonthlyUnitsWindow': round(units_window / max(window_days / 30.4167, 1), 1) if units_window else 0,
            'dailyRunRate730d': round(daily_run_rate_730, 3),
            'dailyRunRate365d': round(daily_run_rate_365, 3),
            'dailyRunRate90d': round(daily_run_rate_90, 3),
            'daysOfCover730d': days_of_cover_730,
            'daysOfCoverWindow': days_of_cover_730,
            'daysOfCover365d': days_of_cover_365,
            'daysOfCover90d': days_of_cover_90,
            'stockMonthsOnHand': stock_months_on_hand,
            'turnoverZone': turnover_zone,
            'reorderRisk': reorder_risk,
            'recommendedMinUnits': recommended_min_units,
            'recommendedOrderUnits': recommended_order_units,
            'trend90v365Pct': trend_pct,
            'seasonalityYoYPct': yoy_pct,
            'stockValueSelling': round(effective_stock * money(base_item.get('unitSellingPrice')), 2) if base_item.get('unitSellingPrice') else None,
            'stockValueAbraAvg': round(effective_stock * money(base_item.get('unitCostAbraAvg')), 2) if base_item.get('unitCostAbraAvg') else None,
            'tags': tags,
            'orderingRole': classify_ordering_role({**base_item, 'effectiveStock': effective_stock, 'units365d': units365d, 'daysOfCover90d': days_of_cover_90, 'turnoverZone': turnover_zone, 'reorderRisk': reorder_risk, 'recommendedOrderUnits': recommended_order_units}),
            'market': market_key,
        })
        source_channel = item.get('sourceChannel') or 'unknown'
        if market_key == 'cz' and source_channel == 'riga' and item.get('orderable', True):
            item['orderable'] = False
            item['excludeFromOrderingReason'] = 'SKU je vedené jako Riga-only a pro CZ objednávání se nesmí použít'
            item['orderingRole'] = 'excluded'
        elif market_key == 'sk' and source_channel == 'praha' and item.get('orderable', True):
            item['orderable'] = False
            item['excludeFromOrderingReason'] = 'SKU je vedené jako Praha-only a pro SK objednávání se nesmí použít'
            item['orderingRole'] = 'excluded'
        items.append(item)

    turnover = sorted([item for item in items if item['units365d'] > 0 and item['effectiveStock'] > 0], key=lambda item: item['units365d'], reverse=True)
    dead_stock = sorted([item for item in items if 'dead_stock' in item['tags']], key=lambda item: item['effectiveStock'], reverse=True)
    slow_movers = sorted([item for item in items if 'slow_mover' in item['tags'] and item['effectiveStock'] > 0], key=lambda item: (((item.get('daysOfCover90d') if item.get('daysOfCover90d') is not None else item.get('daysOfCover365d')) or 0), item['effectiveStock']), reverse=True)
    overstocked = sorted([item for item in items if 'overstocked' in item['tags'] and item['effectiveStock'] > 0], key=lambda item: (item['daysOfCover365d'] or 0), reverse=True)
    fast_low_cover = sorted([item for item in items if 'fast_mover_low_cover' in item['tags']], key=lambda item: (item['daysOfCover365d'] or 999, -item['units365d']))

    return {
        'generatedAt': generated_at,
        'market': market_key,
        'window': base_payload.get('window') or {},
        'summary': {
            'trackedItems': len(items),
            'turnoverItems': len(turnover),
            'deadStockItems': len(dead_stock),
            'slowMoverItems': len(slow_movers),
            'overstockedItems': len(overstocked),
            'fastLowCoverItems': len(fast_low_cover),
            'criticalReorderItems': len([item for item in items if item['reorderRisk'] == 'critical']),
            'reorderSoonItems': len([item for item in items if item['reorderRisk'] == 'soon']),
            'redTurnoverItems': len([item for item in items if item['turnoverZone'] == 'red']),
            'orangeTurnoverItems': len([item for item in items if item['turnoverZone'] == 'orange']),
            'greenTurnoverItems': len([item for item in items if item['turnoverZone'] == 'green']),
        },
        'items': items,
        'topTurnover': turnover[:50],
        'deadStock': dead_stock[:100],
        'slowMovers': slow_movers[:100],
        'overstocked': overstocked[:100],
        'fastLowCover': fast_low_cover[:100],
    }


def classify_ordering_role(item):
    if not item.get('orderable', True):
        return 'excluded'
    if item.get('reorderRisk') in {'critical', 'soon'} or item.get('strategicPriority') == 'risky':
        return 'top_sku'
    if item.get('turnoverZone') == 'green' and (item.get('units365d') or 0) > 0 and (item.get('recommendedOrderUnits') or 0) > 0:
        return 'fill_up'
    return 'standard'


def classify_abc_buckets(items, revenue_weight=0.5, units_weight=0.5):
    eligible = []
    total_revenue = 0.0
    total_units = 0.0

    for item in items or []:
        if item.get('orderable') is False:
            continue
        units_365d = max(0.0, float(item.get('units365d') or 0))
        unit_price = max(0.0, float(item.get('unitSellingPrice') or 0))
        revenue_365d = units_365d * unit_price
        total_revenue += revenue_365d
        total_units += units_365d
        if units_365d > 0 or revenue_365d > 0:
            eligible.append({
                'code': item.get('code'),
                'units365d': units_365d,
                'revenue365d': revenue_365d,
            })

    if not eligible:
        return {}

    scored = []
    for row in eligible:
        revenue_share = (row['revenue365d'] / total_revenue) if total_revenue > 0 else 0.0
        units_share = (row['units365d'] / total_units) if total_units > 0 else 0.0
        combined_score = (revenue_share * revenue_weight) + (units_share * units_weight)
        scored.append({
            **row,
            'revenueShare': revenue_share,
            'unitsShare': units_share,
            'combinedScore': combined_score,
        })

    scored.sort(
        key=lambda row: (
            -row['combinedScore'],
            -row['revenue365d'],
            -row['units365d'],
            row.get('code') or '',
        )
    )

    total_combined_score = sum(row['combinedScore'] for row in scored) or 1.0
    cumulative_share = 0.0
    classified = {}
    for rank, row in enumerate(scored, start=1):
        previous_share = cumulative_share
        score_share = row['combinedScore'] / total_combined_score if total_combined_score > 0 else 0.0
        cumulative_share += score_share
        if previous_share < 0.8:
            abc_class = 'A'
        elif previous_share < 0.95:
            abc_class = 'B'
        else:
            abc_class = 'C'
        classified[row['code']] = {
            'abcClass': abc_class,
            'abcRank': rank,
            'abcRevenue365d': round(row['revenue365d'], 2),
            'abcRevenueShare': round(row['revenueShare'], 6),
            'abcUnitsShare': round(row['unitsShare'], 6),
            'abcCombinedScore': round(row['combinedScore'], 6),
            'abcCumulativeShare': round(cumulative_share, 6),
        }

    return classified


def enrich_item_with_packaging(item, ordering_packaging_map=None):
    item = dict(item or {})
    packaging = ((ordering_packaging_map or {}).get('byCatalogCode') or {}).get(normalize_product_code(item.get('code')))
    options = sorted({int(option) for option in (packaging.get('orderPackOptions') if packaging else []) if int(option) > 0}) if packaging else []
    recommended_step = int(packaging.get('recommendedOrderStep') or 0) if packaging else 0
    if not recommended_step and options:
        recommended_step = options[-1]
    item.update({
        'supplierSkus': packaging.get('supplierSkus') if packaging else [],
        'packagingRaw': packaging.get('packagingRaw') if packaging else None,
        'orderPackOptions': options,
        'recommendedOrderStep': recommended_step or None,
        'packagingMatchStatus': packaging.get('matchStatus') if packaging else 'missing',
    })
    item['orderingRole'] = classify_ordering_role(item)
    return item


def round_to_multiple(units, step):
    step = max(1, int(step or 1))
    units = max(0, float(units or 0))
    if units <= 0:
        return 0
    return int(math.ceil(units / step) * step)


def packaging_step_kind(options, step):
    clean = sorted({int(option) for option in (options or []) if int(option) > 0})
    if not clean or step <= 1:
        return 'unit'
    if len(clean) == 1:
        return 'pack'
    if step == clean[-1]:
        return 'carton'
    if step == clean[0]:
        return 'unit'
    return 'pack'


def packaging_step_label(kind):
    labels = {
        'unit': 'kus',
        'pack': 'balení',
        'carton': 'karton',
    }
    return labels.get(kind, 'bez balení')


def choose_packaging_step(item, raw_units, scenario_type='balanced'):
    options = sorted({int(option) for option in (item.get('orderPackOptions') or []) if int(option) > 0})
    if not options:
        return 1

    preferred_step = int(item.get('recommendedOrderStep') or 0)
    non_unit_options = [option for option in options if option > 1]
    if preferred_step <= 1 and non_unit_options:
        preferred_step = max(non_unit_options)

    raw_units = max(0, float(raw_units or 0))
    role = item.get('orderingRole') or 'standard'
    if raw_units <= 0:
        if preferred_step > 1:
            return preferred_step
        return options[0]

    # If the supplier map explicitly says "order by this pack", prefer that unit
    # over piece-by-piece ordering even when it overshoots the model forecast.
    if preferred_step > 1:
        return preferred_step

    if role == 'fill_up' or scenario_type == 'capacity':
        for step in sorted(options, reverse=True):
            rounded = round_to_multiple(raw_units, step)
            overshoot = (rounded - raw_units) / max(raw_units, 1)
            if raw_units >= step * 0.6 or overshoot <= 0.35:
                return step
        return options[-1]

    for step in sorted(options, reverse=True):
        rounded = round_to_multiple(raw_units, step)
        overshoot = (rounded - raw_units) / max(raw_units, 1)
        if overshoot <= 0.25:
            return step
    return options[0]


def round_to_allowed_pack_sizes(item, raw_units, scenario_type='balanced'):
    raw_units = max(0, int(round(raw_units or 0)))
    step = choose_packaging_step(item, raw_units, scenario_type=scenario_type)
    rounded_units = round_to_multiple(raw_units, step)
    step_kind = packaging_step_kind(item.get('orderPackOptions') or [], step)
    options = item.get('orderPackOptions') or []
    if not options:
        rounding_mode = 'bez omezení balení'
    elif rounded_units == raw_units:
        rounding_mode = f'přesně na {packaging_step_label(step_kind)}'
    else:
        rounding_mode = f'zaokrouhleno nahoru na {packaging_step_label(step_kind)}'
    return {
        'rawUnits': raw_units,
        'orderStepUnits': step,
        'orderStepKind': step_kind,
        'orderStepLabel': packaging_step_label(step_kind),
        'roundedUnits': rounded_units,
        'roundingMode': rounding_mode,
    }


ORDERING_CAPACITY_PROFILES = {
    'small': {'label': 'menší doplnění', 'targetUnits': 1200, 'fillerShare': 0.15},
    'half': {'label': 'půl kamionu', 'targetUnits': 2800, 'fillerShare': 0.35},
    'full': {'label': 'celý kamion', 'targetUnits': 5200, 'fillerShare': 0.65},
}


def clamp_number(value, minimum, maximum):
    return min(max(value, minimum), maximum)


def estimate_ordering_daily_demand(item):
    rate30 = float(item.get('units30d') or 0) / 30 if float(item.get('units30d') or 0) > 0 else 0.0
    rate90 = float(item.get('dailyRunRate90d') or 0)
    rate365 = float(item.get('dailyRunRate365d') or 0)
    rate730 = float(item.get('dailyRunRate730d') or 0)

    weighted = (
        rate30 * 0.65
        + rate90 * 0.25
        + rate365 * 0.07
        + rate730 * 0.03
    )

    trend_factor = clamp_number(1 + float(item.get('trend90v365Pct') or 0) / 600, 0.85, 1.15)
    estimate = weighted * trend_factor

    recent_cap = max(
        rate30 * 1.25 if rate30 > 0 else 0.0,
        rate90 * 1.10 if rate90 > 0 else 0.0,
        rate365 * 0.35 if rate365 > 0 else 0.0,
    )
    if recent_cap > 0:
        estimate = min(estimate, recent_cap)

    recent_floor = max(
        rate30 * 0.85 if rate30 > 0 else 0.0,
        rate90 * 0.75 if rate90 > 0 else 0.0,
    )
    if recent_floor > 0:
        estimate = max(estimate, recent_floor)

    return max(0, estimate)


def forecast_ordering_units(item, days):
    return max(0, round(estimate_ordering_daily_demand(item) * max(0, days)))


def safety_units_for_ordering(item, lead_days):
    base = estimate_ordering_daily_demand(item)
    return max(1, round(base * max(14, lead_days * 0.5)))


def blend_recommendation_target(dynamic_value, baseline_value, dynamic_weight=0.8):
    safe_dynamic = max(0, float(dynamic_value or 0))
    safe_baseline = max(0, float(baseline_value or 0))
    if safe_dynamic <= 0:
        return int(round(safe_baseline))
    safe_weight = max(0.0, min(1.0, float(dynamic_weight or 0)))
    return max(0, round((safe_dynamic * safe_weight) + (safe_baseline * (1 - safe_weight))))


def dynamic_recommendation_targets(item, lead_days):
    stock_units = max(0.0, float(item.get('effectiveStock') or 0))
    dynamic_min_days = max(21, ORDERING_TARGET_COVER_DAYS - 9)
    dynamic_ideal_days = ORDERING_TARGET_COVER_DAYS
    dynamic_max_days = max(45, ORDERING_TARGET_COVER_DAYS + 15)
    dynamic_min = max(0, forecast_ordering_units(item, dynamic_min_days) - stock_units)
    dynamic_ideal = max(dynamic_min, forecast_ordering_units(item, dynamic_ideal_days) - stock_units)
    dynamic_max = max(
        dynamic_ideal,
        forecast_ordering_units(item, dynamic_max_days) - stock_units + round(safety_units_for_ordering(item, lead_days) * 0.2),
    )
    baseline_min = max(0, int(item.get('recommendedMinUnits') or 0))
    baseline_ideal = max(0, int(item.get('recommendedOrderUnits') or 0))
    return {
        'minimumUnits': blend_recommendation_target(dynamic_min, baseline_min, 0.85),
        'optimumUnits': blend_recommendation_target(dynamic_ideal, baseline_ideal, 0.8),
        'capacityUnits': blend_recommendation_target(dynamic_max, max(baseline_ideal, baseline_min), 0.9),
    }


def max_units_for_ordering(item, lead_days):
    ninety = forecast_ordering_units(item, 90)
    recommended = int(item.get('recommendedOrderUnits') or 0)
    computed = max(
        recommended,
        round(recommended * 1.35),
        round(ninety + safety_units_for_ordering(item, lead_days) * 0.7),
    )
    return max(recommended, computed)


def ordering_market_label(item):
    market = str(item.get('market') or 'complete').lower()
    if market == 'cz':
        return 'CZ'
    if market == 'sk':
        return 'SK'
    return 'CZ + SK'


def format_cover_days_for_reason(value):
    if value is None:
        return 'bez prodeje'
    rounded = round(float(value), 1)
    if abs(rounded - round(rounded)) < 0.05:
        return f'{int(round(rounded))} dní'
    return f"{str(rounded).replace('.', ',')} dní"


def recommendation_action_reasons(item, lead_days):
    reasons = []
    market_label = ordering_market_label(item)
    cover_label = format_cover_days_for_reason(item.get('daysOfCover90d'))
    reorder_risk = item.get('reorderRisk')
    role = item.get('orderingRole') or classify_ordering_role(item)
    trend_pct = float(item.get('trend90v365Pct') or 0)

    if reorder_risk == 'critical':
        reasons.append(f'{market_label} je na kritickém pokrytí {cover_label}.')
    elif reorder_risk == 'soon':
        reasons.append(f'{market_label} je na hraničním pokrytí {cover_label}.')
    elif reorder_risk == 'watch':
        reasons.append(f'{market_label} je potřeba hlídat, pokrytí je {cover_label}.')

    if role == 'top_sku':
        reasons.append('Patří mezi hlavní refill položky a má jít před fillery.')
    elif role == 'fill_up':
        reasons.append('Je to filler, takže patří až za hlavní refill shortlist.')

    if trend_pct >= 25:
        reasons.append(f'Prodej zrychluje o {round(trend_pct, 1):g} % proti ročnímu tempu.')
    elif trend_pct <= -25:
        reasons.append(f'Prodej slábne o {round(abs(trend_pct), 1):g} % proti ročnímu tempu.')

    if item.get('strategicPriority') == 'risky':
        reasons.append('Referenční vrstva ho vede jako rizikové SKU.')

    if item.get('sourceChannel') == 'praha':
        reasons.append('Je navázané jen na Prahu, ne na Riga flow.')
    elif item.get('sourceChannel') == 'riga':
        reasons.append('Je navázané přímo na Riga flow.')
    elif item.get('sourceChannel') == 'unknown':
        reasons.append('Zdrojový kanál není referenčně rozlišený.')

    if item.get('packagingMatchStatus') == 'missing':
        reasons.append('Chybí jisté mapování balení pro objednávku.')

    return reasons[:4]


def recommendation_data_status(item):
    bits = []
    packaging_status = item.get('packagingMatchStatus')
    if packaging_status == 'exact_code':
        bits.append('balení spárované přesně')
    elif packaging_status == 'base_code':
        bits.append('balení spárované přes prefix')
    elif packaging_status == 'missing':
        bits.append('balení chybí')

    source_channel = item.get('sourceChannel') or 'unknown'
    if source_channel == 'praha':
        bits.append('Praha-only zdroj')
    elif source_channel == 'riga':
        bits.append('Riga-only zdroj')
    elif source_channel == 'unknown':
        bits.append('zdroj bez referenčního kanálu')

    if item.get('referenceSource') and item.get('referenceSource') != 'default':
        bits.append(f"reference {item.get('referenceSource')}")

    return ' · '.join(bits[:3]) if bits else 'sklad, forecast a balení bez výjimky'


def recommendation_source_meta(item, lead_days, use_praha):
    source_channel = item.get('sourceChannel') or 'unknown'
    if source_channel == 'praha':
        return {
            'source': 'Praha',
            'reason': 'referenčně vedené jako Praha-only SKU',
        }
    if source_channel == 'riga':
        return {
            'source': 'Riga',
            'reason': 'referenčně vedené jako Riga-only SKU',
        }

    cover_90d = item.get('daysOfCover90d')
    if use_praha and ((cover_90d if cover_90d is not None else 999) <= max(7, round(lead_days * 0.35)) or float(item.get('effectiveStock') or 0) <= 0):
        return {
            'source': 'Praha fallback',
            'reason': 'kritické pokrytí před doručením' if item.get('reorderRisk') == 'critical' else 'nízké pokrytí před doručením',
        }
    if item.get('orderingRole') == 'top_sku':
        return {
            'source': 'Riga',
            'reason': 'hlavní refill kvůli pokrytí 90d' if item.get('reorderRisk') == 'critical' else 'hlavní refill kvůli průběžnému pokrytí',
        }
    if item.get('turnoverZone') == 'green':
        return {'source': 'Riga', 'reason': 'filler se zdravou obrátkou, až po hlavním refill'}
    if float(item.get('trend90v365Pct') or 0) >= 25:
        return {'source': 'Riga', 'reason': 'zrychlující prodej proti ročnímu průměru'}
    return {
        'source': 'Riga',
        'reason': 'doplnit kvůli pokrytí 90d' if item.get('reorderRisk') == 'critical' else 'doplnit kvůli průběžnému pokrytí',
    }


def recommendation_priority_score(item):
    score = (
        (5000 if item.get('reorderRisk') == 'critical' else 3000 if item.get('reorderRisk') == 'soon' else 1500 if item.get('reorderRisk') == 'watch' else 400)
        + max(0, 120 - int(item.get('daysOfCover90d') if item.get('daysOfCover90d') is not None else 120)) * 10
        + float(item.get('trend90v365Pct') or 0)
        + min(float(item.get('units90d') or 0), 1000)
    )

    role = item.get('orderingRole') or classify_ordering_role(item)
    if role == 'top_sku':
        score += 900
    elif role == 'fill_up':
        score -= 250

    strategic_priority = item.get('strategicPriority') or 'standard'
    if strategic_priority == 'risky':
        score += 350
    elif strategic_priority == 'supplement':
        score -= 80

    packaging_status = item.get('packagingMatchStatus')
    if packaging_status == 'exact_code':
        score += 60
    elif packaging_status == 'base_code':
        score += 20
    elif packaging_status == 'missing':
        score -= 60

    source_channel = item.get('sourceChannel') or 'unknown'
    if source_channel in {'praha', 'riga'}:
        score += 40
    elif source_channel == 'unknown':
        score -= 40

    return round(score, 1)


def build_ordering_recommendation_rows(items, lead_days=21, use_praha=True):
    rows = []
    for item in items or []:
        if not item.get('orderable', True):
            continue
        if not ((item.get('recommendedOrderUnits') or 0) > 0 or (item.get('recommendedMinUnits') or 0) > 0):
            continue
        if not (
            item.get('reorderRisk') in {'critical', 'soon', 'watch'}
            or (item.get('turnoverZone') == 'green' and (item.get('daysOfCover90d') or 999) <= 120 and (item.get('units365d') or 0) > 0)
        ):
            continue

        source_meta = recommendation_source_meta(item, lead_days, use_praha)
        dynamic_targets = dynamic_recommendation_targets(item, lead_days)
        optimum = max(int(dynamic_targets.get('optimumUnits') or 0), int(dynamic_targets.get('minimumUnits') or 0))
        rounded_min = round_to_allowed_pack_sizes(item, int(dynamic_targets.get('minimumUnits') or 0), scenario_type='minimum')
        rounded_optimum = round_to_allowed_pack_sizes(item, optimum, scenario_type='balanced')
        rounded_max = round_to_allowed_pack_sizes(item, max(int(dynamic_targets.get('capacityUnits') or 0), max_units_for_ordering(item, lead_days)), scenario_type='capacity')
        row = dict(item)
        row.update({
            'forecast30': forecast_ordering_units(item, 30),
            'forecast60': forecast_ordering_units(item, 60),
            'forecast90': forecast_ordering_units(item, 90),
            'safetyStockUnits': safety_units_for_ordering(item, lead_days),
            'optimumUnits': optimum,
            'maxUnits': rounded_max['rawUnits'],
            'roundedMinUnits': rounded_min['roundedUnits'],
            'roundedOptimumUnits': rounded_optimum['roundedUnits'],
            'roundedMaxUnits': rounded_max['roundedUnits'],
            'idealUnits': rounded_optimum['rawUnits'],
            'finalOrderUnits': rounded_optimum['roundedUnits'],
            'orderStepUnits': rounded_optimum['orderStepUnits'],
            'orderStepKind': rounded_optimum['orderStepKind'],
            'orderStepLabel': rounded_optimum['orderStepLabel'],
            'roundingMode': rounded_optimum['roundingMode'],
            'packagingOptionsLabel': ' / '.join(str(option) for option in (item.get('orderPackOptions') or [])) if item.get('orderPackOptions') else '1',
            'source': source_meta['source'],
            'reason': source_meta['reason'],
            'actionReasons': recommendation_action_reasons(item, lead_days),
            'dataStatus': recommendation_data_status(item),
            'marketLabel': ordering_market_label(item),
        })
        row['priorityScore'] = recommendation_priority_score(row)
        rows.append(row)

    rows.sort(key=lambda item: (-item.get('priorityScore', 0), -item.get('optimumUnits', 0), item.get('code') or ''))
    return rows


def build_ordering_scenario_rows(scenario_type, rows, profile):
    target_left = int(profile.get('targetUnits') or 0)
    selected = []
    for item in rows or []:
        units = 0
        optimum = int(item.get('optimumUnits') or 0)
        recommended_min = int(item.get('recommendedMinUnits') or 0)
        max_units = int(item.get('roundedMaxUnits') or item.get('maxUnits') or optimum)
        reorder_risk = item.get('reorderRisk')

        if scenario_type == 'conservative':
            if reorder_risk == 'critical':
                units = max(recommended_min, round(optimum * 0.7))
            elif reorder_risk == 'soon':
                units = max(0, round(recommended_min * 0.8))
        elif scenario_type == 'balanced':
            if reorder_risk == 'critical':
                units = optimum
            elif reorder_risk == 'soon':
                units = max(recommended_min, round(optimum * 0.9))
            elif reorder_risk == 'watch':
                units = round(optimum * 0.55)
            elif item.get('turnoverZone') == 'green':
                units = round(optimum * float(profile.get('fillerShare') or 0))
        elif scenario_type == 'capacity':
            if reorder_risk == 'critical':
                units = min(max_units, round(optimum * 1.15))
            elif reorder_risk == 'soon':
                units = min(max_units, round(optimum * 1.05))
            elif reorder_risk == 'watch':
                units = round(optimum * 0.85)
            elif item.get('turnoverZone') == 'green':
                units = round(optimum * (0.55 + float(profile.get('fillerShare') or 0)))

        rounded_plan = round_to_allowed_pack_sizes(item, units, scenario_type=scenario_type)
        units = max(0, min(int(rounded_plan['roundedUnits'] or 0), max_units or int(rounded_plan['roundedUnits'] or 0)))
        if not units:
            continue
        if scenario_type != 'capacity' and target_left <= 0 and reorder_risk != 'critical':
            continue

        selected_row = dict(item)
        selected_row['plannedUnits'] = units
        selected_row['plannedRawUnits'] = int(rounded_plan['rawUnits'] or 0)
        selected_row['plannedOrderStepUnits'] = int(rounded_plan['orderStepUnits'] or 1)
        selected_row['plannedOrderStepKind'] = rounded_plan['orderStepKind']
        selected_row['plannedOrderStepLabel'] = rounded_plan['orderStepLabel']
        selected_row['plannedRoundingMode'] = rounded_plan['roundingMode']
        selected.append(selected_row)
        target_left -= units
    return selected


def ordering_future_red_risk_count(rows, months):
    days = months * 30
    count = 0
    for item in rows or []:
        future_stock = float(item.get('effectiveStock') or 0) + float(item.get('plannedUnits') or 0) - forecast_ordering_units(item, days)
        future_daily = estimate_ordering_daily_demand(item)
        if future_stock <= 0 or future_daily <= 0:
            continue
        future_cover = future_stock / future_daily
        if future_cover >= 365:
            count += 1
    return count


def build_ordering_planning(items, lead_days=21, capacity_key='half', use_praha=True):
    profile = ORDERING_CAPACITY_PROFILES.get(capacity_key) or ORDERING_CAPACITY_PROFILES['half']
    rows = build_ordering_recommendation_rows(items, lead_days=lead_days, use_praha=use_praha)
    scenarios = {
        'A': {
            'key': 'A',
            'title': 'Varianta A, konzervativní',
            'subtitle': 'Jen nejnutnější doplnění, minimální riziko přebytku.',
            'rows': build_ordering_scenario_rows('conservative', rows, profile),
        },
        'B': {
            'key': 'B',
            'title': 'Varianta B, vyvážená',
            'subtitle': 'Standardní doporučení a rozumný kompromis cash / zásoba.',
            'rows': build_ordering_scenario_rows('balanced', rows, profile),
        },
        'C': {
            'key': 'C',
            'title': 'Varianta C, kapacitní',
            'subtitle': 'Víc doplnění i fillerů se zelenou obrátkovostí.',
            'rows': build_ordering_scenario_rows('capacity', rows, profile),
        },
    }

    scenario_cards = []
    for scenario in scenarios.values():
        scenario_rows = scenario['rows']
        units = sum(int(item.get('plannedUnits') or 0) for item in scenario_rows)
        value = round(sum(float(item.get('unitSellingPrice') or 0) * float(item.get('plannedUnits') or 0) for item in scenario_rows), 2)
        critical_left = 0
        praha_risk = 0
        for item in scenario_rows:
            daily_demand = max(estimate_ordering_daily_demand(item), 0.01)
            future_cover = (float(item.get('effectiveStock') or 0) + float(item.get('plannedUnits') or 0) - forecast_ordering_units(item, lead_days)) / daily_demand
            if item.get('reorderRisk') == 'critical' and future_cover < 30:
                critical_left += 1
            if item.get('source') == 'Praha fallback':
                praha_risk += 1
        usage = min(999, round((units / int(profile.get('targetUnits') or 1)) * 100)) if profile.get('targetUnits') else 0
        scenario_cards.append({
            'key': scenario['key'],
            'title': scenario['title'],
            'subtitle': scenario['subtitle'],
            'plannedUnits': units,
            'proxyValue': value,
            'capacityUsagePct': usage,
            'criticalLeft': critical_left,
            'prahaFallbackRisk': praha_risk,
            'redRiskAfterMonths': {
                'm1': ordering_future_red_risk_count(scenario_rows, 1),
                'm2': ordering_future_red_risk_count(scenario_rows, 2),
                'm3': ordering_future_red_risk_count(scenario_rows, 3),
                'm4': ordering_future_red_risk_count(scenario_rows, 4),
            },
        })

    return {
        'defaults': {
            'leadDays': lead_days,
            'capacity': capacity_key,
            'prahaFallback': use_praha,
        },
        'capacityProfiles': ORDERING_CAPACITY_PROFILES,
        'summary': f'Simulace: lead time {lead_days} dní, kapacita {profile.get("label")}, Praha fallback {"zapnutý" if use_praha else "vypnutý"}. Varianty jsou spočítané v refreshi nad forecastem a selling-price proxy.',
        'recommendationSummary': f'Zobrazeno {len(rows[:120])} doporučených položek. Forecast je dopočítaný z 90d tempa, 365d tempa a trendu proti ročnímu průměru.',
        'recommendationRows': rows[:120],
        'scenarios': scenario_cards,
    }


def build_ordering_core(ctx: OrderingCoreBuildContext):
    analytics_payload = ctx.analytics_payload
    generated_at = ctx.generated_at
    items = analytics_payload.get('items') or []
    orderable_items = [item for item in items if item.get('orderable', True)]
    top_sku_items = [item for item in orderable_items if item.get('orderingRole') == 'top_sku']
    fill_up_items = [item for item in orderable_items if item.get('orderingRole') == 'fill_up']
    critical_reorder = sorted(
        [item for item in top_sku_items if item.get('reorderRisk') == 'critical'],
        key=lambda item: ((item.get('daysOfCover90d') if item.get('daysOfCover90d') is not None else 999), -item.get('recommendedOrderUnits', 0), -item.get('units90d', 0)),
    )
    reorder_watch = sorted(
        [item for item in top_sku_items if item.get('reorderRisk') in {'critical', 'soon', 'watch'}],
        key=lambda item: (0 if item.get('reorderRisk') == 'critical' else 1 if item.get('reorderRisk') == 'soon' else 2, (item.get('daysOfCover90d') if item.get('daysOfCover90d') is not None else 999), -item.get('units90d', 0)),
    )
    overstock_risks = sorted(
        [item for item in orderable_items if item.get('turnoverZone') == 'red' and item.get('effectiveStock', 0) > 0],
        key=lambda item: (-(item.get('stockValueAbraAvg') or item.get('stockValueSelling') or 0), -(item.get('daysOfCover365d') or 0), -(item.get('effectiveStock') or 0)),
    )
    trend_watch = sorted(
        [item for item in orderable_items if item.get('trend90v365Pct') is not None and item.get('units90d', 0) > 0],
        key=lambda item: abs(item.get('trend90v365Pct') or 0),
        reverse=True,
    )
    suggested_fillers = sorted(
        [item for item in fill_up_items if item.get('turnoverZone') == 'green' and (item.get('daysOfCover90d') or 999) <= 90 and item.get('recommendedOrderUnits', 0) > 0],
        key=lambda item: (-item.get('units365d', 0), item.get('daysOfCover90d') or 999),
    )

    cash_in_red = round(sum((item.get('stockValueAbraAvg') or item.get('stockValueSelling') or 0) for item in overstock_risks[:100]), 2)
    alerts = []
    if critical_reorder:
        alerts.append(f'{len(critical_reorder)} SKU mají kritické pokrytí podle 90denní rychlosti prodeje.')
    if overstock_risks:
        alerts.append(f'{len(overstock_risks)} SKU jsou v červené obrátkovosti a vážou zásobu přibližně za {format_czk(cash_in_red)}.')
    accelerating = [item for item in trend_watch if (item.get('trend90v365Pct') or 0) >= 25]
    slowing = [item for item in trend_watch if (item.get('trend90v365Pct') or 0) <= -25]
    if accelerating:
        alerts.append(f'{len(accelerating)} SKU zrychlují o 25 % a víc proti ročnímu tempu.')
    if slowing:
        alerts.append(f'{len(slowing)} SKU zpomalují o 25 % a víc proti ročnímu tempu.')

    return {
        'generatedAt': generated_at,
        'window': analytics_payload.get('window') or {},
        'planning': build_ordering_planning(orderable_items, lead_days=21, capacity_key='half', use_praha=True),
        'summary': {
            'trackedItems': len(items),
            'orderableItems': len(orderable_items),
            'excludedItems': len(items) - len(orderable_items),
            'topSkuItems': len(top_sku_items),
            'fillUpItems': len(fill_up_items),
            'criticalReorderItems': len(critical_reorder),
            'watchReorderItems': len(reorder_watch),
            'redTurnoverItems': len([item for item in orderable_items if item.get('turnoverZone') == 'red']),
            'orangeTurnoverItems': len([item for item in orderable_items if item.get('turnoverZone') == 'orange']),
            'greenTurnoverItems': len([item for item in orderable_items if item.get('turnoverZone') == 'green']),
            'cashInRedTurnover': cash_in_red,
        },
        'alerts': alerts[:6],
        'criticalReorder': critical_reorder[:50],
        'reorderWatch': reorder_watch[:100],
        'overstockRisks': overstock_risks[:100],
        'trendWatch': trend_watch[:100],
        'suggestedFillers': suggested_fillers[:50],
    }


def build_ordering_reference_data(analytics_payload, generated_at):
    items = []
    for item in (analytics_payload.get('items') or []):
        items.append({
            'code': item.get('code'),
            'title': item.get('title'),
            'supplierSkus': item.get('supplierSkus') or [],
            'itemType': item.get('itemType') or 'product',
            'orderable': bool(item.get('orderable', True)),
            'sourceChannel': item.get('sourceChannel') or 'unknown',
            'strategicPriority': item.get('strategicPriority') or 'standard',
            'orderingRole': item.get('orderingRole') or classify_ordering_role(item),
            'giftCandidate': bool(item.get('giftCandidate')),
            'excludeFromOrderingReason': item.get('excludeFromOrderingReason'),
            'referenceSource': item.get('referenceSource') or 'default',
            'referenceFlags': item.get('referenceFlags') or [],
            'packagingMatchStatus': item.get('packagingMatchStatus') or 'missing',
            'packagingRaw': item.get('packagingRaw'),
            'orderPackOptions': item.get('orderPackOptions') or [],
            'recommendedOrderStep': item.get('recommendedOrderStep'),
            'recommendedOrderUnits': item.get('recommendedOrderUnits') or 0,
            'recommendedMinUnits': item.get('recommendedMinUnits') or 0,
            'effectiveStock': item.get('effectiveStock') or 0,
            'units365d': item.get('units365d') or 0,
            'daysOfCover90d': item.get('daysOfCover90d'),
        })

    items.sort(key=lambda row: (row['orderable'], row['itemType'], -(row.get('recommendedOrderUnits') or 0), row.get('code') or ''))

    excluded = [row for row in items if not row['orderable']]
    by_type = Counter(row['itemType'] or 'unknown' for row in items)
    by_source = Counter(row['sourceChannel'] or 'unknown' for row in items)
    by_role = Counter(row['orderingRole'] or 'unknown' for row in items)
    by_packaging = Counter(row['packagingMatchStatus'] or 'missing' for row in items)

    return {
        'generatedAt': generated_at,
        'summary': {
            'trackedItems': len(items),
            'orderableItems': len([row for row in items if row['orderable']]),
            'excludedItems': len(excluded),
            'byItemType': dict(sorted(by_type.items())),
            'bySourceChannel': dict(sorted(by_source.items())),
            'byOrderingRole': dict(sorted(by_role.items())),
            'byPackagingMatchStatus': dict(sorted(by_packaging.items())),
        },
        'excludedTop': excluded[:150],
        'items': items,
    }


def build_expiry_overview(
    generated_at,
    combined_index,
    cz_expiry_rows,
    sk_expiry_rows,
    *,
    cz_inventory_items=None,
    sk_inventory_items=None,
    sales_orders=None,
    end_dt=None,
    pos_admin_views=None,
):
    title_by_code = {}
    for row in combined_index.get('items') or []:
        title_by_code[row['code']] = row['title']
        for source_code in row.get('fourpx', {}).get('sourceCodes') or []:
            title_by_code.setdefault(source_code, row['title'])

    exact_inventory_by_market = {
        'CZ': aggregate_exact_inventory(cz_inventory_items or []),
        'SK': aggregate_exact_inventory(sk_inventory_items or []),
    }
    exact_sales_metrics = collect_exact_order_metrics(
        sales_orders or [],
        end_dt=end_dt or current_local_time(),
        pos_admin_views=pos_admin_views,
        windows=(90, 30, 14),
    )

    combined_rows = []
    for row in (cz_expiry_rows or []) + (sk_expiry_rows or []):
        enriched = dict(row)
        enriched['title'] = title_by_code.get(row['sku']) or row['sku']
        enriched['label'] = f"{row['sku']} · {enriched['title']} ({row['account']})"
        exact_inventory = (exact_inventory_by_market.get(row['account']) or {}).get(row['sku']) or {}
        exact_sales = exact_sales_metrics.get(row['sku']) or {}
        view_sales = ((exact_sales.get('byView') or {}).get(str(row['account']).lower()) or {})
        units90d = round(view_sales.get('units90d', 0.0), 2)
        units30d = round(view_sales.get('units30d', 0.0), 2)
        units14d = round(view_sales.get('units14d', 0.0), 2)
        exact_stock_total = round(num(exact_inventory.get('availableStock')), 2)
        expiry_cover_stock = round(
            num(row.get('stockAtNearestExpiry')) or num(row.get('datedStock')),
            2,
        )
        daily_run_rate_90 = units90d / 90 if units90d else 0.0
        daily_run_rate_30 = units30d / 30 if units30d else 0.0
        enriched['exactAnalytics'] = {
            'code': row['sku'],
            'availableStock': exact_stock_total,
            'coverStock': expiry_cover_stock,
            'units90d': units90d,
            'units30d': units30d,
            'units14d': units14d,
            'daysOfCover30d': round(expiry_cover_stock / daily_run_rate_30, 1) if daily_run_rate_30 > 0 else None,
            'daysOfCover90d': round(expiry_cover_stock / daily_run_rate_90, 1) if daily_run_rate_90 > 0 else None,
            'daysOfCover30dAvailable': round(exact_stock_total / daily_run_rate_30, 1) if daily_run_rate_30 > 0 else None,
            'daysOfCover90dAvailable': round(exact_stock_total / daily_run_rate_90, 1) if daily_run_rate_90 > 0 else None,
            'lastSaleDate': exact_sales.get('lastSaleDate'),
        }
        combined_rows.append(enriched)

    combined_rows.sort(key=lambda item: (-item['riskScore'], item['daysToExpiry'], -item['datedStock'], item['sku'], item['dateExpiry']))
    return {
        'generatedAt': generated_at,
        'summary': {
            'datedSkuCount': len({row['sku'] for row in combined_rows}),
            'czSkuCount': len({row['sku'] for row in (cz_expiry_rows or [])}),
            'skSkuCount': len({row['sku'] for row in (sk_expiry_rows or [])}),
            'datedRowCount': len(combined_rows),
            'czRowCount': len(cz_expiry_rows or []),
            'skRowCount': len(sk_expiry_rows or []),
        },
        'topExpiring': combined_rows,
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


def build_inventory_health_summary(analytics_payload, ordering_core_payload):
    items = [row for row in (analytics_payload or {}).get('items') or [] if row.get('orderable') is not False]
    a_rows = [row for row in items if row.get('abcClass') == 'A']
    if not a_rows:
        a_rows = [row for row in items if row.get('orderingRole') == 'top_sku']
    a_base_count = len(a_rows)

    def cover_days(row):
        for key in ('daysOfCover90d', 'daysOfCover365d', 'daysOfCover730d'):
            value = row.get(key)
            if value is not None:
                return float(value)
        return None

    def positive_stock_value(row):
        return max(0.0, float(row.get('stockValueAbraAvg') or row.get('stockValueSelling') or 0))

    def is_dead_stock(row):
        cover = cover_days(row)
        return float(row.get('effectiveStock') or 0) > 0 and cover is not None and cover > 180

    def is_slow_stock(row):
        cover = cover_days(row)
        return float(row.get('effectiveStock') or 0) > 0 and cover is not None and 90 < cover <= 180

    a_critical_rows = [
        row for row in a_rows
        if float(row.get('effectiveStock') or 0) <= 0
        or row.get('reorderRisk') == 'critical'
        or ((cover_days(row) is not None) and cover_days(row) < 7)
    ]
    a_warning_rows = [
        row for row in a_rows
        if row not in a_critical_rows and (
            row.get('reorderRisk') in {'soon', 'watch'}
            or ((cover_days(row) is not None) and cover_days(row) < 14)
        )
    ]
    dead_rows = [row for row in items if is_dead_stock(row)]
    slow_rows = [row for row in items if is_slow_stock(row)]
    overstock_rows = [
        row for row in items
        if float(row.get('effectiveStock') or 0) > 0 and (
            'overstocked' in (row.get('tags') or [])
            or row.get('turnoverZone') == 'red'
            or ((cover_days(row) or 0) > 90)
        )
    ]

    total_stock_value = sum(positive_stock_value(row) for row in items)
    dead_value = sum(positive_stock_value(row) for row in dead_rows)
    slow_value = sum(positive_stock_value(row) for row in slow_rows)
    slow_dead_value = dead_value + slow_value
    overstock_value = sum(positive_stock_value(row) for row in overstock_rows)
    daily_sales_value_90 = sum(
        (max(0.0, float(row.get('units90d') or 0)) * max(0.0, float(row.get('unitSellingPrice') or 0))) / 90.0
        for row in items
    )
    total_cover_days = round(total_stock_value / daily_sales_value_90, 1) if daily_sales_value_90 > 0 else None
    slow_dead_share = round((slow_dead_value / total_stock_value) * 100, 1) if total_stock_value > 0 else 0.0
    dead_share = round((dead_value / total_stock_value) * 100, 1) if total_stock_value > 0 else 0.0
    overstock_share = round((overstock_value / total_stock_value) * 100, 1) if total_stock_value > 0 else 0.0

    a_critical_rows = sorted(
        a_critical_rows,
        key=lambda row: (
            0 if float(row.get('effectiveStock') or 0) <= 0 else 1,
            0 if row.get('reorderRisk') == 'critical' else 1,
            cover_days(row) if cover_days(row) is not None else 999999,
            row.get('abcRank') if row.get('abcRank') is not None else 999999,
            -float(row.get('abcRevenue365d') or 0),
        ),
    )
    a_warning_rows = sorted(
        a_warning_rows,
        key=lambda row: (
            0 if row.get('reorderRisk') == 'soon' else 1,
            cover_days(row) if cover_days(row) is not None else 999999,
            row.get('abcRank') if row.get('abcRank') is not None else 999999,
            -float(row.get('abcRevenue365d') or 0),
        ),
    )

    a_critical_share = round((len(a_critical_rows) / a_base_count) * 100, 1) if a_base_count else 0.0
    a_warning_share = round((len(a_warning_rows) / a_base_count) * 100, 1) if a_base_count else 0.0

    health_score = 100
    if a_base_count:
        # Calibrate score by share of the true ABC-A layer in risk, so larger assortments do not collapse to zero too easily.
        health_score -= round((len(a_critical_rows) / a_base_count) * 60)
        health_score -= round((len(a_warning_rows) / a_base_count) * 25)
    if dead_share > 15:
        health_score -= 20
    elif dead_share > 8:
        health_score -= 10
    if slow_dead_share > 35:
        health_score -= 15
    elif slow_dead_share > 20:
        health_score -= 8
    if total_cover_days is not None and total_cover_days > 120:
        health_score -= 15
    elif total_cover_days is not None and total_cover_days > 90:
        health_score -= 8
    if overstock_share > 20:
        health_score -= 10
    health_score = max(0, round(health_score))

    return {
        'healthScore': health_score,
        'aCriticalCount': len(a_critical_rows),
        'aCriticalShare': a_critical_share,
        'aWarningCount': len(a_warning_rows),
        'aWarningShare': a_warning_share,
        'aBaseCount': a_base_count,
        'topSkuCount': a_base_count,
        'slowDeadValue': round(slow_dead_value, 2),
        'slowDeadShare': slow_dead_share,
        'deadValue': round(dead_value, 2),
        'deadShare': dead_share,
        'overstockValue': round(overstock_value, 2),
        'overstockShare': overstock_share,
        'totalCoverDays': total_cover_days,
        'topRiskCodes': [row.get('code') for row in a_critical_rows[:3] if row.get('code')],
        'topRiskLabels': [row.get('label') or f'{row.get("code") or "SKU"} · {row.get("title") or "Bez názvu"}' for row in a_critical_rows[:3]],
        'blockedItems': int((ordering_core_payload or {}).get('summary', {}).get('excludedItems') or 0),
    }


def build_alerts(wpj_summary, stock_summary, logistics_summary, warnings, inventory_health=None):
    alerts = []
    if wpj_summary.get('problematicOrders'):
        alerts.append(f'{wpj_summary["problematicOrders"]} problematických nebo stornovaných objednávek.')
    if stock_summary.get('lowStockSoldYesterday'):
        alerts.append(f'{len(stock_summary["lowStockSoldYesterday"])} včera prodaných produktů je teď na nízkém skladu.')
    if stock_summary.get('negativeStoreStock'):
        alerts.append(f'{len(stock_summary["negativeStoreStock"])} skladových pozic je v mínusu.')
    if inventory_health:
        if inventory_health.get('aCriticalCount'):
            alerts.append(f'{inventory_health["aCriticalCount"]} A produktů je v kritickém riziku výpadku.')
        elif (inventory_health.get('slowDeadShare') or 0) > 20:
            alerts.append(f'Slow/dead stock váže {inventory_health["slowDeadShare"]:.1f} % zásoby.')
    if logistics_summary.get('coverageWarnings'):
        alerts.extend(logistics_summary['coverageWarnings'])
    if any((row.get('daysToExpiry') is not None and row.get('daysToExpiry') <= 30) for row in (logistics_summary.get('expiringProducts') or [])):
        alerts.append('V top expiracích je aspoň jedna položka do 30 dnů.')
    alerts.extend(warnings)
    deduped = []
    for alert in alerts:
        if alert not in deduped:
            deduped.append(alert)
    return deduped[:4]


def build_priorities(wpj_summary, stock_summary, logistics_summary, inventory_health=None):
    priorities = []
    if inventory_health and inventory_health.get('aCriticalCount'):
        codes = ', '.join(inventory_health.get('topRiskCodes') or []) or f'{inventory_health["aCriticalCount"]} SKU'
        priorities.append(f'Prověřit A produkty v riziku výpadku ({inventory_health["aCriticalCount"]} SKU): {codes}.')
    for row in stock_summary.get('lowStockSoldYesterday') or []:
        priorities.append(f'Dohlédnout {row["code"]} ({row["title"]}), aktuálně {format_units(row["stock"])}.')
        if len(priorities) >= 2:
            break
    if wpj_summary.get('problematicOrders'):
        priorities.append(f'Projít {wpj_summary["problematicOrders"]} problematických nebo stornovaných objednávek z včerejška.')
    if inventory_health and (inventory_health.get('slowDeadShare') or 0) > 20:
        priorities.append(f'Připravit řez slow/dead stock, aktuálně {inventory_health["slowDeadShare"]:.1f} % zásoby / {format_czk(inventory_health["slowDeadValue"])}.')
    if logistics_summary.get('coverageWarnings'):
        priorities.append('Rozšířit 4PX pull, aby ranní report neřezal starší včerejší zásilky.')
    if not logistics_summary.get('expiringProducts'):
        priorities.append('Dohledat spolehlivý zdroj expirací, 4PX inventory zatím vrací jen batch_no bez data spotřeby.')
    else:
        priorities.append('Projít nejbližší expirace v 4PX a rozhodnout o doprodeji nebo přesunu zásoby.')
    return priorities[:5]


def build_morning_report(ctx: MorningReportBuildContext):
    orders_delta = pct_delta(ctx.wpj_summary['orders'], ctx.baseline_orders) if ctx.baseline_orders is not None else None
    revenue_delta = pct_delta(ctx.wpj_summary['revenueWithVat'], ctx.baseline_revenue) if ctx.baseline_revenue is not None else None
    quick = {
        'orders': {
            'value': ctx.wpj_summary['orders'],
            'baseline': ctx.baseline_orders,
            'deltaPct': orders_delta,
        },
        'revenueWithVat': {
            'value': ctx.wpj_summary['revenueWithVat'],
            'baseline': ctx.baseline_revenue,
            'deltaPct': revenue_delta,
        },
        'shipmentsTotal': ctx.logistics_summary['shipmentsTotal'],
        'alerts': ctx.alerts,
    }

    report = {
        'generatedAt': current_local_time().isoformat(),
        'reportDate': ctx.report_date.isoformat(),
        'detailUrl': SETTINGS.morning_report_detail_url,
        'window': {
            'from': datetime(ctx.report_date.year, ctx.report_date.month, ctx.report_date.day, 0, 0, 1, tzinfo=PRAGUE_TZ).isoformat(),
            'to': datetime(ctx.report_date.year, ctx.report_date.month, ctx.report_date.day, 23, 59, 59, tzinfo=PRAGUE_TZ).isoformat(),
        },
        'warnings': ctx.warnings,
        'quickSummary': quick,
        'mtd': ctx.mtd_summary or {},
        'eshop': ctx.wpj_summary,
        'stock': ctx.stock_summary,
        'inventory': ctx.inventory_summary,
        'inventoryHealth': ctx.inventory_health or {},
        'logistics': ctx.logistics_summary,
        'priorities': ctx.priorities,
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


def format_pct_delta(value):
    if value is None:
        return 'bez srovnání'
    return f'{value:+.1f}'.replace('.', ',') + ' %'


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


def inventory_health_headline(health):
    if not health:
        return 'health metrika skladu zatím není k dispozici'
    score = int(health.get('healthScore') or 0)
    a_critical = int(health.get('aCriticalCount') or 0)
    a_base_count = int(health.get('aBaseCount') or health.get('topSkuCount') or 0)
    slow_dead_share = float(health.get('slowDeadShare') or 0)
    if a_base_count > 0:
        critical_text = f'{a_critical} z {a_base_count} SKU'
    else:
        critical_text = f'{a_critical} SKU'
    return f'score skladu {score}/100 · A riziko {critical_text} · slow/dead {str(round(slow_dead_share, 1)).replace(".", ",")} %'


def inventory_health_cash_line(health):
    if not health:
        return 'bez health detailu skladu'
    cover = health.get('totalCoverDays')
    cover_text = '–' if cover is None else f'{str(round(float(cover), 1)).replace(".", ",")} dní'
    return (
        f'vázaný cash slow/dead {format_czk(health.get("slowDeadValue", 0))} '
        f'({str(round(float(health.get("slowDeadShare", 0)), 1)).replace(".", ",")} %) '
        f'· dead stock {str(round(float(health.get("deadShare", 0)), 1)).replace(".", ",")} % '
        f'· cover {cover_text}'
    )


def abc_inventory_line():
    return 'ABC skladu: A = horní vrstva podle kombinace obratu a prodaných kusů, B = střed, C = pomalé nebo doplňkové položky.'


def format_morning_report_text(report):
    report_date = parse_dt(report['window']['from']).strftime('%-d. %-m. %Y')
    quick = report['quickSummary']
    eshop = report['eshop']
    stock = report['stock']
    inventory = report.get('inventory') or {}
    inventory_health = report.get('inventoryHealth') or {}
    logistics = report['logistics']
    mtd = report.get('mtd') or {}
    mtd_current = mtd.get('current') or {}
    mtd_previous = mtd.get('previousSamePeriod') or {}
    mtd_pre_previous = mtd.get('prePreviousSamePeriod') or {}
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
        f'• Obrat od začátku měsíce: {format_czk(mtd_current.get("revenueWithVat"))} ({format_pct_delta(mtd.get("changePct"))} vs {mtd_previous.get("label") or "minulý měsíc"}, {format_czk(mtd_previous.get("revenueWithVat"))}; {format_pct_delta(mtd.get("prePreviousChangePct"))} vs předminulý {mtd_pre_previous.get("label") or "stejné období"}, {format_czk(mtd_pre_previous.get("revenueWithVat"))})',
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
        f'• Zdraví skladu: {inventory_health_headline(inventory_health)}',
        f'• {abc_inventory_line()}',
        f'• Cash ve skladu: {inventory_health_cash_line(inventory_health)}',
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
    inventory_health = report.get('inventoryHealth') or {}
    logistics = report['logistics']
    mtd = report.get('mtd') or {}
    mtd_current = mtd.get('current') or {}
    mtd_previous = mtd.get('previousSamePeriod') or {}
    mtd_pre_previous = mtd.get('prePreviousSamePeriod') or {}
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
        f'• Obrat od začátku měsíce: {format_czk(mtd_current.get("revenueWithVat"))} ({format_pct_delta(mtd.get("changePct"))} vs {mtd_previous.get("label") or "minulý měsíc"}, {format_czk(mtd_previous.get("revenueWithVat"))}; {format_pct_delta(mtd.get("prePreviousChangePct"))} vs předminulý {mtd_pre_previous.get("label") or "stejné období"}, {format_czk(mtd_pre_previous.get("revenueWithVat"))})',
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

    lines.extend([
        '',
        '📦 Zdraví skladu',
        f'• {inventory_health_headline(inventory_health)}',
        f'• {abc_inventory_line()}',
        f'• {inventory_health_cash_line(inventory_health)}',
    ])

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


def abra_config():
    return SETTINGS.abra_config()


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
    return ABRA_ADAPTER.records(payload, evidence)


def abra_get(config, evidence, params=None, selector=None):
    return ABRA_ADAPTER.get(config, evidence, params=params, selector=selector)


def abra_download(config, path, params=None, accept=None):
    return ABRA_ADAPTER.download(config, path, params=params, accept=accept)


def fetch_abra_average_cost_map(product_codes, page_size=ABRA_STOCK_CARD_PAGE_SIZE, max_pages=ABRA_STOCK_CARD_MAX_PAGES):
    wanted_codes = {
        normalize_product_code(code)
        for code in (product_codes or [])
        if normalize_product_code(code)
    }
    if not wanted_codes:
        return {}

    config = abra_config()
    if not config.get('enabled'):
        return {}

    rows = []
    for page in range(max_pages):
        try:
            chunk = abra_records(abra_get(config, 'skladova-karta', {
                'detail': 'full',
                'limit': page_size,
                'start': page * page_size,
                'order': 'id@A',
            }), 'skladova-karta')
        except Exception:
            return {}
        rows.extend(chunk)
        if len(chunk) < page_size:
            break

    by_code = defaultdict(list)
    for row in rows:
        code = normalize_product_code(abra_text(row.get('cenik')).replace('code:', '', 1))
        if not code or code not in wanted_codes:
            continue
        period_label = abra_text(row.get('ucetObdobi@showAs') or row.get('ucetObdobi'))
        period_rank_match = re.search(r'(\d{4})', period_label)
        period_rank = int(period_rank_match.group(1)) if period_rank_match else 0
        quantity = max(0.0, abra_money(row.get('stavMJ') or row.get('dostupMj')))
        inventory_value = max(0.0, abra_money(row.get('stavTuz')))
        avg_cost = max(0.0, abra_money(row.get('prumCenaTuz')))
        by_code[code].append({
            'periodRank': period_rank,
            'quantity': quantity,
            'inventoryValue': inventory_value,
            'avgCost': avg_cost,
            'lastUpdate': abra_text(row.get('lastUpdate')),
        })

    result = {}
    for code, entries in by_code.items():
        if not entries:
            continue
        latest_period = max(entry.get('periodRank') or 0 for entry in entries)
        current_entries = [entry for entry in entries if (entry.get('periodRank') or 0) == latest_period] or list(entries)
        positive_entries = [entry for entry in current_entries if entry.get('quantity', 0) > 0 and (entry.get('inventoryValue', 0) > 0 or entry.get('avgCost', 0) > 0)]
        if positive_entries:
            total_quantity = sum(entry.get('quantity', 0) for entry in positive_entries)
            total_value = sum(
                entry.get('inventoryValue', 0) if entry.get('inventoryValue', 0) > 0 else entry.get('avgCost', 0) * entry.get('quantity', 0)
                for entry in positive_entries
            )
            if total_quantity > 0 and total_value > 0:
                result[code] = {
                    'unitCostAbraAvg': round(total_value / total_quantity, 6),
                    'periodRank': latest_period,
                }
                continue

        fallback = next((item for item in sorted(current_entries, key=lambda value: value.get('lastUpdate') or '', reverse=True) if item.get('avgCost', 0) > 0), None)
        if fallback:
            result[code] = {
                'unitCostAbraAvg': round(fallback.get('avgCost', 0), 6),
                'periodRank': latest_period,
            }

    return result


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


def build_live_cash_snapshot(config):
    rows = abra_records(abra_get(config, 'obratova-predvaha', {
        'detail': 'full',
        'limit': 5000,
    }), 'obratova-predvaha')

    account_rows = []
    total_cash = 0.0

    for row in rows:
        account_value = abra_text(row.get('ucet'))
        account_code = account_value.replace('code:', '') if account_value.startswith('code:') else account_value
        if not account_code.startswith(LIVE_CASH_ACCOUNT_PREFIXES):
            continue

        balance = abra_money(row.get('zustatek'))
        currency = abra_text(row.get('mena@showAs')) or abra_text(row.get('mena'))
        account_label = abra_text(row.get('ucet@showAs')) or account_code
        is_czk_row = 'CZK' in currency
        balance_for_cash = max(balance, 0.0) if is_czk_row else 0.0
        total_cash += balance_for_cash
        account_rows.append({
            'accountCode': account_code,
            'accountLabel': account_label,
            'currency': currency,
            'balance': round(balance, 2),
            'includedBalance': round(balance_for_cash, 2),
        })

    account_rows.sort(key=lambda item: item['includedBalance'], reverse=True)

    return {
        'cashOnAccounts': round(total_cash, 2),
        'cashAccountsSource': {
            'status': 'live',
            'message': 'Cash na účtech a pokladnách je počítán živě z ABRA obratové předvahy pro účty 221 a 211, ze CZK řádků a se započtením kladných zůstatků.',
            'accounts': account_rows[:24],
        },
    }


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

        sorted_entries = sorted(month_entries, key=lambda row: (row['dateSort'], row['amount']), reverse=True)
        expense_entries = [row for row in sorted_entries if row.get('side') == 'náklad']
        revenue_entries = [row for row in sorted_entries if row.get('side') == 'výnos']
        month_entries = sorted(expense_entries + revenue_entries, key=lambda row: (row['dateSort'], row['amount']), reverse=True)
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
    journal_error = None
    try:
        journal = build_live_journal_snapshot(config, now_local)
    except Exception as exc:
        journal_error = str(exc)
        journal = build_journal_snapshot_fallback(now_local, str(exc))

    cash_snapshot = {}
    cash_error = None
    try:
        cash_snapshot = build_live_cash_snapshot(config)
    except Exception as exc:
        cash_error = str(exc)

    cash_payload = {
        'unpaidInvoices': round(unpaid_total, 2),
        'overdueInvoicesCount': overdue_count,
        'overdueInvoicesAmount': round(overdue_amount, 2),
        'largestPayables': sorted(payable_rows, key=lambda row: row['amountDue'], reverse=True)[:8],
    }
    cash_payload.update(cash_snapshot)
    if cash_payload.get('cashOnAccounts') is not None:
        cash_payload['netCashPosition'] = round(float(cash_payload.get('cashOnAccounts') or 0) - float(cash_payload.get('unpaidInvoices') or 0), 2)
    if cash_error:
        cash_payload['cashAccountsSource'] = {
            'status': 'error',
            'message': f'Live cash z ABRA obratové předvahy se nepodařilo načíst ({cash_error}).',
        }

    live_message_bits = ['Závazky z přijatých faktur jsou tahány živě z ABRA API.']
    if cash_snapshot.get('cashOnAccounts') is not None:
        live_message_bits.append('Cash na účtech a pokladnách je také live z ABRA obratové předvahy.')
    else:
        live_message_bits.append('Cash na účtech zatím zůstává na legacy snapshotu.')
    if journal_error:
        live_message_bits.append(f'Účetní deník fallbacknul na poslední snapshot ({journal_error}).')
    if cash_error:
        live_message_bits.append(f'Live cash adapter selhal ({cash_error}).')

    return {
        'source': {
            'status': 'live_payables',
            'message': ' '.join(live_message_bits),
        },
        'cash': cash_payload,
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
    ga4_overview = load_optional_current_json('ga4_overview.json') or {}
    affiliate_overview = load_optional_current_json('affiliate_overview.json') or {}
    ga4_analytics = {
        'ready': bool(ga4_overview),
        'source': (ga4_overview.get('source') or {}),
        'property': (ga4_overview.get('property') or {}),
        'yesterday': ga4_overview.get('yesterday') or {},
        'last7days': ga4_overview.get('last7days') or {},
        'last30days': ga4_overview.get('last30days') or {},
        'currentMonth': ga4_overview.get('currentMonth') or {},
        'previousMonth': ga4_overview.get('previousMonth') or {},
        'channelPerformance7d': ga4_overview.get('channelPerformance7d') or [],
        'channelPerformanceCurrentMonth': ga4_overview.get('channelPerformanceCurrentMonth') or [],
        'landingPages7d': ga4_overview.get('landingPages7d') or [],
        'sourcePerformance7d': ga4_overview.get('sourcePerformance7d') or [],
        'topPages7d': ga4_overview.get('topPages7d') or [],
        'countries7d': ga4_overview.get('countries7d') or [],
        'aiTraffic': ga4_overview.get('aiTraffic') or {},
    }

    def build_channel_rows(sklik_direct, sklik_current, meta_direct, meta_summary, google_direct, google_summary, ecomail_direct, ecomail_current):
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
        if ecomail_direct['ready']:
            channel_rows.append({
                'name': 'Ecomail',
                'amount': round(float(ecomail_current.get('totalAttributedRevenueCzk') or 0), 2),
                'clicks': int(ecomail_current.get('totalClicks') or 0),
                'conversions': round(float(ecomail_current.get('totalAttributedOrders') or 0), 2),
                'roas': None,
                'source': 'live_api',
            })
        return channel_rows

    sklik_overview = load_optional_current_json('sklik_overview.json') or {}
    sklik_current = ((sklik_overview.get('currentMonth') or {}).get('total') or {})
    sklik_previous = ((sklik_overview.get('previousMonth') or {}).get('total') or {})
    sklik_direct = {
        'ready': bool(sklik_overview),
        'label': 'Sklik',
        'source': (sklik_overview.get('source') or {}).get('status'),
        'account': sklik_overview.get('account') or {},
        'currentMonth': sklik_overview.get('currentMonth') or {},
        'previousMonth': sklik_overview.get('previousMonth') or {},
        'campaignSummary': sklik_overview.get('campaignSummary') or {},
        'campaignsCurrentMonth': sklik_overview.get('campaignPerformanceCurrentMonth') or [],
        'campaignsPreviousMonth': sklik_overview.get('campaignPerformancePreviousMonth') or [],
        'topCampaignsCurrentMonth': sorted(
            sklik_overview.get('campaignPerformanceCurrentMonth') or [],
            key=lambda row: float(row.get('priceCzk') or 0),
            reverse=True,
        )[:5],
        'topCampaignsPreviousMonth': sorted(
            sklik_overview.get('campaignPerformancePreviousMonth') or [],
            key=lambda row: float(row.get('priceCzk') or 0),
            reverse=True,
        )[:5],
    }
    meta_overview = load_optional_current_json('meta_ads_overview.json') or {}
    meta_summary = meta_overview.get('summary') or {}
    meta_previous = meta_overview.get('previousMonth') or {}
    meta_direct = {
        'ready': bool(meta_overview),
        'label': 'Meta Ads',
        'source': (meta_overview.get('source') or {}).get('status'),
        'accounts': meta_overview.get('accounts') or [],
        'currentMonth': meta_summary,
        'previousMonth': meta_previous,
        'campaignsCurrentMonth': meta_overview.get('campaignsCurrentMonth') or [],
        'campaignsPreviousMonth': meta_overview.get('campaignsPreviousMonth') or [],
        'topCampaignsCurrentMonth': meta_overview.get('topCampaignsCurrentMonth') or [],
        'topCampaignsPreviousMonth': meta_overview.get('topCampaignsPreviousMonth') or [],
        'dailySummary': meta_overview.get('dailySummary') or [],
        'dailySummaryPreviousMonth': meta_overview.get('dailySummaryPreviousMonth') or [],
    }
    google_overview = load_optional_current_json('google_ads_overview.json') or {}
    google_summary = google_overview.get('summary') or {}
    google_previous = google_overview.get('previousMonth') or {}
    google_direct = {
        'ready': bool(google_overview),
        'label': 'Google Ads',
        'source': (google_overview.get('source') or {}).get('status'),
        'accounts': google_overview.get('accounts') or [],
        'currentMonth': google_summary,
        'previousMonth': google_previous,
        'campaignsCurrentMonth': google_overview.get('campaignsCurrentMonth') or google_overview.get('topCampaignsCurrentMonth') or [],
        'campaignsPreviousMonth': google_overview.get('campaignsPreviousMonth') or google_overview.get('topCampaignsPreviousMonth') or [],
        'topCampaignsCurrentMonth': google_overview.get('topCampaignsCurrentMonth') or google_overview.get('campaignsCurrentMonth') or [],
        'topCampaignsPreviousMonth': google_overview.get('topCampaignsPreviousMonth') or google_overview.get('campaignsPreviousMonth') or [],
        'dailySummary': google_overview.get('dailySummary') or [],
        'dailySummaryPreviousMonth': google_overview.get('dailySummaryPreviousMonth') or [],
    }
    ecomail_overview = load_optional_current_json('ecomail_overview.json') or load_optional_current_json('klaviyo_overview.json') or {}
    ecomail_current = ecomail_overview.get('currentMonth') or {}
    ecomail_previous = ecomail_overview.get('previousMonth') or {}
    ecomail_direct = {
        'ready': bool(ecomail_overview),
        'label': 'Ecomail',
        'source': (ecomail_overview.get('source') or {}).get('status'),
        'account': ecomail_overview.get('account') or {},
        'currentMonth': ecomail_current,
        'previousMonth': ecomail_previous,
        'dailySummary': ecomail_overview.get('dailySummary') or [],
        'dailySummaryPreviousMonth': ecomail_overview.get('dailySummaryPreviousMonth') or [],
        'flowsCurrentMonth': ecomail_overview.get('flowsCurrentMonth') or [],
        'flowsPreviousMonth': ecomail_overview.get('flowsPreviousMonth') or [],
        'topFlowsCurrentMonth': ecomail_overview.get('topFlowsCurrentMonth') or [],
        'topFlowsPreviousMonth': ecomail_overview.get('topFlowsPreviousMonth') or [],
        'recentCampaigns': ecomail_overview.get('recentCampaigns') or [],
        'recentCampaignsPreviousMonth': ecomail_overview.get('recentCampaignsPreviousMonth') or [],
    }
    affiliate_direct = {
        'ready': bool(affiliate_overview),
        'label': 'Affiliate',
        'source': (affiliate_overview.get('source') or {}).get('status'),
        'period': affiliate_overview.get('period') or {},
        'summary': affiliate_overview.get('summary') or {},
        'reports': affiliate_overview.get('reports') or {},
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
        previous_month = monthly[-2] if len(monthly) > 1 else {
            'label': ((finance_snapshot.get('previousMonth') or {}).get('label') or ((sklik_direct.get('previousMonth') or {}).get('dateTo') or (meta_direct.get('previousMonth') or {}).get('dateTo') or (google_direct.get('previousMonth') or {}).get('dateTo') or '')),
            'performanceSpend': round(float(meta_previous.get('spendCzk') or 0) + float(google_previous.get('spendCzk') or 0) + float(sklik_previous.get('priceCzk') or 0), 2),
            'brandSpend': 0.0,
            'totalSpend': round(float(meta_previous.get('spendCzk') or 0) + float(google_previous.get('spendCzk') or 0) + float(sklik_previous.get('priceCzk') or 0), 2),
            'revenue': round(float(((finance_snapshot.get('previousMonth') or {}).get('revenue') or 0)), 2),
            'spendShareOfRevenuePct': safe_ratio(
                round(float(meta_previous.get('spendCzk') or 0) + float(google_previous.get('spendCzk') or 0) + float(sklik_previous.get('priceCzk') or 0), 2),
                round(float(((finance_snapshot.get('previousMonth') or {}).get('revenue') or 0)), 2),
            ),
        }
        direct_sources = {'sklik': sklik_direct, 'meta': meta_direct, 'google': google_direct, 'ecomail': ecomail_direct, 'klaviyo': ecomail_direct, 'affiliate': affiliate_direct}
        channel_rows = build_channel_rows(sklik_direct, sklik_current, meta_direct, meta_summary, google_direct, google_summary, ecomail_direct, ecomail_current)
        source_message = 'Marketing se skládá z live ABRA reportu a aktuálních položek z účetního deníku.'
        live_labels = []
        if sklik_direct['ready']:
            live_labels.append('Sklik')
        if meta_direct['ready']:
            live_labels.append('Meta Ads')
        if google_direct['ready']:
            live_labels.append('Google Ads')
        if ecomail_direct['ready']:
            live_labels.append('Ecomail')
        if affiliate_direct['ready']:
            live_labels.append('Affiliate')
        if live_labels:
            source_message += ' Přímé platformy přes API: ' + ', '.join(live_labels) + '.'
        return {
            'generatedAt': generated_at,
            'source': {'status': 'live_report', 'message': source_message},
            'analytics': ga4_analytics,
            'monthly': monthly,
            'currentMonth': current_month,
            'previousMonth': previous_month,
            'topSuppliersCurrentMonth': [
                {'name': name, 'amount': round(amount, 2)}
                for name, amount in sorted(supplier_totals.items(), key=lambda item: item[1], reverse=True)[:8]
            ],
            'entriesCurrentMonth': current_entries[:20],
            'directSources': direct_sources,
            'channelsCurrentMonth': channel_rows,
            'activeCampaignsBySource': active_campaigns,
        }

    direct_sources = {'sklik': sklik_direct, 'meta': meta_direct, 'google': google_direct, 'ecomail': ecomail_direct, 'klaviyo': ecomail_direct, 'affiliate': affiliate_direct}
    live_labels = [source['label'] for source in direct_sources.values() if source.get('ready')]
    channel_rows = build_channel_rows(sklik_direct, sklik_current, meta_direct, meta_summary, google_direct, google_summary, ecomail_direct, ecomail_current)
    live_current_month = {
        'label': (finance_snapshot.get('currentMonth') or {}).get('label') or ((meta_summary.get('dateTo') or google_summary.get('dateTo') or (sklik_direct.get('currentMonth') or {}).get('dateTo') or ecomail_current.get('dateTo') or 'aktuální měsíc')),
        'performanceSpend': round(float(meta_summary.get('spendCzk') or 0) + float(google_summary.get('spendCzk') or 0) + float(sklik_current.get('priceCzk') or 0), 2),
        'brandSpend': 0.0,
        'totalSpend': round(float(meta_summary.get('spendCzk') or 0) + float(google_summary.get('spendCzk') or 0) + float(sklik_current.get('priceCzk') or 0), 2),
        'revenue': round(float(((finance_snapshot.get('currentMonth') or {}).get('revenue') or 0)), 2),
    }
    live_current_month['spendShareOfRevenuePct'] = safe_ratio(live_current_month['totalSpend'], live_current_month['revenue'])
    live_previous_month = {
        'label': ((finance_snapshot.get('previousMonth') or {}).get('label') or ((sklik_direct.get('previousMonth') or {}).get('dateTo') or meta_previous.get('dateTo') or google_previous.get('dateTo') or ecomail_previous.get('dateTo') or 'předchozí měsíc')),
        'performanceSpend': round(float(meta_previous.get('spendCzk') or 0) + float(google_previous.get('spendCzk') or 0) + float(sklik_previous.get('priceCzk') or 0), 2),
        'brandSpend': 0.0,
        'totalSpend': round(float(meta_previous.get('spendCzk') or 0) + float(google_previous.get('spendCzk') or 0) + float(sklik_previous.get('priceCzk') or 0), 2),
        'revenue': round(float(((finance_snapshot.get('previousMonth') or {}).get('revenue') or 0)), 2),
    }
    live_previous_month['spendShareOfRevenuePct'] = safe_ratio(live_previous_month['totalSpend'], live_previous_month['revenue'])

    if live_labels:
        source_message = 'Marketing je teď skládaný přímo z live marketing API.'
        if (finance_snapshot.get('source') or {}).get('status') in {'live_report', 'mixed_live_legacy', 'live_payables_only', 'legacy_with_live_error', 'legacy_snapshot'}:
            source_message += ' Revenue benchmark bere z finance snapshotu.'
        source_message += ' Přímé platformy přes API: ' + ', '.join(live_labels) + '.'
        return {
            'generatedAt': generated_at,
            'source': {'status': 'live_channels', 'message': source_message},
            'analytics': ga4_analytics,
            'monthly': [live_previous_month, live_current_month] if live_previous_month.get('label') else [live_current_month],
            'currentMonth': live_current_month,
            'previousMonth': live_previous_month,
            'topSuppliersCurrentMonth': [],
            'entriesCurrentMonth': [],
            'directSources': direct_sources,
            'channelsCurrentMonth': channel_rows,
            'activeCampaignsBySource': active_campaigns,
        }

    if not legacy_abra_payload:
        return {
            'generatedAt': generated_at,
            'source': {'status': 'missing', 'message': 'Legacy marketing snapshot nebyl nalezen.'},
            'analytics': ga4_analytics,
            'monthly': [],
            'currentMonth': {},
            'previousMonth': {},
            'topSuppliersCurrentMonth': [],
            'entriesCurrentMonth': [],
            'directSources': direct_sources,
            'channelsCurrentMonth': channel_rows,
            'activeCampaignsBySource': active_campaigns,
        }

    model = legacy_abra_payload['model']
    marketing_group = next((group for group in model.get('groups') or [] if group.get('id') == 'marketing'), None)
    if not marketing_group:
        return {
            'generatedAt': generated_at,
            'source': {'status': 'missing', 'message': 'Marketing skupina ve legacy ABRA modelu chybí.'},
            'analytics': ga4_analytics,
            'monthly': [],
            'currentMonth': {},
            'previousMonth': {},
            'topSuppliersCurrentMonth': [],
            'entriesCurrentMonth': [],
            'directSources': direct_sources,
            'channelsCurrentMonth': channel_rows,
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
    previous_month = monthly[-2] if len(monthly) > 1 else {}
    channel_rows = build_channel_rows(sklik_direct, sklik_current, meta_direct, meta_summary, google_direct, google_summary, ecomail_direct, ecomail_current)
    return {
        'generatedAt': generated_at,
        'source': legacy_abra_payload['source'],
        'analytics': ga4_analytics,
        'monthly': monthly,
        'currentMonth': current_month,
        'previousMonth': previous_month,
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
        'directSources': direct_sources,
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


def build_journal_snapshot_fallback(now_local, reason=None):
    fallback_payload = load_optional_current_json('finance_overview.json') or load_previous_snapshot_json('finance_overview.json') or {}
    fallback_journal = fallback_payload.get('journal') or {}
    monthly = fallback_journal.get('monthly') or []
    current_label = month_label(month_floor(now_local))
    current_month = next((row for row in monthly if row.get('label') == current_label), None) or fallback_journal.get('currentMonth') or {
        'label': current_label,
        'topExpenseAccounts': [],
        'topExpenseClasses': [],
        'topVendors': [],
        'recentEntries': [],
    }
    message = 'Live účetní deník se nepodařilo načíst, použit poslední úspěšný snapshot.'
    if reason:
        message = f'{message} ({reason})'
    return {
        'source': {
            'status': 'fallback_snapshot',
            'message': message,
        },
        'monthly': monthly,
        'currentMonth': current_month,
    }


def ensure_daily_sklik_snapshot(now_local):
    return MARKETING_SOURCES.ensure_sklik_snapshot(now_local)


def ensure_daily_meta_snapshot(now_local):
    return MARKETING_SOURCES.ensure_meta_snapshot(now_local)


def ensure_daily_google_snapshot(now_local):
    return MARKETING_SOURCES.ensure_google_snapshot(now_local)


def ensure_daily_ga4_snapshot(now_local):
    return MARKETING_SOURCES.ensure_ga4_snapshot(now_local)


def ensure_daily_klaviyo_snapshot(now_local):
    return MARKETING_SOURCES.ensure_ecomail_snapshot(now_local)


def run_twisto_watchdog(snapshot_path: Path, generated_at: str):
    script_path = ROOT / 'scripts' / 'build_twisto_watchdog.py'
    if not script_path.exists():
        return load_optional_current_json('twisto_watchdog.json') or {
            'generatedAt': generated_at,
            'alert': {'status': 'missing', 'lines': ['Twisto watchdog script chybí.']},
            'summary': {},
            'source': {'twisto': {'status': 'missing'}},
        }
    try:
        result = subprocess.run(
            ['python3', str(script_path), '--snapshot-path', str(snapshot_path), '--generated-at', generated_at],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except Exception as exc:
        fallback = load_optional_current_json('twisto_watchdog.json') or {}
        fallback.setdefault('alert', {'status': 'warn', 'lines': []})
        fallback['generatedAt'] = generated_at
        fallback['alert']['status'] = 'warn'
        fallback['alert']['lines'] = (fallback['alert'].get('lines') or []) + [f'Twisto watchdog se nepodařilo spustit: {exc}']
        return fallback

    if result.returncode != 0:
        message = (result.stderr or result.stdout or '').strip()
        fallback = load_optional_current_json('twisto_watchdog.json') or {
            'generatedAt': generated_at,
            'summary': {},
            'source': {'twisto': {'status': 'warn'}},
            'alert': {'status': 'warn', 'lines': []},
        }
        fallback['generatedAt'] = generated_at
        fallback['alert']['status'] = 'warn'
        fallback['alert']['lines'] = [f'Twisto watchdog selhal: {message[:300] or "bez detailu"}']
        return fallback

    payload = load_optional_current_json('twisto_watchdog.json') or {
        'generatedAt': generated_at,
        'summary': {},
        'source': {'twisto': {'status': 'warn'}},
        'alert': {'status': 'warn', 'lines': ['Twisto watchdog doběhl, ale payload chybí.']},
    }
    return payload


def build_refresh_runtime_context():
    load_env_file(ENV_FILE)
    if REMOTE_STORAGE_ENV_FILE.exists():
        load_env_file(REMOTE_STORAGE_ENV_FILE)
    manual_overrides = load_manual_sku_overrides(SKU_MAPPING_OVERRIDE_FILE)
    pos_admin_views = load_pos_admin_view_overrides(POS_ADMIN_VIEW_OVERRIDE_FILE)
    pos_view_filters = load_pos_view_filter_ids(POS_ADMIN_VIEW_OVERRIDE_FILE)
    ordering_reference_overrides = load_ordering_reference_overrides(ORDERING_REFERENCE_OVERRIDE_FILE)
    ordering_packaging_map = load_ordering_packaging_map(ORDERING_PACKAGING_MATCH_FILE)
    store_expiry_input = load_store_expiry_input(STORE_EXPIRY_BATCHES_FILE, SETTINGS.store_expiry_sheet_csv_url)
    warehouse_code = SETTINGS.fourpx_warehouse_code
    max_pages = SETTINGS.fourpx_outbound_max_pages
    now_local = current_local_time()
    stamp = now_local.strftime('%Y%m%d-%H%M%S')
    generated_at = now_local.isoformat()
    report_start, report_end = previous_day_window(now_local)
    report_date = report_start.date()

    required = (
        'FOURPX_CZ_APP_KEY', 'FOURPX_CZ_APP_SECRET',
        'FOURPX_SK_APP_KEY', 'FOURPX_SK_APP_SECRET',
    )
    missing = SETTINGS.missing(*required)
    if missing:
        raise SystemExit(f'Missing required env keys: {", ".join(missing)}')

    cz_app_key, cz_app_secret = SETTINGS.fourpx_credentials('CZ')
    sk_app_key, sk_app_secret = SETTINGS.fourpx_credentials('SK')

    previous_wpj_products = None
    previous_snapshot = load_previous_snapshot_json('wpj_products.json')
    if previous_snapshot:
        previous_wpj_products = previous_snapshot.get('items') or []

    return RefreshRuntimeContext(
        manual_overrides=manual_overrides,
        pos_admin_views=pos_admin_views,
        pos_view_filters=pos_view_filters,
        ordering_reference_overrides=ordering_reference_overrides,
        ordering_packaging_map=ordering_packaging_map,
        store_expiry_input=store_expiry_input,
        warehouse_code=warehouse_code,
        max_pages=max_pages,
        now_local=now_local,
        stamp=stamp,
        generated_at=generated_at,
        report_start=report_start,
        report_end=report_end,
        report_date=report_date,
        cz_app_key=cz_app_key,
        cz_app_secret=cz_app_secret,
        sk_app_key=sk_app_key,
        sk_app_secret=sk_app_secret,
        previous_wpj_products=previous_wpj_products,
    )


def fetch_refresh_inputs(ctx: RefreshRuntimeContext) -> RefreshFetchResult:
    cz_inventory = fetch_inventory(ctx.cz_app_key, ctx.cz_app_secret, ctx.warehouse_code)
    sk_inventory = fetch_inventory(ctx.sk_app_key, ctx.sk_app_secret, ctx.warehouse_code)
    cz_inventory_detail = fetch_inventory_details(ctx.cz_app_key, ctx.cz_app_secret, ctx.warehouse_code, cz_inventory['items'])
    sk_inventory_detail = fetch_inventory_details(ctx.sk_app_key, ctx.sk_app_secret, ctx.warehouse_code, sk_inventory['items'])
    cz_expiry_summary = summarize_expiry_details('CZ', cz_inventory_detail['items'])
    sk_expiry_summary = summarize_expiry_details('SK', sk_inventory_detail['items'])
    cz_outbound = fetch_recent_outbound(
        ctx.cz_app_key,
        ctx.cz_app_secret,
        ctx.warehouse_code,
        max_pages=ctx.max_pages,
        stop_before=ctx.report_start,
    )
    sk_outbound = fetch_recent_outbound(
        ctx.sk_app_key,
        ctx.sk_app_secret,
        ctx.warehouse_code,
        max_pages=ctx.max_pages,
        stop_before=ctx.report_start,
    )
    wpj_ready = bool(wpj_endpoint() and SETTINGS.wpj_access_token)
    legacy_abra_payload = extract_legacy_abra_model(LEGACY_ABRA_HTML)
    live_abra_payload = fetch_abra_live_snapshot(ctx.now_local)
    abra_vykaz_hospodareni_reports = fetch_abra_vykaz_hospodareni_reports(ctx.now_local)
    sklik_status = ensure_daily_sklik_snapshot(ctx.now_local)
    meta_status = ensure_daily_meta_snapshot(ctx.now_local)
    google_status = ensure_daily_google_snapshot(ctx.now_local)
    ga4_status = ensure_daily_ga4_snapshot(ctx.now_local)
    klaviyo_status = ensure_daily_klaviyo_snapshot(ctx.now_local)
    finance_snapshot = build_finance_snapshot(
        legacy_abra_payload,
        live_abra_payload,
        abra_vykaz_hospodareni_reports,
        ctx.generated_at,
    )
    try:
        affiliate_overview = build_affiliate_overview(ctx.generated_at, ctx.now_local)
    except Exception as exc:
        print(f'WARN: affiliate overview refresh failed, reusing last snapshot if available: {exc}', flush=True)
        affiliate_overview = load_optional_current_json('affiliate_overview.json') or {
            'generatedAt': ctx.generated_at,
            'source': {
                'status': 'unavailable',
                'message': f'Affiliate přehled se nepodařilo aktualizovat: {exc}',
            },
            'period': {},
            'summary': {},
            'reports': {},
        }
    marketing_snapshot = build_marketing_snapshot(
        legacy_abra_payload,
        abra_vykaz_hospodareni_reports,
        finance_snapshot,
        ctx.generated_at,
    )
    return RefreshFetchResult(
        cz_inventory=cz_inventory,
        sk_inventory=sk_inventory,
        cz_inventory_detail=cz_inventory_detail,
        sk_inventory_detail=sk_inventory_detail,
        cz_expiry_summary=cz_expiry_summary,
        sk_expiry_summary=sk_expiry_summary,
        cz_outbound=cz_outbound,
        sk_outbound=sk_outbound,
        wpj_ready=wpj_ready,
        legacy_abra_payload=legacy_abra_payload,
        live_abra_payload=live_abra_payload,
        abra_vykaz_hospodareni_reports=abra_vykaz_hospodareni_reports,
        sklik_status=sklik_status,
        meta_status=meta_status,
        google_status=google_status,
        ga4_status=ga4_status,
        klaviyo_status=klaviyo_status,
        finance_snapshot=finance_snapshot,
        affiliate_overview=affiliate_overview,
        marketing_snapshot=marketing_snapshot,
    )


def build_empty_refresh_state(generated_at: str) -> RefreshBuildState:
    return RefreshBuildState(
        warnings=[],
        wpj_summary={
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
        },
        wpj_orders_payload={'generatedAt': generated_at, 'items': []},
        wpj_products_payload={'generatedAt': generated_at, 'items': []},
        wpj_history_payload={'generatedAt': generated_at, 'days': []},
        eshop_ytd_payload={'generatedAt': generated_at, 'years': {}, 'months': [], 'totals': {}},
        customer_fact_payload={'generatedAt': generated_at, 'window': {}, 'ordersProcessed': 0, 'customersCount': 0, 'summary': {}, 'customers': []},
        order_fact_payload={'generatedAt': generated_at, 'window': {}, 'summary': {}, 'orders': []},
        inventory_analytics_payload={'generatedAt': generated_at, 'summary': {}, 'topTurnover': [], 'deadStock': [], 'slowMovers': [], 'overstocked': [], 'fastLowCover': []},
        inventory_analytics_730_payload={'generatedAt': generated_at, 'summary': {}, 'topTurnover': [], 'deadStock': [], 'slowMovers': [], 'overstocked': [], 'fastLowCover': [], 'items': []},
        inventory_analytics_730_cz_payload={'generatedAt': generated_at, 'market': 'cz', 'summary': {}, 'topTurnover': [], 'deadStock': [], 'slowMovers': [], 'overstocked': [], 'fastLowCover': [], 'items': []},
        inventory_analytics_730_sk_payload={'generatedAt': generated_at, 'market': 'sk', 'summary': {}, 'topTurnover': [], 'deadStock': [], 'slowMovers': [], 'overstocked': [], 'fastLowCover': [], 'items': []},
        ordering_core_payload={'generatedAt': generated_at, 'summary': {}, 'alerts': [], 'criticalReorder': [], 'reorderWatch': [], 'overstockRisks': [], 'trendWatch': [], 'suggestedFillers': []},
        ordering_core_cz_payload={'generatedAt': generated_at, 'market': 'cz', 'summary': {}, 'alerts': [], 'criticalReorder': [], 'reorderWatch': [], 'overstockRisks': [], 'trendWatch': [], 'suggestedFillers': []},
        ordering_core_sk_payload={'generatedAt': generated_at, 'market': 'sk', 'summary': {}, 'alerts': [], 'criticalReorder': [], 'reorderWatch': [], 'overstockRisks': [], 'trendWatch': [], 'suggestedFillers': []},
        ordering_reference_payload={'generatedAt': generated_at, 'summary': {}, 'items': [], 'excludedTop': []},
        ordering_reference_cz_payload={'generatedAt': generated_at, 'market': 'cz', 'summary': {}, 'items': [], 'excludedTop': []},
        ordering_reference_sk_payload={'generatedAt': generated_at, 'market': 'sk', 'summary': {}, 'items': [], 'excludedTop': []},
        ordering_sales_history_payload={'generatedAt': generated_at, 'window': {}, 'summary': {}, 'codes': {}},
        expiry_overview_payload={'generatedAt': generated_at, 'summary': {}, 'topExpiring': []},
        store_expiry_watchdog_payload={'generatedAt': generated_at, 'source': {'status': 'missing', 'mode': 'none'}, 'summary': {}, 'alerts': [], 'warnings': [], 'groups': [], 'items': [], 'allItems': []},
        combined_index_payload={'generatedAt': generated_at, 'items': [], 'counts': {}},
        combined_overview_payload={'generatedAt': generated_at, 'counts': {}},
        stock_summary={
            'lowStockSoldYesterday': [],
            'lowStockOverall': [],
            'negativeStoreStock': [],
            'largestMovesSinceLastSnapshot': [],
        },
    )


def populate_refresh_wpj_state(ctx: RefreshRuntimeContext, fetch_result: RefreshFetchResult, state: RefreshBuildState):
    generated_at = ctx.generated_at
    now_local = ctx.now_local
    report_start = ctx.report_start
    report_end = ctx.report_end
    report_date = ctx.report_date
    wpj_url = wpj_endpoint()
    wpj_token = SETTINGS.wpj_access_token
    history_start = report_start - timedelta(days=7)
    ytd_start = datetime(now_local.year - 1, 1, 1, 0, 0, 0, tzinfo=PRAGUE_TZ)
    year_start = report_start - timedelta(days=364)
    two_year_start = report_start - timedelta(days=ORDERING_ANALYTICS_DAYS - 1)

    history_orders = fetch_wpj_orders(wpj_url, wpj_token, history_start, report_end, limit=1000, detailed=False)
    ytd_orders = fetch_wpj_orders(wpj_url, wpj_token, ytd_start, now_local, limit=1000, detailed=False)
    yesterday_orders = fetch_wpj_orders(wpj_url, wpj_token, report_start, report_end, limit=250, detailed=True)
    expiry_sales_orders = fetch_expiry_exact_sales_orders(
        wpj_url,
        wpj_token,
        report_end,
        pos_view_ids=ctx.pos_view_filters,
        # Expiry pages use the exact 30d movement layer; keep the pull narrow so refresh stays reliable.
        window_days=30,
        limit=1000,
    )
    history_orders = apply_pos_view_overrides_to_orders(history_orders, wpj_url, wpj_token, history_start, report_end, detailed=False, pos_view_ids=ctx.pos_view_filters, limit=1000)
    ytd_orders = apply_pos_view_overrides_to_orders(ytd_orders, wpj_url, wpj_token, ytd_start, now_local, detailed=False, pos_view_ids=ctx.pos_view_filters, limit=1000)
    yesterday_orders = apply_pos_view_overrides_to_orders(yesterday_orders, wpj_url, wpj_token, report_start, report_end, detailed=True, pos_view_ids=ctx.pos_view_filters, limit=250)
    wpj_products = fetch_wpj_products(wpj_url, wpj_token)

    state.wpj_summary = summarize_orders(yesterday_orders, pos_admin_views=ctx.pos_admin_views)
    history_days, state.baseline_orders, state.baseline_revenue = summarize_daily_history(history_orders, report_date, pos_admin_views=ctx.pos_admin_views)
    state.mtd_summary = build_mtd_revenue_snapshot(ytd_orders, report_date, pos_admin_views=ctx.pos_admin_views)
    state.eshop_ytd_payload = build_eshop_ytd_payload(ytd_orders, generated_at, now_local, pos_admin_views=ctx.pos_admin_views)
    state.customer_fact_payload = build_customer_fact_payload(
        ytd_orders,
        generated_at,
        {'from': ytd_start.isoformat(), 'to': now_local.isoformat()},
        pos_admin_views=ctx.pos_admin_views,
    )
    state.order_fact_payload = build_order_fact_payload(
        ytd_orders,
        generated_at,
        {'from': ytd_start.isoformat(), 'to': now_local.isoformat()},
        pos_admin_views=ctx.pos_admin_views,
    )
    state.stock_summary = summarize_stock(
        wpj_products,
        state.wpj_summary['soldProductCodes'],
        previous_products=ctx.previous_wpj_products,
        ordering_reference_overrides=ctx.ordering_reference_overrides,
    )
    state.stock_summary = filter_non_orderable_stock_rows(
        state.stock_summary,
        ordering_reference_overrides=ctx.ordering_reference_overrides,
    )

    store_expiry_sales_orders, store_expiry_sales_warnings = fetch_store_expiry_sales_orders(
        ctx,
        ctx.store_expiry_input.get('rows') or [],
    )
    state.store_expiry_watchdog_payload = build_store_expiry_watchdog(
        generated_at,
        now_local,
        ctx.store_expiry_input,
        wpj_products=wpj_products,
        sales_orders=store_expiry_sales_orders,
        manual_overrides=ctx.manual_overrides,
        pos_admin_views=ctx.pos_admin_views,
    )
    state.store_expiry_watchdog_payload['warnings'].extend(store_expiry_sales_warnings)

    combined_products_ctx = CombinedProductsBuildContext(
        wpj_products=wpj_products,
        yesterday_orders=yesterday_orders,
        cz_inventory=fetch_result.cz_inventory,
        sk_inventory=fetch_result.sk_inventory,
        cz_outbound=fetch_result.cz_outbound,
        sk_outbound=fetch_result.sk_outbound,
        start_dt=report_start,
        end_dt=report_end,
        generated_at=generated_at,
        manual_overrides=ctx.manual_overrides,
        pos_admin_views=ctx.pos_admin_views,
    )
    state.combined_index_payload, state.combined_overview_payload = build_combined_product_views(combined_products_ctx)
    state.expiry_overview_payload = build_expiry_overview(
        generated_at,
        state.combined_index_payload,
        fetch_result.cz_expiry_summary,
        fetch_result.sk_expiry_summary,
        cz_inventory_items=fetch_result.cz_inventory.get('items') or [],
        sk_inventory_items=fetch_result.sk_inventory.get('items') or [],
        sales_orders=expiry_sales_orders,
        end_dt=report_end,
        pos_admin_views=ctx.pos_admin_views,
    )

    analytics_cache_path = CURRENT_DIR / 'inventory_analytics_365d.json'
    analytics_730_cache_path = CURRENT_DIR / 'inventory_analytics_730d.json'
    ordering_core_cache_path = CURRENT_DIR / 'ordering_core.json'
    ordering_sales_history_cache_path = CURRENT_DIR / 'ordering_sales_history.json'
    ordering_actions_cache_path = CURRENT_DIR / 'ordering_actions.json'
    ordering_actions_overrides = load_ordering_actions_overrides(ORDERING_ACTIONS_OVERRIDE_FILE)
    state.inventory_analytics_payload = load_json_if_fresh(analytics_cache_path, max_age_hours=24, freshness_key='sourceGeneratedAt')
    state.inventory_analytics_730_payload = load_json_if_fresh(analytics_730_cache_path, max_age_hours=24, freshness_key='sourceGeneratedAt')
    state.ordering_core_payload = load_json_if_fresh(ordering_core_cache_path, max_age_hours=24)
    state.ordering_sales_history_payload = load_json_if_fresh(ordering_sales_history_cache_path, max_age_hours=24)

    if state.inventory_analytics_payload:
        state.inventory_analytics_payload = mark_payload_refreshed(state.inventory_analytics_payload, generated_at)
    if state.inventory_analytics_730_payload:
        state.inventory_analytics_730_payload = mark_payload_refreshed(state.inventory_analytics_730_payload, generated_at)
    if state.ordering_core_payload:
        state.ordering_core_payload = mark_payload_refreshed(state.ordering_core_payload, generated_at)
    if state.ordering_sales_history_payload:
        state.ordering_sales_history_payload = mark_payload_refreshed(state.ordering_sales_history_payload, generated_at)
    wpj_by_code = {item.get('code'): item for item in wpj_products if item.get('code')}
    abra_costs_by_code = fetch_abra_average_cost_map(wpj_by_code.keys())

    state.inventory_analytics_payload, analytics_prices_changed = enrich_inventory_analytics_prices(state.inventory_analytics_payload, wpj_by_code)
    state.inventory_analytics_730_payload, analytics_730_prices_changed = enrich_inventory_analytics_prices(state.inventory_analytics_730_payload, wpj_by_code)
    state.inventory_analytics_payload, analytics_abra_costs_changed = enrich_inventory_analytics_abra_costs(state.inventory_analytics_payload, abra_costs_by_code)
    state.inventory_analytics_730_payload, analytics_730_abra_costs_changed = enrich_inventory_analytics_abra_costs(state.inventory_analytics_730_payload, abra_costs_by_code)
    state.inventory_analytics_payload, analytics_targets_changed = reapply_inventory_recommendation_targets(state.inventory_analytics_payload)
    state.inventory_analytics_730_payload, analytics_730_targets_changed = reapply_inventory_recommendation_targets(state.inventory_analytics_730_payload)
    state.inventory_analytics_payload, analytics_reference_changed = reapply_ordering_reference_to_analytics(state.inventory_analytics_payload, ctx.ordering_reference_overrides)
    state.inventory_analytics_730_payload, analytics_730_reference_changed = reapply_ordering_reference_to_analytics(state.inventory_analytics_730_payload, ctx.ordering_reference_overrides)
    state.inventory_analytics_payload, analytics_packaging_changed = reapply_ordering_packaging_to_analytics(state.inventory_analytics_payload, ctx.ordering_packaging_map)
    state.inventory_analytics_730_payload, analytics_730_packaging_changed = reapply_ordering_packaging_to_analytics(state.inventory_analytics_730_payload, ctx.ordering_packaging_map)
    state.inventory_analytics_payload, analytics_stock_changed = reapply_combined_stock_to_analytics(state.inventory_analytics_payload, state.combined_index_payload, market_key='complete')
    state.inventory_analytics_730_payload, analytics_730_stock_changed = reapply_combined_stock_to_analytics(state.inventory_analytics_730_payload, state.combined_index_payload, market_key='complete')

    if (analytics_prices_changed or analytics_abra_costs_changed or analytics_targets_changed or analytics_reference_changed or analytics_packaging_changed or analytics_stock_changed) and state.inventory_analytics_payload:
        write_json(analytics_cache_path, state.inventory_analytics_payload)
    if (analytics_730_prices_changed or analytics_730_abra_costs_changed or analytics_730_targets_changed or analytics_730_reference_changed or analytics_730_packaging_changed or analytics_730_stock_changed) and state.inventory_analytics_730_payload:
        write_json(analytics_730_cache_path, state.inventory_analytics_730_payload)

    analytics_orders = None
    if (
        not state.inventory_analytics_payload or not state.inventory_analytics_payload.get('items')
        or not state.inventory_analytics_730_payload or not state.inventory_analytics_730_payload.get('items')
        or not state.ordering_core_payload or not state.ordering_core_payload.get('summary')
    ):
        analytics_orders = fetch_wpj_year_order_metrics(wpj_url, wpj_token, two_year_start, report_end, limit=1000)
        analytics_orders = apply_pos_view_overrides_to_orders(analytics_orders, wpj_url, wpj_token, two_year_start, report_end, detailed=False, pos_view_ids=ctx.pos_view_filters, limit=1000)
        state.inventory_analytics_payload = build_inventory_analytics_365d(InventoryAnalyticsBuildContext(
            combined_index=state.combined_index_payload,
            orders=analytics_orders,
            start_dt=year_start,
            end_dt=report_end,
            generated_at=generated_at,
            wpj_by_code=wpj_by_code,
            manual_overrides=ctx.manual_overrides,
            pos_admin_views=ctx.pos_admin_views,
            ordering_reference_overrides=ctx.ordering_reference_overrides,
            ordering_packaging_map=ctx.ordering_packaging_map,
        ))
        state.inventory_analytics_730_payload = build_inventory_analytics_730d(InventoryAnalyticsBuildContext(
            combined_index=state.combined_index_payload,
            orders=analytics_orders,
            start_dt=two_year_start,
            end_dt=report_end,
            generated_at=generated_at,
            wpj_by_code=wpj_by_code,
            manual_overrides=ctx.manual_overrides,
            pos_admin_views=ctx.pos_admin_views,
            ordering_reference_overrides=ctx.ordering_reference_overrides,
            ordering_packaging_map=ctx.ordering_packaging_map,
        ))
        state.ordering_core_payload = build_ordering_core(OrderingCoreBuildContext(
            analytics_payload=state.inventory_analytics_730_payload,
            generated_at=generated_at,
        ))
    elif analytics_730_prices_changed or analytics_730_reference_changed or analytics_730_packaging_changed or analytics_730_stock_changed:
        state.ordering_core_payload = build_ordering_core(OrderingCoreBuildContext(
            analytics_payload=state.inventory_analytics_730_payload,
            generated_at=generated_at,
        ))

    state.ordering_reference_payload = build_ordering_reference_data(state.inventory_analytics_730_payload, generated_at)
    state.inventory_analytics_730_cz_payload = build_inventory_analytics_market_view(state.inventory_analytics_730_payload, state.combined_index_payload, generated_at, market_key='cz')
    state.inventory_analytics_730_sk_payload = build_inventory_analytics_market_view(state.inventory_analytics_730_payload, state.combined_index_payload, generated_at, market_key='sk')
    state.ordering_core_cz_payload = build_ordering_core(OrderingCoreBuildContext(
        analytics_payload=state.inventory_analytics_730_cz_payload,
        generated_at=generated_at,
    ))
    state.ordering_core_sk_payload = build_ordering_core(OrderingCoreBuildContext(
        analytics_payload=state.inventory_analytics_730_sk_payload,
        generated_at=generated_at,
    ))
    state.ordering_reference_cz_payload = build_ordering_reference_data(state.inventory_analytics_730_cz_payload, generated_at)
    state.ordering_reference_sk_payload = build_ordering_reference_data(state.inventory_analytics_730_sk_payload, generated_at)

    if ordering_sales_history_needs_rebuild(state.ordering_sales_history_payload, now_local):
        ordering_history_end = now_local
        if analytics_orders is None:
            analytics_orders = fetch_wpj_year_order_metrics(wpj_url, wpj_token, two_year_start, ordering_history_end, limit=1000)
            analytics_orders = apply_pos_view_overrides_to_orders(analytics_orders, wpj_url, wpj_token, two_year_start, ordering_history_end, detailed=False, pos_view_ids=ctx.pos_view_filters, limit=1000)
        state.ordering_sales_history_payload = build_ordering_sales_history_payload(OrderingSalesHistoryBuildContext(
            orders=analytics_orders,
            start_dt=two_year_start,
            end_dt=ordering_history_end,
            generated_at=generated_at,
            wpj_by_code=wpj_by_code,
            manual_overrides=ctx.manual_overrides,
            pos_admin_views=ctx.pos_admin_views,
        ))
    existing_ordering_actions_payload = load_optional_current_json('ordering_actions.json')
    refreshed_ordering_actions_payload = refresh_ordering_actions_payload(
        existing_ordering_actions_payload,
        market_payloads={
            'complete': state.inventory_analytics_730_payload,
            'cz': state.inventory_analytics_730_cz_payload,
            'sk': state.inventory_analytics_730_sk_payload,
        },
        combined_index_payload=state.combined_index_payload,
        sales_history_payload=state.ordering_sales_history_payload,
        generated_at=generated_at,
        overrides=ordering_actions_overrides,
    )
    if refreshed_ordering_actions_payload:
        write_json(ordering_actions_cache_path, refreshed_ordering_actions_payload)

    state.wpj_orders_payload = {
        'generatedAt': generated_at,
        'window': {'from': report_start.isoformat(), 'to': report_end.isoformat()},
        'items': yesterday_orders,
        'summary': state.wpj_summary,
    }
    state.wpj_products_payload = {'generatedAt': generated_at, 'items': wpj_products}
    state.wpj_history_payload = {
        'generatedAt': generated_at,
        'window': {'from': history_start.isoformat(), 'to': report_end.isoformat()},
        'days': history_days,
    }


def append_refresh_source_warnings(fetch_result: RefreshFetchResult, warnings: list[str]):
    if fetch_result.finance_snapshot.get('source', {}).get('status') == 'legacy_with_live_error':
        warnings.append('ABRA live adapter selhal, finance fallbacknuly na legacy snapshot.')
    if fetch_result.abra_vykaz_hospodareni_reports.get('source', {}).get('status') == 'error':
        warnings.append('ABRA report Výkaz hospodaření za měsíc se nepodařilo stáhnout.')
    if not fetch_result.sklik_status.get('ready') and fetch_result.sklik_status.get('reason') != 'missing_token':
        warnings.append('Sklik denní refresh selhal, marketing používá poslední dostupný snapshot.')
    if not fetch_result.meta_status.get('ready') and fetch_result.meta_status.get('reason') != 'missing_token':
        warnings.append('Meta Ads denní refresh selhal, marketing používá poslední dostupný snapshot.')
    if not fetch_result.google_status.get('ready') and fetch_result.google_status.get('reason') != 'missing_token':
        warnings.append('Google Ads denní refresh selhal, marketing používá poslední dostupný snapshot.')
    if not fetch_result.ga4_status.get('ready') and fetch_result.ga4_status.get('reason') != 'missing_token':
        warnings.append('GA4 denní refresh selhal, akviziční kontext se bere z posledního dostupného snapshotu.')
    if not fetch_result.klaviyo_status.get('ready') and fetch_result.klaviyo_status.get('reason') != 'missing_token':
        warnings.append('Ecomail denní refresh selhal, marketing používá poslední dostupný snapshot.')


def build_refresh_inventory_summary(fetch_result: RefreshFetchResult) -> dict[str, Any]:
    return {
        'availableStockTotal': round(fetch_result.cz_inventory['availableStockTotal'] + fetch_result.sk_inventory['availableStockTotal'], 2),
        'itemsTotal': len(fetch_result.cz_inventory['items']) + len(fetch_result.sk_inventory['items']),
        'byAccount': {
            'CZ': round(fetch_result.cz_inventory['availableStockTotal'], 2),
            'SK': round(fetch_result.sk_inventory['availableStockTotal'], 2),
        },
        'itemsByAccount': {
            'CZ': len(fetch_result.cz_inventory['items']),
            'SK': len(fetch_result.sk_inventory['items']),
        },
    }


def merge_ranked_count_rows(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter = Counter({})
    for rows in groups:
        counter += Counter({row['name']: row['count'] for row in rows})
    return [{'name': name, 'count': count} for name, count in counter.most_common()]


def build_refresh_logistics_summary(
    fetch_result: RefreshFetchResult,
    report_start: datetime,
    report_end: datetime,
    expiry_overview_payload: dict[str, Any],
) -> dict[str, Any]:
    cz_daily = summarize_4px_window('CZ', fetch_result.cz_outbound, report_start, report_end)
    sk_daily = summarize_4px_window('SK', fetch_result.sk_outbound, report_start, report_end)
    return {
        'shipmentsTotal': cz_daily['shipments'] + sk_daily['shipments'],
        'byAccount': {'CZ': cz_daily['shipments'], 'SK': sk_daily['shipments']},
        'carrierCounts': merge_ranked_count_rows(cz_daily['carrierCounts'], sk_daily['carrierCounts']),
        'logisticsCounts': merge_ranked_count_rows(cz_daily['logisticsCounts'], sk_daily['logisticsCounts']),
        'statusCounts': merge_ranked_count_rows(cz_daily['statusCounts'], sk_daily['statusCounts']),
        'coverageWarnings': [warning for warning in [cz_daily['coverageWarning'], sk_daily['coverageWarning']] if warning],
        'expiringProducts': (expiry_overview_payload.get('topExpiring') or [])[:5],
        'notes': [],
    }


def build_abra_report_manifest(generated_at: str, abra_vykaz_hospodareni_reports: dict[str, Any]) -> dict[str, Any]:
    return {
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


def should_write_refresh_snapshot(output: RefreshOutputSpec, build_result: RefreshBuildResult) -> bool:
    if output.snapshot_policy != 'skip_heavy':
        return True
    return not (build_result.skip_snapshot_for_heavy and output.name in build_result.heavy_payloads)


def write_refresh_output(output: RefreshOutputSpec, current_dir: Path, snapshot_path: Path, build_result: RefreshBuildResult):
    if output.writer == 'finance':
        write_finance_payloads(current_dir, output.payload)
        if should_write_refresh_snapshot(output, build_result):
            write_finance_payloads(snapshot_path, output.payload)
        return

    if output.writer == 'text':
        write_text(current_dir / output.name, output.payload)
        if should_write_refresh_snapshot(output, build_result):
            write_text(snapshot_path / output.name, output.payload)
        return

    write_json(current_dir / output.name, output.payload)
    if should_write_refresh_snapshot(output, build_result):
        write_json(snapshot_path / output.name, output.payload)


def build_refresh_output_registry(
    ctx: RefreshRuntimeContext,
    fetch_result: RefreshFetchResult,
    build_result: RefreshBuildResult,
    remote_sync_result: dict[str, Any] | None = None,
) -> list[RefreshOutputSpec]:
    outputs = [
        RefreshOutputSpec('4px_cz_inventory.json', {'generatedAt': ctx.generated_at, **fetch_result.cz_inventory}),
        RefreshOutputSpec('4px_sk_inventory.json', {'generatedAt': ctx.generated_at, **fetch_result.sk_inventory}),
        RefreshOutputSpec('4px_cz_inventory_detail.json', {'generatedAt': ctx.generated_at, **fetch_result.cz_inventory_detail}),
        RefreshOutputSpec('4px_sk_inventory_detail.json', {'generatedAt': ctx.generated_at, **fetch_result.sk_inventory_detail}),
        RefreshOutputSpec('4px_cz_outbound_recent.json', {'generatedAt': ctx.generated_at, **fetch_result.cz_outbound}),
        RefreshOutputSpec('4px_sk_outbound_recent.json', {'generatedAt': ctx.generated_at, **fetch_result.sk_outbound}),
        RefreshOutputSpec('4px_expiry_overview.json', build_result.expiry_overview_payload),
        RefreshOutputSpec('combined_product_index.json', build_result.combined_index_payload),
        RefreshOutputSpec('combined_inventory_overview.json', build_result.combined_overview_payload),
        RefreshOutputSpec('inventory_analytics_365d.json', build_result.inventory_analytics_payload),
        RefreshOutputSpec('inventory_analytics_730d.json', build_result.inventory_analytics_730_payload),
        RefreshOutputSpec('inventory_analytics_730d_cz.json', build_result.inventory_analytics_730_cz_payload),
        RefreshOutputSpec('inventory_analytics_730d_sk.json', build_result.inventory_analytics_730_sk_payload),
        RefreshOutputSpec('ordering_core.json', build_result.ordering_core_payload),
        RefreshOutputSpec('ordering_core_cz.json', build_result.ordering_core_cz_payload),
        RefreshOutputSpec('ordering_core_sk.json', build_result.ordering_core_sk_payload),
        RefreshOutputSpec('ordering_reference_data.json', build_result.ordering_reference_payload),
        RefreshOutputSpec('ordering_reference_data_cz.json', build_result.ordering_reference_cz_payload),
        RefreshOutputSpec('ordering_reference_data_sk.json', build_result.ordering_reference_sk_payload),
        RefreshOutputSpec('ordering_sales_history.json', build_result.ordering_sales_history_payload, snapshot_policy='skip_heavy'),
        RefreshOutputSpec('store_expiry_watchdog.json', build_result.store_expiry_watchdog_payload),
        RefreshOutputSpec('finance_overview.json', fetch_result.finance_snapshot, writer='finance'),
        RefreshOutputSpec('marketing_overview.json', fetch_result.marketing_snapshot),
        RefreshOutputSpec('affiliate_overview.json', fetch_result.affiliate_overview),
        RefreshOutputSpec('wpj_orders_previous_day.json', build_result.wpj_orders_payload),
        RefreshOutputSpec('wpj_products.json', build_result.wpj_products_payload),
        RefreshOutputSpec('wpj_history_8_days.json', build_result.wpj_history_payload),
        RefreshOutputSpec('eshop_ytd.json', build_result.eshop_ytd_payload),
        RefreshOutputSpec('customer_fact_ytd_window.json', build_result.customer_fact_payload, snapshot_policy='skip_heavy'),
        RefreshOutputSpec('order_fact_ytd_window.json', build_result.order_fact_payload, snapshot_policy='skip_heavy'),
        RefreshOutputSpec('morning_report_previous_day.json', build_result.report_json),
        RefreshOutputSpec('abra_vykaz_hospodareni_reports.json', build_result.report_manifest),
        RefreshOutputSpec('morning_report_previous_day.txt', build_result.report_text, writer='text'),
        RefreshOutputSpec('morning_report_previous_day_telegram.txt', build_result.report_telegram_text, writer='text'),
    ]
    if remote_sync_result is not None:
        outputs.append(RefreshOutputSpec(
            'reporting_remote_storage_status.json',
            {
                'generatedAt': ctx.generated_at,
                'heavyPayloads': sorted(build_result.heavy_payloads),
                **remote_sync_result,
            },
        ))
    return outputs


def build_refresh_payloads(ctx: RefreshRuntimeContext, fetch_result: RefreshFetchResult) -> RefreshBuildResult:
    generated_at = ctx.generated_at
    state = build_empty_refresh_state(generated_at)

    if fetch_result.wpj_ready:
        populate_refresh_wpj_state(ctx, fetch_result, state)
    else:
        state.warnings.append('WPJ část není připojená, ranní report nebude mít e-shop výkon.')
        state.store_expiry_watchdog_payload = build_store_expiry_watchdog(
            generated_at,
            ctx.now_local,
            ctx.store_expiry_input,
            wpj_products=[],
            sales_orders=[],
            manual_overrides=ctx.manual_overrides,
            pos_admin_views=ctx.pos_admin_views,
        )
        if ctx.store_expiry_input.get('rows'):
            state.store_expiry_watchdog_payload['warnings'].append('WPJ neni pripojene, store expiry watchdog proto zatim neodcita automaticke prodeje.')

    append_refresh_source_warnings(fetch_result, state.warnings)

    if not state.expiry_overview_payload.get('topExpiring'):
        state.expiry_overview_payload = build_expiry_overview(
            generated_at,
            state.combined_index_payload,
            fetch_result.cz_expiry_summary,
            fetch_result.sk_expiry_summary,
            cz_inventory_items=fetch_result.cz_inventory.get('items') or [],
            sk_inventory_items=fetch_result.sk_inventory.get('items') or [],
            sales_orders=analytics_orders or [],
            end_dt=report_end,
            pos_admin_views=ctx.pos_admin_views,
        )

    inventory_summary = build_refresh_inventory_summary(fetch_result)
    logistics_summary = build_refresh_logistics_summary(
        fetch_result,
        ctx.report_start,
        ctx.report_end,
        state.expiry_overview_payload,
    )
    state.warnings.extend(logistics_summary['coverageWarnings'])

    inventory_health_summary = build_inventory_health_summary(state.inventory_analytics_730_payload, state.ordering_core_payload)
    alerts = build_alerts(state.wpj_summary, state.stock_summary, logistics_summary, state.warnings, inventory_health_summary)
    priorities = build_priorities(state.wpj_summary, state.stock_summary, logistics_summary, inventory_health_summary)

    report_json = build_morning_report(MorningReportBuildContext(
        report_date=ctx.report_date,
        wpj_summary=state.wpj_summary,
        baseline_orders=state.baseline_orders,
        baseline_revenue=state.baseline_revenue,
        stock_summary=state.stock_summary,
        inventory_summary=inventory_summary,
        logistics_summary=logistics_summary,
        alerts=alerts,
        priorities=priorities,
        warnings=state.warnings,
        mtd_summary=state.mtd_summary,
        inventory_health=inventory_health_summary,
    ))
    report_text = format_morning_report_text(report_json)
    report_telegram_text = format_morning_report_telegram_text(report_json)

    heavy_payloads = set(SETTINGS.reporting_heavy_payloads or [
        'order_fact_ytd_window.json',
        'customer_fact_ytd_window.json',
        'ordering_sales_history.json',
    ])
    skip_snapshot_for_heavy = SETTINGS.reporting_skip_heavy_snapshot_writes
    report_manifest = build_abra_report_manifest(generated_at, fetch_result.abra_vykaz_hospodareni_reports)

    return RefreshBuildResult(
        warnings=state.warnings,
        wpj_summary=state.wpj_summary,
        wpj_orders_payload=state.wpj_orders_payload,
        wpj_products_payload=state.wpj_products_payload,
        wpj_history_payload=state.wpj_history_payload,
        eshop_ytd_payload=state.eshop_ytd_payload,
        customer_fact_payload=state.customer_fact_payload,
        order_fact_payload=state.order_fact_payload,
        inventory_analytics_payload=state.inventory_analytics_payload,
        inventory_analytics_730_payload=state.inventory_analytics_730_payload,
        inventory_analytics_730_cz_payload=state.inventory_analytics_730_cz_payload,
        inventory_analytics_730_sk_payload=state.inventory_analytics_730_sk_payload,
        ordering_core_payload=state.ordering_core_payload,
        ordering_core_cz_payload=state.ordering_core_cz_payload,
        ordering_core_sk_payload=state.ordering_core_sk_payload,
        ordering_reference_payload=state.ordering_reference_payload,
        ordering_reference_cz_payload=state.ordering_reference_cz_payload,
        ordering_reference_sk_payload=state.ordering_reference_sk_payload,
        ordering_sales_history_payload=state.ordering_sales_history_payload,
        expiry_overview_payload=state.expiry_overview_payload,
        store_expiry_watchdog_payload=state.store_expiry_watchdog_payload,
        combined_index_payload=state.combined_index_payload,
        combined_overview_payload=state.combined_overview_payload,
        baseline_orders=state.baseline_orders,
        baseline_revenue=state.baseline_revenue,
        stock_summary=state.stock_summary,
        inventory_summary=inventory_summary,
        logistics_summary=logistics_summary,
        inventory_health_summary=inventory_health_summary,
        alerts=alerts,
        priorities=priorities,
        report_json=report_json,
        report_text=report_text,
        report_telegram_text=report_telegram_text,
        heavy_payloads=heavy_payloads,
        skip_snapshot_for_heavy=skip_snapshot_for_heavy,
        report_manifest=report_manifest,
    )


def persist_refresh_outputs(
    ctx: RefreshRuntimeContext,
    fetch_result: RefreshFetchResult,
    build_result: RefreshBuildResult,
):
    CURRENT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path = SNAPSHOT_DIR / ctx.stamp
    snapshot_path.mkdir(parents=True, exist_ok=True)

    remote_sync_result = sync_remote_heavy_payloads(sorted(build_result.heavy_payloads))
    for output in build_refresh_output_registry(ctx, fetch_result, build_result, remote_sync_result=remote_sync_result):
        write_refresh_output(output, CURRENT_DIR, snapshot_path, build_result)
    for row in (fetch_result.abra_vykaz_hospodareni_reports.get('exports') or []):
        body = row.get('bytes')
        if not body:
            continue
        write_bytes(CURRENT_DIR / row['fileName'], body)
        write_bytes(snapshot_path / row['fileName'], body)

    twisto_watchdog = run_twisto_watchdog(snapshot_path, ctx.generated_at)
    portal_summary = {
        'generatedAt': ctx.generated_at,
        'config': {
            'warehouseCode': ctx.warehouse_code,
            'outboundMaxPages': ctx.max_pages,
            'reportWindow': {
                'from': ctx.report_start.isoformat(),
                'to': ctx.report_end.isoformat(),
            },
        },
        'warnings': build_result.warnings,
        'accounts': {
            'cz': account_payload('CZ', fetch_result.cz_inventory, fetch_result.cz_outbound),
            'sk': account_payload('SK', fetch_result.sk_inventory, fetch_result.sk_outbound),
        },
        'wpJ': {
            'ready': fetch_result.wpj_ready,
            'message': 'WPJ připojeno a ranní report je vygenerovaný.' if fetch_result.wpj_ready else 'WPJ zatím není připojené. Chybí token nebo URL.',
            'orders': build_result.wpj_summary['orders'],
            'revenueWithVat': build_result.wpj_summary['revenueWithVat'],
            'averageOrderValue': build_result.wpj_summary['averageOrderValue'],
            'problematicOrders': build_result.wpj_summary['problematicOrders'],
        },
        'report': {
            'date': ctx.report_date.isoformat(),
            'shipments': build_result.logistics_summary['shipmentsTotal'],
            'alerts': build_result.alerts,
            'priorities': build_result.priorities,
        },
        'expiries': build_result.expiry_overview_payload.get('summary') or {},
        'storeExpiry': build_result.store_expiry_watchdog_payload.get('summary') or {},
        'pairing': build_result.combined_overview_payload.get('counts') or {},
        'finance': {
            'ready': fetch_result.finance_snapshot.get('source', {}).get('status') != 'missing',
            'mode': fetch_result.finance_snapshot.get('source', {}).get('status'),
            'message': fetch_result.finance_snapshot.get('source', {}).get('message'),
            'currentMonth': fetch_result.finance_snapshot.get('currentMonth') or {},
            'cash': fetch_result.finance_snapshot.get('cash') or {},
            'reportExport': fetch_result.abra_vykaz_hospodareni_reports.get('source') or {},
        },
        'marketing': {
            'ready': fetch_result.marketing_snapshot.get('source', {}).get('status') != 'missing',
            'mode': fetch_result.marketing_snapshot.get('source', {}).get('status'),
            'message': fetch_result.marketing_snapshot.get('source', {}).get('message'),
            'currentMonth': fetch_result.marketing_snapshot.get('currentMonth') or {},
            'topSupplier': (fetch_result.marketing_snapshot.get('topSuppliersCurrentMonth') or [None])[0],
        },
        'affiliate': {
            'ready': bool(fetch_result.affiliate_overview),
            'mode': (fetch_result.affiliate_overview.get('source') or {}).get('status'),
            'message': (fetch_result.affiliate_overview.get('source') or {}).get('message'),
            'period': fetch_result.affiliate_overview.get('period') or {},
            'summary': fetch_result.affiliate_overview.get('summary') or {},
        },
        'ga4': {
            'ready': (fetch_result.marketing_snapshot.get('analytics') or {}).get('ready', False),
            'source': ((fetch_result.marketing_snapshot.get('analytics') or {}).get('source') or {}).get('status'),
            'message': ((fetch_result.marketing_snapshot.get('analytics') or {}).get('source') or {}).get('message'),
            'property': (fetch_result.marketing_snapshot.get('analytics') or {}).get('property') or {},
            'currentMonth': (fetch_result.marketing_snapshot.get('analytics') or {}).get('currentMonth') or {},
            'last7days': (fetch_result.marketing_snapshot.get('analytics') or {}).get('last7days') or {},
            'topChannel7d': (((fetch_result.marketing_snapshot.get('analytics') or {}).get('channelPerformance7d') or [None])[0]),
            'countries7d': ((fetch_result.marketing_snapshot.get('analytics') or {}).get('countries7d') or [])[:5],
        },
        'twistoWatchdog': {
            'ready': (twisto_watchdog.get('alert') or {}).get('status') != 'missing',
            'status': (twisto_watchdog.get('alert') or {}).get('status'),
            'summary': twisto_watchdog.get('summary') or {},
            'source': twisto_watchdog.get('source') or {},
            'message': ((twisto_watchdog.get('alert') or {}).get('lines') or [None])[0],
        },
    }
    write_refresh_output(
        RefreshOutputSpec('portal_summary.json', portal_summary),
        CURRENT_DIR,
        snapshot_path,
        build_result,
    )

    latest_snapshot = SNAPSHOT_DIR / 'latest'
    if latest_snapshot.exists() or latest_snapshot.is_symlink():
        latest_snapshot.unlink()
    latest_snapshot.symlink_to(snapshot_path.name)

    keep_snapshots = SETTINGS.reporting_snapshot_keep
    try:
        keep_snapshots_int = max(int(keep_snapshots), 1)
    except ValueError:
        keep_snapshots_int = 3
    snapshot_dirs = sorted([path for path in SNAPSHOT_DIR.iterdir() if path.is_dir() and path.name != 'latest'], key=lambda p: p.name)
    for old_path in snapshot_dirs[:-keep_snapshots_int]:
        shutil.rmtree(old_path)


def print_refresh_summary(ctx: RefreshRuntimeContext, fetch_result: RefreshFetchResult, build_result: RefreshBuildResult):
    print(f'Refreshed reporting data at {ctx.generated_at}')
    print(f'CZ inventory rows: {len(fetch_result.cz_inventory["items"])} | CZ outbound rows: {len(fetch_result.cz_outbound["items"])}')
    print(f'SK inventory rows: {len(fetch_result.sk_inventory["items"])} | SK outbound rows: {len(fetch_result.sk_outbound["items"])}')
    print(f'WPJ previous-day orders: {build_result.wpj_summary["orders"]} | Revenue with VAT: {build_result.wpj_summary["revenueWithVat"]}')
    print(f'Store expiry visible rows: {build_result.store_expiry_watchdog_payload.get("summary", {}).get("visibleRows", 0)}')
    print(f'Morning report file: {CURRENT_DIR / "morning_report_previous_day.txt"}')


def main():
    ctx = build_refresh_runtime_context()
    fetch_result = fetch_refresh_inputs(ctx)
    build_result = build_refresh_payloads(ctx, fetch_result)
    persist_refresh_outputs(ctx, fetch_result, build_result)
    print_refresh_summary(ctx, fetch_result, build_result)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise
