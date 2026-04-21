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

  function ensureMobileSidebarControls() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar) return;

    let toggle = document.querySelector('[data-mobile-nav-toggle]');
    if (!toggle) {
      toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'mobile-nav-toggle';
      toggle.setAttribute('data-mobile-nav-toggle', '1');
      toggle.setAttribute('aria-label', 'Otevřít menu');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16"></path><path d="M4 12h16"></path><path d="M4 17h16"></path></svg>';
      document.body.appendChild(toggle);
    }

    let overlay = document.querySelector('[data-mobile-nav-overlay]');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'mobile-nav-overlay';
      overlay.setAttribute('data-mobile-nav-overlay', '1');
      document.body.appendChild(overlay);
    }

    const closeNav = () => {
      sidebar.classList.remove('open');
      overlay.classList.remove('show');
      document.body.classList.remove('nav-open');
      toggle.setAttribute('aria-expanded', 'false');
    };

    const openNav = () => {
      sidebar.classList.add('open');
      overlay.classList.add('show');
      document.body.classList.add('nav-open');
      toggle.setAttribute('aria-expanded', 'true');
    };

    if (!toggle.dataset.bound) {
      toggle.dataset.bound = '1';
      toggle.addEventListener('click', () => {
        if (sidebar.classList.contains('open')) closeNav();
        else openNav();
      });
    }

    if (!overlay.dataset.bound) {
      overlay.dataset.bound = '1';
      overlay.addEventListener('click', closeNav);
    }

    sidebar.querySelectorAll('a').forEach(link => {
      if (link.dataset.mobileNavBound === '1') return;
      link.dataset.mobileNavBound = '1';
      link.addEventListener('click', closeNav);
    });

    const syncDesktopState = () => {
      if (window.innerWidth > 900) closeNav();
    };

    if (!window.__dpMobileNavResizeBound) {
      window.__dpMobileNavResizeBound = true;
      window.addEventListener('resize', syncDesktopState);
    }
    syncDesktopState();
  }

  function renderSidebar() {
    const sidebar = document.querySelector('.sidebar');
    if (!sidebar || sidebar.innerHTML.trim()) {
      ensureMobileSidebarControls();
      return;
    }
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

    ensureMobileSidebarControls();
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
