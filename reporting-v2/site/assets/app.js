const state = {
  theme: localStorage.getItem('dp-theme') || 'light',
};

const DEFAULT_SIDEBAR_LINKS = [
  { href: 'index.html', label: '🏠 Přehled' },
  { href: 'finance.html', label: '💰 Finance' },
  { href: 'marketing.html', label: '📣 Reklama' },
  { href: 'inventory.html', label: '📦 Sklad' },
  { href: 'ordering.html', label: '🧠 Objednávání zboží' },
  { href: 'logistics.html', label: '🚚 Expedice 4PX' },
  { href: 'expiry.html', label: '⏳ Akce a expirace' },
  { href: 'eshop.html', label: '🛒 E-shop' },
];

const REDESIGN_SIDEBAR_LINKS = [
  { href: 'portal-redesign.html', label: '✨ Portal redesign' },
  { href: 'index.html', label: '🏠 Aktuální portal' },
  { href: 'finance.html', label: '💰 Finance' },
  { href: 'marketing.html', label: '📣 Reklama' },
  { href: 'inventory.html', label: '📦 Sklad' },
  { href: 'ordering.html', label: '🧠 Objednávání zboží' },
  { href: 'logistics.html', label: '🚚 Expedice 4PX' },
  { href: 'expiry.html', label: '⏳ Akce a expirace' },
  { href: 'eshop.html', label: '🛒 E-shop' },
];

document.documentElement.setAttribute('data-theme', state.theme);

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function renderSidebar() {
  document.querySelectorAll('.sidebar').forEach((sidebar) => {
    if (sidebar.dataset.sidebarRendered === '1') return;
    const page = sidebar.dataset.sidebarPage || '';
    const mode = sidebar.dataset.sidebarMode || 'default';
    const links = mode === 'redesign' ? REDESIGN_SIDEBAR_LINKS : DEFAULT_SIDEBAR_LINKS;
    const title = sidebar.dataset.sidebarTitle || 'Diamond Plus';
    const subtitle = sidebar.dataset.sidebarSubtitle || '';
    const section = sidebar.dataset.sidebarSection || 'Přehled';
    const footer = sidebar.dataset.sidebarFooter || '';

    sidebar.innerHTML = `
      <div class="sidebar-logo"><h2>${escapeHtml(title)}</h2><span>${escapeHtml(subtitle)}</span></div>
      <div class="sidebar-section-label">${escapeHtml(section)}</div>
      <nav class="sidebar-nav">
        ${links.map((link) => `<a class="${link.href === page ? 'active' : ''}" href="${escapeHtml(link.href)}">${escapeHtml(link.label)}</a>`).join('')}
      </nav>
      <div class="sidebar-footer">${escapeHtml(footer)}</div>
    `;
    sidebar.dataset.sidebarRendered = '1';
  });
}

function setTheme(next) {
  state.theme = next;
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('dp-theme', next);
  const button = document.querySelector('[data-theme-toggle]');
  if (button) button.textContent = next === 'dark' ? 'Světlý režim' : 'Tmavý režim';
}

function initThemeToggle() {
  const button = document.querySelector('[data-theme-toggle]');
  if (!button) return;
  button.textContent = state.theme === 'dark' ? 'Světlý režim' : 'Tmavý režim';
  button.addEventListener('click', () => setTheme(state.theme === 'dark' ? 'light' : 'dark'));
}

async function loadJson(path) {
  const res = await fetch(path, { cache: 'no-store' });
  if (!res.ok) throw new Error(`Failed to load ${path}`);
  return res.json();
}

function fmtNumber(value) {
  return new Intl.NumberFormat('cs-CZ').format(Number(value || 0));
}

function fmtDateTime(value) {
  if (!value) return '–';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return new Intl.DateTimeFormat('cs-CZ', { dateStyle: 'medium', timeStyle: 'short' }).format(d);
}

function fmtPercent(value) {
  return `${Number(value || 0).toFixed(1)} %`;
}

function sum(list, key) {
  return (list || []).reduce((acc, item) => acc + Number(item?.[key] || 0), 0);
}

function statusPill(text, kind = 'info') {
  return `<span class="pill ${kind}">${text}</span>`;
}

renderSidebar();

window.DP = { initThemeToggle, loadJson, fmtNumber, fmtDateTime, fmtPercent, sum, statusPill, renderSidebar };
