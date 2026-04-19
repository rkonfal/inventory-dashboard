# Marketing API integration plan

## Current state in reporting-v2

- `site/marketing.html` now renders live Meta Ads and Sklik highlights inside the existing marketing dashboard.
- `scripts/refresh_data.py` currently builds marketing from accounting accounts `518900` (PPC kredit) and `518901` (Reklama a marketing).
- `scripts/fetch_meta_ads.py` now pulls live Meta Ads spend, clicks, conversions and purchase value for configured ad accounts.
- `data/current/marketing_overview.json` is still accounting-led for monthly reconciliation, but now includes direct live platform blocks for Sklik and Meta Ads.

## What the local audit found

### Channels visible from current local data

From the latest finance/journal data currently available in the workspace:

- **Meta Platforms Ireland Limited** is clearly present in the last months.
- **ProfiSMS s.r.o.** is clearly present, but that is SMS, not ad-platform spend.
- No clear **Google Ads**, **Sklik**, **Heureka**, or **GLAMI** vendor strings are visible in the top current journal rows, so those may be:
  - booked under another supplier name,
  - paid from another entity/account,
  - netted/aggregated differently in ABRA,
  - or simply absent in the last currently loaded sample.

### Existing integration environment already present

The local reporting setup already has working server-side integrations for:

- 4PX
- WPJ GraphQL
- ABRA

Ad-platform API environment is now present locally for:

- Meta Ads
- Sklik

## Target architecture

### 1. Source of truth by layer

- **Ad platforms become the primary source** for daily spend and campaign metrics.
- **ABRA remains the control layer** for monthly reconciliation and bookkeeping completeness.

### 2. First-wave direct integrations

#### Meta Ads
Implemented now:
- Business Manager system-user token
- configured ad account list via `META_AD_ACCOUNT_IDS`
- current-month account summary, daily summary and top campaigns

Current output:
- `data/current/meta_ads_overview.json`
- merged Meta block under `data/current/marketing_overview.json -> directSources.meta`
- marketing dashboard cards showing live Meta spend, clicks and ROAS

#### Google Ads
Use:
- manager account (MCC)
- Google Ads API developer token
- service account or OAuth app authorized against the manager hierarchy

Pull:
- all linked child customer accounts
- daily spend, clicks, impressions, conversions
- campaign detail as secondary drilldown

#### Sklik
Already implemented:
- daily fetch to `data/current/sklik_overview.json`
- merged under `data/current/marketing_overview.json -> directSources.sklik`

### 3. Normalized output schema

Each raw fetch should normalize into a shared record shape:

- `date`
- `platform`
- `accountId`
- `accountName`
- `currency`
- `spend`
- `impressions`
- `clicks`
- `conversions`
- `conversionValue`
- `campaignId`
- `campaignName`
- `rawSource`

Then aggregate into dashboard-ready JSONs:

- `data/current/marketing_accounts_daily.json`
- `data/current/marketing_campaigns_daily.json`
- `data/current/marketing_platform_summary.json`
- `data/current/marketing_reconciliation.json`

## How I will do it

### Phase A, audit and access validation

1. Confirm all real paid channels from accounting and any existing browser/account access.
2. Confirm whether ad accounts sit under one admin structure or are split across multiple businesses/managers.
3. Store credentials only in local secret config, never in tracked files.

### Phase B, collectors

1. Add one fetcher per platform.
2. Fetch account lists first, metrics second.
3. Cache raw responses into timestamped snapshots for debugging.
4. Normalize to one shared internal schema.

### Phase C, reporting integration

1. Extend `refresh_data.py` to load normalized ad-platform data.
2. Keep ABRA totals for month-level comparison and discrepancy warnings.
3. Surface per-platform and per-account spend on the marketing dashboard.
4. Add Google Ads next into the same pattern as Meta and Sklik.

### Phase D, controls

1. Alert on missing tokens, revoked access, empty account lists, or sudden zero spend.
2. Add reconciliation status: platform totals vs ABRA totals.
3. Fail soft, so the dashboard still loads even if one platform is temporarily down.

## What I can do without Ruda

I can do without ongoing help:
- audit the current reporting code
- prepare the integration structure
- implement the fetch + normalize pipeline
- wire the dashboard to the new JSON outputs
- reconcile against ABRA

## What may still require a one-time human action

Only if access is missing:
- accepting an invite to a Meta business or ad account
- granting a Google Ads manager or service account access
- generating a Sklik API token from the controlling Seznam account
- completing a one-time 2FA or admin approval flow

## Immediate next steps

1. Stabilize the full `refresh_data.py` run so the Meta merge is always written by the main refresh, not just by the targeted collector step.
2. Add Google Ads collector into the same `directSources` structure.
3. Add reconciliation between platform totals and ABRA monthly totals.
4. Surface top Meta campaigns directly on the marketing page.
