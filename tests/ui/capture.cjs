/* Capture the real settled theme without disabling CSS transitions or browser sandboxing. */
async function waitForTheme(page, expectedTheme) {
  const theme = expectedTheme || await page.locator('html').getAttribute('data-theme');
  if (!['light', 'black'].includes(theme)) throw new Error(`Unknown capture theme: ${theme}`);
  // A click leaves the pointer on the theme button. Remove hover before checking its fill.
  await page.mouse.move(0, 0);
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(async expected => {
    const deadline = performance.now() + 5000;
    let previous = '', stableFrames = 0, diagnostic = '';
    const rgb = value => {
      const hex = value.trim().replace(/^#/, '');
      if (!/^(?:[a-f\d]{3}|[a-f\d]{6})$/i.test(hex)) throw new Error(`Unsupported theme token: ${value}`);
      const full = hex.length === 3 ? [...hex].map(x => x + x).join('') : hex;
      return `rgb(${[0, 2, 4].map(i => parseInt(full.slice(i, i + 2), 16)).join(', ')})`;
    };
    while (performance.now() < deadline) {
      await new Promise(requestAnimationFrame);
      const root = document.documentElement;
      const tokens = getComputedStyle(root);
      const choices = [...document.querySelectorAll('[data-theme-choice]')];
      const stateMatches = root.dataset.theme === expected && choices.length > 0 &&
        choices.some(button => button.dataset.themeChoice === expected) &&
        choices.every(button => button.getAttribute('aria-pressed') === String(button.dataset.themeChoice === expected));
      const body = getComputedStyle(document.body);
      const colorsMatch = body.backgroundColor === rgb(tokens.getPropertyValue('--bg')) &&
        body.color === rgb(tokens.getPropertyValue('--ink')) && choices.every(button => {
          const style = getComputedStyle(button);
          return button.dataset.themeChoice === expected
            ? style.backgroundColor === rgb(tokens.getPropertyValue('--surface')) && style.color === rgb(tokens.getPropertyValue('--ink'))
            : style.backgroundColor === 'rgba(0, 0, 0, 0)';
        });
      // Wait for genuine CSS transitions, without fast-forwarding finite collaboration events.
      const transitioning = document.getAnimations().some(animation =>
        animation instanceof CSSTransition && (animation.playState === 'running' || animation.pending));
      const samples = [...document.querySelectorAll('body, .sidebar, [data-theme-choice], .primary, .page-actions button')]
        .map(element => {
          const style = getComputedStyle(element);
          return [style.backgroundColor, style.color, style.borderColor, style.opacity];
        });
      const signature = JSON.stringify(samples);
      stableFrames = stateMatches && colorsMatch && !transitioning && signature === previous ? stableFrames + 1 : 0;
      previous = signature;
      diagnostic = JSON.stringify({expected, actual:root.dataset.theme, stateMatches, colorsMatch, transitioning, samples});
      if (stableFrames >= 3) return;
    }
    throw new Error(`Theme did not settle before capture: ${diagnostic}`);
  }, theme);
}

async function selectTheme(page, theme) {
  await page.locator(`[data-theme-choice="${theme}"]`).click();
  await waitForTheme(page, theme);
}

async function settledScreenshot(page, options) {
  await waitForTheme(page);
  return page.screenshot(options);
}

module.exports = {waitForTheme, selectTheme, settledScreenshot};
