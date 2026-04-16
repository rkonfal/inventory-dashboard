# Reporting V2

New reporting rebuild for Diamond Plus / Království TianDe.

## Structure
- `site/` static portal + dashboards
- `data/current/` latest normalized JSON payloads used by the site
- `data/snapshots/` timestamped refresh snapshots
- `scripts/` fetch + normalize + refresh scripts
- `ops/` cron and operational helpers

## Current state
- 4PX direct ingestion is wired first
- WPJ placeholders are prepared but still need a live token / current flow trace
- site visuals intentionally reuse the current Diamond Plus visual language, but with shared assets and cleaner structure

## Run locally
```bash
cd reporting-v2
python3 scripts/refresh_data.py
python3 -m http.server 8080
```

Open:
- `http://localhost:8080/site/`
- `http://localhost:8080/site/inventory.html`
- `http://localhost:8080/site/logistics.html`
- `http://localhost:8080/site/eshop.html`

## Hourly refresh
Primary automation assets:
- `ops/hourly-refresh.cron` for classic cron install
- `ops/ai.rudanek.reporting-v2.hourly.plist` for macOS LaunchAgent scheduling

Current host state:
- cron file is prepared
- macOS LaunchAgent fallback was installed and loaded so hourly refresh can run even while `crontab` is being uncooperative in the current exec context
