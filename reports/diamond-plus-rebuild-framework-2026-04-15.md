# Diamond Plus / TianDe reporting rebuild framework — 2026-04-15

## Why this document exists
This is the new shared starting point.

We already know enough to stop guessing and start rebuilding the reporting stack deliberately.
The goal is not to patch one dashboard, but to create a reporting flow that is understandable, reproducible, and robust.

## What is true today

### 1. The public portal is not the system
The public Diamond Plus portal is only a front door.
Behind it sits a set of separate GitHub Pages repositories with rendered HTML dashboards.

### 2. Most repos contain outputs, not generation logic
The linked dashboard repositories mostly contain static HTML snapshots.
The real generation logic is not committed there.

### 3. Part of the current refresh path likely runs through Claude
Rudolf confirmed that the current update flow is tied to a Claude dispatch bot.
That matches the repo evidence and explains the missing generators.

### 4. 4PX is now a verified direct source
Live 4PX Open API access was verified for both TIANDE_SK and TIANDE_CZ.
Confirmed working endpoints:
- `com.basis.warehouse.getlist`
- `fu.wms.inventory.get`
- `fu.wms.outbound.getlist`

This means 4PX should not stay a black box.
It can be pulled directly and deterministically.

### 5. WPJ remains the main unknown
There is a strong technical lead that WPJ data has been accessed through:
- WPJShop GraphQL: `https://www.kralovstvi-tiande.cz/admin/graphql/`
- Cloudflare Worker proxy: `https://tiande-proxy.rkonfal-rk.workers.dev`
- auth via `X-Access-Token`

But the currently used refresh pipeline still needs to be traced.

## Rebuild objective
Create a reporting stack that is:
- reproducible,
- inspectable,
- easy to refresh,
- easy to debug,
- not dependent on hidden manual steps,
- able to generate both dashboards and the 08:00 morning report from the same source data.

## Design principles

### Principle 1. Separate data extraction from presentation
Do not treat HTML dashboards as source of truth.
Source of truth should be structured datasets produced from APIs or known exports.

### Principle 2. Prefer deterministic pipelines over opaque agent behavior
Where data can be pulled directly, do it with scripts.
Use an agent only where summarization, commentary, or exception handling adds value.

### Principle 3. One dataset, many outputs
The same refreshed data should feed:
- portal dashboards,
- daily 08:00 report,
- future alerts or operational checks.

### Principle 4. Preserve continuity before replacing live flows
Do not break the current dashboard publishing path before we understand it.
Short term, map it. Medium term, simplify or replace it.

## Recommended target architecture

### Layer A. Data ingestion
Independent pull scripts for each source:
- `4px/` for shipments, inventory, warehouse metadata
- `wpj/` for orders, revenue, products, cancellations, payment and shipping methods
- optional `abra/` if finance pages stay in scope

### Layer B. Normalized datasets
Refresh into stable local machine-readable files, for example:
- `data/raw/...`
- `data/normalized/...`
- `data/snapshots/YYYY-MM-DD/...`

This gives us auditability and rollback.

### Layer C. Renderers
Separate rendering scripts that transform normalized data into:
- dashboard HTML files
- report payloads / message text

### Layer D. Scheduling
Use cron for predictable refresh windows, for example:
- overnight or early-morning data refresh
- 08:00 delivery of the previous-day report

### Layer E. Optional agent layer
Use an agent for:
- natural-language summary generation
- anomaly commentary
- escalation when source data is missing or suspicious

Do not use the agent as the only place where raw operational truth exists.

## Migration strategy

### Phase 1. Map current reality
Find and inspect:
- Claude dispatch bot project or runtime
- current prompts, scripts, or tool chain
- where HTML gets generated
- how GitHub pushes happen today
- where WPJ credentials / proxy routing are handled

Goal: understand the current system without breaking it.

### Phase 2. Rebuild direct 4PX pipeline
Because 4PX is already verified, this is the safest first extraction.

Deliverables:
- direct 4PX fetch script(s)
- previous-day parcel dataset
- current inventory dataset
- carrier split logic
- packaging-consumption calculation assumptions captured explicitly

Goal: make 4PX reproducible without Claude dependency.

### Phase 3. Recover or rebuild WPJ pipeline
Two possible paths:

#### Path A. Recover existing WPJ refresh logic
If the Claude dispatch bot already has a stable WPJ extraction path, reuse it short term.

#### Path B. Rebuild WPJ extraction directly
If current Claude logic is too opaque, rebuild a direct WPJ GraphQL pull.

Goal: produce stable previous-day commercial data for the morning report.

### Phase 4. Merge into one reporting model
Create one canonical previous-day dataset that includes:
- e-shop orders and revenue
- top products
- cancellations / anomalies
- outbound parcels by country / account / carrier
- stock alerts
- packaging consumption

### Phase 5. Replace fragile publishing steps
Once data + rendering are reproducible:
- regenerate dashboards locally or via controlled automation
- publish to GitHub Pages repos in a known way
- reduce reliance on hidden manual/Claude-only flows

## Recommended immediate work order
1. Locate the Claude dispatch bot and document the current flow.
2. Extract 4PX into direct scripts and local datasets.
3. Confirm WPJ data path, either current or rebuilt.
4. Build the previous-day dataset model.
5. Generate the 08:00 morning report from that dataset.
6. Only then refactor portal/dashboard publishing.

## What we should not do
- Do not edit dashboard HTML by hand as a long-term method.
- Do not make the morning report depend on reading public pages.
- Do not keep critical business metrics inside an opaque agent-only workflow.
- Do not replace the current system blindly before tracing it.

## Definition of done
This rebuild is successful when:
- we can refresh the required data on demand,
- we know exactly where each number comes from,
- a failed source produces a visible warning instead of silent bad data,
- the 08:00 report can be generated reliably for the previous day,
- dashboard publishing is reproducible without guesswork.

## Immediate next missing input
We need the location or shape of the current Claude dispatch bot flow:
- repo,
- local folder,
- prompt,
- workflow export,
- or any other trace of where it runs.

Once we have that, we can decide which parts to preserve and which to replace.
