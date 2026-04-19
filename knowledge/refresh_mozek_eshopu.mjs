#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const workspace = '/Users/rudolfkonfal/.openclaw/workspace';
const htmlPath = path.join(workspace, 'reporting-v2/site/index.html');
const summaryPath = path.join(workspace, 'reporting-v2/data/current/portal_summary.json');
const reportPath = path.join(workspace, 'reporting-v2/data/current/morning_report_previous_day.json');
const marketingPath = path.join(workspace, 'reporting-v2/data/current/marketing_overview.json');
const klaviyoPath = path.join(workspace, 'reporting-v2/data/current/klaviyo_overview.json');
const outputPath = path.join(workspace, 'knowledge/mozek_eshopu.json');
const flatOutputPath = path.join(workspace, 'knowledge/mozek_eshopu_flat.json');

const html = fs.readFileSync(htmlPath, 'utf8');
const summary = JSON.parse(fs.readFileSync(summaryPath, 'utf8'));
const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
const marketing = JSON.parse(fs.readFileSync(marketingPath, 'utf8'));
const klaviyo = JSON.parse(fs.readFileSync(klaviyoPath, 'utf8'));

const sourceFingerprint = crypto
  .createHash('sha256')
  .update(html)
  .update(JSON.stringify(summary))
  .update(JSON.stringify(report))
  .update(JSON.stringify(marketing))
  .update(JSON.stringify(klaviyo))
  .digest('hex');

const fmtNumber = value => new Intl.NumberFormat('cs-CZ').format(Number(value || 0));
const fmtMoney = value => `${fmtNumber(Math.round(value || 0))} Kč`;
const pctText = value => {
  if (value == null) return 'bez srovnání';
  const sign = value > 0 ? '+' : '';
  return `${sign}${Number(value).toFixed(1)} % vs 7denní průměr`;
};

const total4pxStock = (summary.accounts.cz.inventory.availableStockTotal || 0) + (summary.accounts.sk.inventory.availableStockTotal || 0);
const total4pxRows = (summary.accounts.cz.inventory.items || 0) + (summary.accounts.sk.inventory.items || 0);
const totalLowStockRows = (summary.accounts.cz.inventory.lowStockItems || 0) + (summary.accounts.sk.inventory.lowStockItems || 0);
const alerts = report.quickSummary?.alerts || [];

const brain = {
  source: {
    pageUrl: 'http://127.0.0.1:8765/site/index.html',
    htmlPath,
    dataFiles: {
      portalSummary: summaryPath,
      morningReportPreviousDay: reportPath,
      marketingOverview: marketingPath,
      klaviyoOverview: klaviyoPath
    },
    capturedAt: new Date().toISOString(),
    sourceFingerprint
  },
  pageMeta: {
    title: 'Diamond Plus Reporting V2',
    language: 'cs',
    htmlLength: html.length
  },
  staticVisibleContent: {
    sidebar: {
      logo: 'Diamond Plus',
      subtitle: 'Reporting V2, čistší a prouživatelsky vedený portál',
      nav: ['Přehled', 'Finance / ABRA', 'Marketing', 'Sklad', 'Logistika 4PX', 'Expirace / promo', 'E-shop / WPJ'],
      footer: 'V2 směr: méně šumu, rychlá orientace a jasné priority.'
    },
    header: {
      kicker: 'Hlavní přehled • schválený směr',
      title: 'Reporting portál, který se čte během minuty',
      subtitle: 'První obrazovka má nově ukázat, co se stalo, co je potřeba řešit a kam má člověk kliknout dál. Až pod tím zůstává technický monitoring zdrojů a detailnější tabulky.',
      actions: ['Otevřít ranní report', 'Přejít do skladu', 'Přejít do e-shopu'],
      dateNote: 'objednávky a ranní report jsou za včerejšek, 4PX sklad je aktuální stav po posledním refreshi a finance s marketingem níž ukazují poslední dostupný měsíc'
    },
    explanatoryBlocks: {
      howToReadTitle: 'Jak tu stránku číst',
      howToReadText: 'Dva krátké bloky. První řekne, co sledovat hned, druhý kdy je potřeba jít do detailu.',
      watchNow: 'Tohle je ranní rozcestník. Během minuty má ukázat, co se včera stalo, co dnes hoří a kam kliknout dál. Detail necháváme až na další stránky.',
      meaning: 'Když je první obrazovka klidná, firma pravděpodobně běží normálně. Když ne, hned je vidět, jestli problém míří do e-shopu, skladu, logistiky, marketingu nebo financí.'
    },
    doneItems: [
      { title: '4PX API napojení', desc: 'CZ i SK připojené napřímo přes Open API.' },
      { title: 'WPJ GraphQL napojení', desc: 'Objednávky, revenue, průměry a problémové objednávky už tečou do JSON.' },
      { title: 'Nová datová sada', desc: 'Refresh skript zapisuje normalizovaná JSON data, snapshoty a ranní report.' },
      { title: 'Nový portál', desc: 'Sdílené assets a společný vizuální systém bez kopírování stylů mezi HTML.' },
      { title: 'Automatické reporty', desc: 'Ranní report se generuje a odesílá automaticky každý den v 8:00.' }
    ]
  },
  rendered: {
    heroMetrics: [
      {
        label: 'Objednávky včera',
        value: fmtNumber(report.eshop.orders || 0),
        sub: `${pctText(report.quickSummary?.orders?.deltaPct)} · celý WPJShop · AOV ${fmtMoney(report.eshop.averageOrderValue || 0)}`,
        raw: {
          orders: report.eshop.orders || 0,
          deltaPctVs7d: report.quickSummary?.orders?.deltaPct ?? null,
          averageOrderValue: Math.round(report.eshop.averageOrderValue || 0)
        }
      },
      {
        label: 'Tržby s DPH',
        value: fmtMoney(report.eshop.revenueWithVat || 0),
        sub: `${pctText(report.quickSummary?.revenueWithVat?.deltaPct)} · celý WPJShop www.kralovstvi-tiande`,
        raw: {
          revenueWithVat: Math.round(report.eshop.revenueWithVat || 0),
          deltaPctVs7d: report.quickSummary?.revenueWithVat?.deltaPct ?? null
        }
      },
      {
        label: 'CZ + SK dohromady',
        value: fmtNumber(Math.round(total4pxStock || 0)),
        sub: `4PX sklad celkem · CZ ${fmtNumber(summary.accounts.cz.inventory.availableStockTotal || 0)} · SK ${fmtNumber(summary.accounts.sk.inventory.availableStockTotal || 0)}`,
        raw: {
          totalStock: Math.round(total4pxStock || 0),
          czStock: summary.accounts.cz.inventory.availableStockTotal || 0,
          skStock: summary.accounts.sk.inventory.availableStockTotal || 0
        }
      }
    ],
    attention: {
      badge: alerts.length ? `${alerts.length} věcí k pozornosti` : 'Nic zásadního',
      alerts,
      priorities: report.priorities || []
    },
    reportMini: [
      { label: 'Objednávky WPJShop', display: fmtNumber(report.eshop.orders || 0), raw: report.eshop.orders || 0 },
      { label: 'Tržby WPJShop', display: fmtMoney(report.eshop.revenueWithVat || 0), raw: Math.round(report.eshop.revenueWithVat || 0) },
      { label: '4PX sklad CZ+SK', display: fmtNumber(Math.round(report.inventory?.availableStockTotal || total4pxStock || 0)), raw: Math.round(report.inventory?.availableStockTotal || total4pxStock || 0) },
      { label: 'Expedice CZ+SK', display: `${fmtNumber(report.logistics.shipmentsTotal || 0)} (${report.logistics.byAccount?.CZ || 0}/${report.logistics.byAccount?.SK || 0})`, raw: { total: report.logistics.shipmentsTotal || 0, CZ: report.logistics.byAccount?.CZ || 0, SK: report.logistics.byAccount?.SK || 0 } },
      { label: 'Problematické', display: fmtNumber(report.eshop.problematicOrders || 0), raw: report.eshop.problematicOrders || 0 },
      { label: 'Expirace k řešení', display: fmtNumber((report.logistics.expiringProducts || []).length), raw: (report.logistics.expiringProducts || []).length }
    ],
    statusList: [
      { title: 'WPJ a objednávky', text: summary.wpJ.ready ? 'Připojeno, bereme kompletní výsledek e-shopu.' : summary.wpJ.message },
      { title: '4PX sklady a logistika', text: 'CZ i SK běží živě.' },
      { title: 'Finance a marketing', text: summary.finance?.message || 'Zatím bez finanční zprávy.' }
    ],
    topKpis: [
      { label: '4PX sklad CZ + SK', display: fmtNumber(Math.round(total4pxStock)), sub: `CZ ${fmtNumber(summary.accounts.cz.inventory.availableStockTotal)} · SK ${fmtNumber(summary.accounts.sk.inventory.availableStockTotal)}`, raw: { total: Math.round(total4pxStock), cz: summary.accounts.cz.inventory.availableStockTotal, sk: summary.accounts.sk.inventory.availableStockTotal } },
      { label: '4PX skladové řádky', display: fmtNumber(total4pxRows), sub: `${fmtNumber(totalLowStockRows)} nízkých skladových řádků napříč CZ+SK`, raw: { totalRows: total4pxRows, lowStockRows: totalLowStockRows } },
      { label: 'Finance, čistá cash pozice', display: fmtNumber(Math.round(summary.finance?.cash?.netCashPosition || 0)), sub: summary.finance?.mode === 'mixed_live_legacy' ? 'živé závazky + starší P&L' : summary.finance?.mode === 'legacy_with_live_error' ? 'starší data + chyba API' : summary.finance?.mode === 'legacy_snapshot' ? 'starší ABRA snapshot' : 'finanční zdroj', raw: Math.round(summary.finance?.cash?.netCashPosition || 0) },
      { label: 'Marketing, poslední měsíc', display: fmtNumber(Math.round(summary.marketing?.currentMonth?.totalSpend || 0)), sub: `${(summary.marketing?.currentMonth?.spendShareOfRevenuePct ?? 0).toFixed(1)} % z výnosů`, raw: { totalSpend: Math.round(summary.marketing?.currentMonth?.totalSpend || 0), spendShareOfRevenuePct: Number((summary.marketing?.currentMonth?.spendShareOfRevenuePct ?? 0).toFixed(1)) } }
    ],
    blockers: summary.warnings?.length ? summary.warnings : (summary.report?.alerts || []).length ? summary.report.alerts : ['Nic zásadního nás teď neblokuje.'],
    sourceRows: [
      ['4PX CZ inventory', 'OK', `Warehouse ${summary.config.warehouseCode}`, summary.accounts.cz.inventory.items],
      ['4PX SK inventory', 'OK', `Warehouse ${summary.config.warehouseCode}`, summary.accounts.sk.inventory.items],
      ['4PX CZ outbound', 'OK', `Recent pages: ${summary.accounts.cz.outbound.scannedPages}`, summary.accounts.cz.outbound.items],
      ['4PX SK outbound', 'OK', `Recent pages: ${summary.accounts.sk.outbound.scannedPages}`, summary.accounts.sk.outbound.items],
      ['WPJ / GraphQL', summary.wpJ.ready ? 'OK' : 'Čeká', summary.wpJ.ready ? 'Kompletní výsledek www.kralovstvi-tiande' : summary.wpJ.message, summary.wpJ.orders || 0],
      ['ABRA Flexi / finance', summary.finance?.ready ? 'OK' : 'Čeká', summary.finance?.message || 'Bez dat', Math.round(summary.finance?.currentMonth?.revenue || 0)],
      ['Marketing', summary.marketing?.ready ? 'OK' : 'Čeká', summary.marketing?.message || 'Bez dat', Math.round(summary.marketing?.currentMonth?.totalSpend || 0)],
      ['Ranní report', 'OK', `Alerty: ${(summary.report.alerts || []).length}`, summary.report.shipments || 0]
    ].map(([source, state, whatWeSee, objects]) => ({ source, state, whatWeSee, objectsDisplay: fmtNumber(objects), objectsRaw: objects }))
  },
  backingData: {
    portal_summary: summary,
    morning_report_previous_day: report,
    marketing_overview: marketing,
    klaviyo_overview: klaviyo
  },
  quickBrainSummary: {
    ordersYesterday: report.eshop.orders || 0,
    revenueWithVatYesterday: Math.round(report.eshop.revenueWithVat || 0),
    averageOrderValueYesterday: Math.round(report.eshop.averageOrderValue || 0),
    shipmentsYesterday: report.logistics.shipmentsTotal || 0,
    problematicOrdersYesterday: report.eshop.problematicOrders || 0,
    expiringProductsCount: (report.logistics.expiringProducts || []).length,
    total4pxStock: Math.round(total4pxStock || 0),
    total4pxRows,
    totalLowStockRows,
    marketingLastMonthSpend: Math.round(summary.marketing?.currentMonth?.totalSpend || 0),
    klaviyoAttributedRevenueMonth: Math.round(klaviyo.currentMonth?.totalAttributedRevenueCzk || 0),
    klaviyoAttributedOrdersMonth: Math.round(klaviyo.currentMonth?.totalAttributedOrders || 0),
    financeCurrentMonthRevenue: Math.round(summary.finance?.currentMonth?.revenue || 0),
    attentionCount: alerts.length
  }
};

function flatten(value, basePath = '', out = []) {
  if (Array.isArray(value)) {
    value.forEach((item, index) => flatten(item, `${basePath}[${index}]`, out));
    return out;
  }
  if (value && typeof value === 'object') {
    Object.entries(value).forEach(([key, child]) => flatten(child, basePath ? `${basePath}.${key}` : key, out));
    return out;
  }
  out.push({
    path: basePath,
    value,
    valueType: value === null ? 'null' : typeof value
  });
  return out;
}

const flatItems = flatten(brain);
const flat = {
  source: outputPath,
  generatedAt: new Date().toISOString(),
  itemCount: flatItems.length,
  sourceFingerprint,
  items: flatItems
};

let previousFingerprint = null;
if (fs.existsSync(outputPath)) {
  try {
    const existing = JSON.parse(fs.readFileSync(outputPath, 'utf8'));
    previousFingerprint = existing?.source?.sourceFingerprint || null;
  } catch {}
}

const changed = previousFingerprint !== sourceFingerprint;
if (changed) {
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(brain, null, 2));
  fs.writeFileSync(flatOutputPath, JSON.stringify(flat, null, 2));
}

console.log(JSON.stringify({
  ok: true,
  changed,
  sourceFingerprint,
  previousFingerprint,
  updated: changed ? [outputPath, flatOutputPath] : [],
  itemCount: flatItems.length,
  capturedAt: brain.source.capturedAt
}, null, 2));
