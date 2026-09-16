/* 첫 화면을 그리기 전에 테마만 복원한다. 계정이나 작업 정보는 저장하지 않는다. */
(() => {
  const key = 'ai-company-theme';
  const system = window.matchMedia('(prefers-color-scheme: dark)');
  const valid = value => value === 'light' || value === 'black';
  let preference;
  try { preference = localStorage.getItem(key); } catch { /* 저장 차단 시 현재 화면에서 사용. */ }
  function apply(theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'black' ? '#17181c' : '#f5f5f6');
    document.querySelectorAll('[data-theme-choice]').forEach(button => {
      button.setAttribute('aria-pressed', String(button.dataset.themeChoice === theme));
    });
  }
  const resolve = () => valid(preference) ? preference : system.matches ? 'black' : 'light';
  apply(resolve());
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-theme-choice]');
    if (!button || !valid(button.dataset.themeChoice)) return;
    preference = button.dataset.themeChoice;
    apply(preference);
    try { localStorage.setItem(key, preference); } catch { /* 로그인과 화면 조작은 계속 사용. */ }
  });
  system.addEventListener('change', () => { if (!valid(preference)) apply(resolve()); });
  window.addEventListener('storage', event => {
    if (event.key !== key && event.key !== null) return;
    preference = event.newValue;
    apply(resolve());
  });
})();
