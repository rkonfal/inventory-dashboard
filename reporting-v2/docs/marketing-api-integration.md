# Marketing API integration plan

## Current state in reporting-v2

- `site/marketing.html` still says direct source integrations are pending for Meta, Google and other channels.
- `scripts/refresh_data.py` currently builds marketing from accounting accounts `518900` (PPC kredit) and `518901` (Reklama a marketing).
- `data/current/marketing_overview.json` is therefore still an accounting-led view, not a live ad-platform view.

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

No ad-platform API environment variables were found in the workspace config yet.

## Target architecture

### 1. Source of truth by layer

- **Ad platforms become the primary source** for daily spend and campaign metrics.
- **ABRA remains the control layer** for monthly reconciliation and bookkeeping completeness.

### 2. First-wave direct integrations

#### Meta Ads
Use:
- Business Manager
- System User
- app with Marketing API access
- long-lived or permanent system-user token

Pull:
- ad accounts under the business
- daily spend
- campaign / ad set / campaign metadata as optional detail
- conversions only where attribution setup is trustworthy

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
Use:
- Sklik API token from the central Seznam/Sklik account that manages the clients

Pull:
- client accounts
- campaigns
- daily spend and key performance metrics

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
2. Update `marketing_overview.json` so direct platform data becomes the default source.
3. Keep ABRA totals for month-level comparison and discrepancy warnings.
4. Surface per-platform and per-account spend on the marketing dashboard and homepage summary.

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

1. Map the real account landscape, Meta first.
2. Check whether existing browser/session access is available locally.
3. Add non-secret env documentation and platform config placeholders.
4. Start the Meta collector first, then Google Ads, then Sklik.
