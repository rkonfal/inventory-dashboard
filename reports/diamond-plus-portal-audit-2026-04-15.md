# Diamond Plus portal audit — 2026-04-15

## What I inspected
Live portal:
- https://rkonfal.github.io/diamond-plus-portal/

Cloned repos:
- diamond-plus-portal
- diamond-plus-report
- 4px-obalovy-material
- 4px-kpi-dashboard
- 4px-carriers
- eshop-dashboard
- inventory-dashboard
- balicky-dashboard
- expirace-dashboard

## High-level finding
The portal is currently a set of separate GitHub Pages repos, each mostly as a static HTML snapshot with hardcoded data.

I did not find generation scripts, workflows, or shared data files inside these repos.
That means the real update logic lives somewhere else, or some dashboards are still being refreshed manually.

## Repo-by-repo status

### 1. diamond-plus-portal
- Role: landing portal linking to all dashboards
- Current state: static HTML summary cards + links
- Last commit: 2026-04-14
- Notes: portal numbers and summary text are hardcoded
- Automation status: not self-updating in repo

### 2. diamond-plus-report
- Role: finance overview
- Source domain mentioned in page: ABRA Flexi
- Current state: static HTML with hardcoded financial numbers and commentary
- Last commit: 2026-04-14
- Automation status: no generator present in repo

### 3. 4px-obalovy-material
- Role: packaging material consumption dashboard
- Current state: static HTML with embedded weekly numbers
- Last commit: 2026-04-14
- Notes: updated with W15 data, but no update script is committed
- Automation status: currently looks snapshot-based

### 4. 4px-kpi-dashboard
- Role: 4PX shipping speed / KPI dashboard
- Current state: static HTML output, but commit history strongly suggests an external auto-update flow
- Last commit: 2026-04-15
- Example commit pattern: "Auto-update: Expedice April 2026 from 4PX API"
- Automation status: likely already fed by an external script or agent, but that script is not in repo

### 5. 4px-carriers
- Role: transport cost dashboard by carrier
- Current state: static HTML with embedded monthly arrays
- Last commit: 2026-04-14
- Automation status: snapshot-based in repo

### 6. eshop-dashboard
- Role: WPJ Shop sales dashboard
- Current state: static HTML snapshot of KPIs, daily totals, top products
- Last commit: 2026-04-15
- Notes: commit history suggests periodic refreshes, but no actual fetch/generation code is stored in repo
- Automation status: refresh exists somewhere outside repo, or updates are manual

### 7. inventory-dashboard
- Role: stock / inventory overview
- Current state: static HTML snapshot, but with strong signs of recent data refresh from 4PX and ABRA comparison work
- Last commit: 2026-04-15
- Example commit pattern: "Full 4PX API stock update"
- Automation status: likely partially externalized already, but generator missing from repo

### 8. balicky-dashboard
- Role: bundle sales dashboard
- Current state: static HTML snapshot
- Last commit: 2026-04-15
- Automation status: looks manual or externally generated, not reproducible from repo alone

### 9. expirace-dashboard
- Role: expiry-risk dashboard
- Current state: static HTML snapshot
- Last commit: 2026-04-14
- Example commit pattern: "Auto-update: Expirace data from 4PX API"
- Automation status: likely fed by an external update script, not stored in repo

## What this means for nightly updates
To make the portal reliably refresh every night, we need one of these two realities to be true:

### Preferred path
We locate the real generator scripts that already produce these HTML dashboards, then wire them to a nightly schedule.

### Fallback path
We rebuild the missing generation pipeline ourselves in this workspace:
- fetch WPJ data
- fetch 4PX data
- transform data into dashboard HTML updates
- commit + push the affected repos nightly
- then generate the 8:00 morning report from the same refreshed data

## Important technical conclusion
The portal itself is not the hard part.
The real job is the hidden data-refresh pipeline behind the linked repos.

## Immediate next best move
Find where the current auto-update scripts live, if they already exist on your machine.
Likely candidates:
- local project folders outside these repos
- Claude/Codex outputs directories
- cron/launchd jobs on the Mac
- shell, Python, or JS scripts that rewrite index.html and push to GitHub

## Recommendation
Do not start by editing the portal homepage only.
First secure the generation path for:
1. eshop-dashboard
2. inventory-dashboard
3. 4px-kpi-dashboard
4. expirace-dashboard

Once those are reproducible, the portal homepage and the 8:00 report become easy and stable.
