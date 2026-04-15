#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import sys
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE_URL = os.environ.get('FOURPX_BASE_URL', 'https://open.eu.4px.com/router/api/service')
APP_KEY = os.environ.get('FOURPX_APP_KEY', '')
APP_SECRET = os.environ.get('FOURPX_APP_SECRET', '')
ACCESS_TOKEN = os.environ.get('FOURPX_ACCESS_TOKEN', '')
LANGUAGE = os.environ.get('FOURPX_LANGUAGE', 'en')
VERSION = os.environ.get('FOURPX_VERSION', '1.0')


def compact_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def build_sign(params, body_json, app_secret):
    parts = []
    for key in sorted(k for k in params.keys() if k not in {'sign', 'access_token', 'language'}):
        parts.append(f"{key}{params[key]}")
    raw = ''.join(parts) + body_json + app_secret
    return hashlib.md5(raw.encode('utf-8')).hexdigest()


def call_api(method, body):
    if not APP_KEY or not APP_SECRET:
        raise SystemExit('Missing FOURPX_APP_KEY / FOURPX_APP_SECRET environment variables')

    body_json = compact_json(body)
    params = {
        'method': method,
        'app_key': APP_KEY,
        'v': VERSION,
        'timestamp': str(int(time.time() * 1000)),
        'format': 'json',
    }
    sign = build_sign(params, body_json, APP_SECRET)
    params['sign'] = sign
    if ACCESS_TOKEN:
        params['access_token'] = ACCESS_TOKEN
    if LANGUAGE:
        params['language'] = LANGUAGE

    url = f"{BASE_URL}?{urlencode(params)}"
    req = Request(url, data=body_json.encode('utf-8'), headers={'Content-Type': 'application/json', 'User-Agent': 'openclaw-fourpx-probe/1.0'})
    with urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode('utf-8', 'ignore'))


def main():
    parser = argparse.ArgumentParser(description='Call 4PX Open API with env-provided credentials')
    parser.add_argument('method', help='4PX method, e.g. com.basis.warehouse.getlist')
    parser.add_argument('--body', default='{}', help='JSON body string')
    args = parser.parse_args()

    try:
        body = json.loads(args.body)
    except json.JSONDecodeError as e:
        raise SystemExit(f'Invalid --body JSON: {e}')

    result = call_api(args.method, body)
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
