(function () {
  const sidebarLinks = [
    ['index.html', 'Přehled firmy'],
    ['inventory.html', 'Sklad'],
    ['eshop.html', 'E-shop'],
    ['logistics.html', 'Logistika'],
    ['expiry.html', 'Expirace a akce'],
    ['finance.html', 'Finance'],
    ['marketing.html', 'Marketing'],
    ['ordering.html', 'Objednávání'],
    ['meetings.html', 'Porady']
  ];

  function fmtNumber(value) {
    const num = Number(value || 0);
    return new Intl.NumberFormat('cs-CZ').format(num);
  }

  function fmtDateTime(value) {
    if (!value) return '–';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat('cs-CZ', {
      year: 'numeric', month: 'numeric', day: 'numeric',
      hour: '2-digit', minute: '2-digit'
    }).format(date);
  }

  async function loadJson(url) {
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
    return res.json();
  }

  function statusPill(label, tone = 'info') {
    const safeLabel = escapeHtml(label == null ? '' : String(label));
    const safeTone = ['success', 'warning', 'danger', 'info'].includes(tone) ? tone : 'info';
    return `<span class="pill ${safeTone}">${safeLabel}</span>`;
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function applyTheme(theme) {
    if (theme === 'dark') document.documentElement.setAttribute('data-theme', 'dark');
    else document.documentElement.removeAttribute('data-theme');
  }

  function initThemeToggle() {
    const saved = localStorage.getItem('dp-theme');
    applyTheme(saved === 'dark' ? 'dark' : 'light');
    const buttons = document.querySelectorAll('[data-theme-toggle]');
    const sync = () => {
      const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
      buttons.forEach(btn => btn.textContent = isDark ? 'Světlý režim' : 'Tmavý režim');
    };
    sync();
    buttons.forEach(btn => {
      if (btn.dataset.themeBound === '1') return;
      btn.dataset.themeBound = '1';
      btn.addEventListener('click', () => {
        const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
        const next = isDark ? 'light' : 'dark';
        localStorage.setItem('dp-theme', next);
        applyTheme(next);
        sync();
      });
    });
  }

  function renderSidebar() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar || sidebar.innerHTML.trim()) return;
    const current = sidebar.dataset.sidebarPage || 'index.html';
    const title = sidebar.dataset.sidebarTitle || 'Diamond Plus';
    const subtitle = sidebar.dataset.sidebarSubtitle || '';
    const section = sidebar.dataset.sidebarSection || 'Přehled';
    const footer = sidebar.dataset.sidebarFooter || '';

    sidebar.innerHTML = `
      <div class="sidebar-logo">
        <h2>${escapeHtml(title)}</h2>
        <span>${escapeHtml(subtitle)}</span>
      </div>
      <div class="sidebar-section-label">${escapeHtml(section)}</div>
      <nav class="sidebar-nav">
        ${sidebarLinks.map(([href, label]) => `
          <a href="${href}" class="${href === current ? 'active' : ''}">${escapeHtml(label)}</a>
        `).join('')}
      </nav>
      <div class="sidebar-footer">${escapeHtml(footer)}</div>
    `;
  }

  document.addEventListener('DOMContentLoaded', () => {
    renderSidebar();
    initThemeToggle();
  });

  window.DP = {
    fmtNumber,
    fmtDateTime,
    loadJson,
    statusPill,
    initThemeToggle,
    renderSidebar,
  };
})();
