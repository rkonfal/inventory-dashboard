#!/usr/bin/env python3
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path('/Users/rudolfkonfal/.openclaw/workspace/reporting-v2')
WORKTREE = Path(os.environ.get('PREVIEW_WORKTREE', '/Users/rudolfkonfal/.openclaw/workspace/tmp/diamond-plus-reporting-preview'))
REPO = os.environ.get('PREVIEW_REPO', 'rkonfal/diamond-plus-reporting-preview')
BRANCH = os.environ.get('PREVIEW_BRANCH', 'main')
SITE_DIR = ROOT / 'site'
CURRENT_DIR = ROOT / 'data' / 'current'
PREVIEWS_DIR = ROOT / 'previews'
PREVIEW_FILES = [
    'dashboard-portal-preview-clean-light.png',
    'dashboard-eshop-preview-clean-light.png',
]
PREVIEW_CURRENT_ALLOWLIST = [
    '4px_expiry_overview.json',
    'affiliate_overview.json',
    'combined_inventory_overview.json',
    'combined_product_index.json',
    'eshop_ytd.json',
    'finance_overview.json',
    'ga4_overview.json',
    'google_ads_overview.json',
    'inventory_analytics_365d.json',
    'inventory_analytics_730d.json',
    'inventory_analytics_730d_cz.json',
    'inventory_analytics_730d_sk.json',
    'klaviyo_overview.json',
    'marketing_overview.json',
    'meta_ads_overview.json',
    'morning_report_delivery_status.json',
    'morning_report_prepare_status.json',
    'morning_report_previous_day.json',
    'ordering_actions.json',
    'ordering_core.json',
    'ordering_core_cz.json',
    'ordering_core_sk.json',
    'ordering_reference_data.json',
    'ordering_reference_data_cz.json',
    'ordering_reference_data_sk.json',
    'ordering_sales_history.json',
    'packaging_consumption.json',
    'portal_summary.json',
    'sklik_overview.json',
    'top_50_customers_last_year.json',
    'twisto_watchdog.json',
    'wpj_history_8_days.json',
    'wpj_orders_previous_day.json',
    'wpj_products.json',
]
PREVIEW_WARN_MB = float(os.environ.get('PREVIEW_WARN_MB', '25'))
PREVIEW_HARD_LIMIT_MB = float(os.environ.get('PREVIEW_HARD_LIMIT_MB', '50'))


def run(*args, cwd=None, env=None):
    print('+', ' '.join(args))
    subprocess.run(args, cwd=cwd, check=True, env=env)


def ensure_clone():
    if (WORKTREE / '.git').exists():
        run('git', 'fetch', 'origin', cwd=str(WORKTREE))
        run('git', 'checkout', BRANCH, cwd=str(WORKTREE))
        run('git', 'reset', '--hard', f'origin/{BRANCH}', cwd=str(WORKTREE))
        return
    if WORKTREE.exists():
        shutil.rmtree(WORKTREE)
    WORKTREE.parent.mkdir(parents=True, exist_ok=True)
    try:
        run('gh', 'repo', 'clone', REPO, str(WORKTREE))
    except Exception:
        run('git', 'clone', f'https://github.com/{REPO}.git', str(WORKTREE))
        run('git', 'checkout', BRANCH, cwd=str(WORKTREE))


def clear_worktree():
    for child in WORKTREE.iterdir():
        if child.name == '.git':
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def build_dynamic_pages():
    target_html = WORKTREE / 'site' / 'order-bump.html'
    target_html.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='order-bump-preview-') as temp_dir:
        env = os.environ.copy()
        env.update({
            'ORDER_BUMP_TARGET_HTML': str(target_html),
            'ORDER_BUMP_TARGET_JSON': str(Path(temp_dir) / 'order_bump_report.json'),
        })
        run('python3', 'scripts/build_order_bump_report.py', cwd=str(ROOT), env=env)


def copy_preview_current_files():
    target_current = WORKTREE / 'data' / 'current'
    target_current.mkdir(parents=True, exist_ok=True)
    copied = []
    warnings = []
    for name in PREVIEW_CURRENT_ALLOWLIST:
        src = CURRENT_DIR / name
        if not src.exists():
            continue
        size_mb = src.stat().st_size / (1024 ** 2)
        if size_mb > PREVIEW_HARD_LIMIT_MB:
            raise RuntimeError(f'Preview export hard stop: {name} is {size_mb:.2f} MB, limit is {PREVIEW_HARD_LIMIT_MB:.2f} MB')
        if size_mb > PREVIEW_WARN_MB:
            warnings.append(f'{name} is {size_mb:.2f} MB')
        shutil.copy2(src, target_current / name)
        copied.append((name, round(size_mb, 2)))
    print(f'Preview current allowlist copied: {len(copied)} files')
    for name, size_mb in copied:
        print(f'  - {name} ({size_mb} MB)')
    for warning in warnings:
        print(f'WARN: preview export large file: {warning}')


def export_preview():
    clear_worktree()
    shutil.copytree(SITE_DIR, WORKTREE / 'site')
    copy_preview_current_files()
    build_dynamic_pages()

    audit_page = SITE_DIR / 'ads-audit-2026-04.html'
    if audit_page.exists():
        shutil.copy2(audit_page, WORKTREE / 'ads-audit-2026-04.html')

    target_previews = WORKTREE / 'previews'
    target_previews.mkdir(parents=True, exist_ok=True)
    for name in PREVIEW_FILES:
        src = PREVIEWS_DIR / name
        if src.exists():
            shutil.copy2(src, target_previews / name)

    (WORKTREE / '.nojekyll').write_text('', encoding='utf-8')
    (WORKTREE / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=site/index.html"><title>Diamond Plus Reporting Preview</title><p>Redirecting to <a href="site/index.html">site preview</a>…</p>',
        encoding='utf-8',
    )

    for page in SITE_DIR.glob('*.html'):
        if page.name == 'index.html':
            continue
        (WORKTREE / page.name).write_text(
            f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0; url=site/{page.name}"><title>Diamond Plus Reporting Preview</title><p>Redirecting to <a href="site/{page.name}">{page.name}</a>…</p>',
            encoding='utf-8',
        )

    (WORKTREE / 'README.md').write_text(
        '# Diamond Plus Reporting Preview\n\n'
        'Static preview export of reporting-v2.\n\n'
        '- Main preview: `site/index.html`\n'
        '- Root page shortcuts: `/*.html` redirect to matching `site/*.html`\n'
        '- Current preview data: allowlist only under `data/current/`\n'
        '- Preview boards: `previews/`\n',
        encoding='utf-8',
    )


def commit_and_push():
    run('git', 'add', '-A', cwd=str(WORKTREE))
    diff = subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=str(WORKTREE))
    if diff.returncode == 0:
        print('No preview changes to publish.')
        return
    stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    run('git', 'commit', '-m', f'Publish preview {stamp}', cwd=str(WORKTREE))
    run('git', 'push', 'origin', BRANCH, cwd=str(WORKTREE))


def main():
    ensure_clone()
    export_preview()
    commit_and_push()
    print(f'Preview repo updated: https://github.com/{REPO}')
    owner, repo = REPO.split('/', 1)
    print(f'Preview site: https://{owner}.github.io/{repo}/')


if __name__ == '__main__':
    main()
