// The Ink / Paper switch.
//
// The theme itself is applied by a tiny inline script in <head>, before the
// first paint, so the app never flashes the wrong ground. This file only owns
// the control in the rail foot and the writing side of localStorage.
//
// Event delegation on document, so the toggle keeps working after any HTMX
// swap replaces the rail.

(() => {
  const STORAGE_KEY = "upside.theme";
  const THEMES = ["ink", "paper"];

  const current = () => document.documentElement.dataset.theme || "ink";

  const remember = (theme) => {
    // Private windows and blocked site data throw here. Losing the
    // preference is survivable; breaking the click is not.
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* no storage: the choice lasts for this page only */
    }
  };

  const paint = (theme) => {
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll("[data-set-theme]").forEach((button) => {
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.setTheme === theme),
      );
    });
    // Canvas charts read their colours from the tokens at build time, so
    // they have to be drawn again. Whoever owns a chart listens for this.
    document.dispatchEvent(
      new CustomEvent("upside:themechange", { detail: { theme } }),
    );
  };

  const apply = (theme) => {
    if (!THEMES.includes(theme) || theme === current()) return;
    paint(theme);
    remember(theme);
  };

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-set-theme]");
    if (!button) return;
    event.preventDefault();
    apply(button.dataset.setTheme);
  });

  // The buttons are rendered without a pressed state, so that the markup
  // does not have to know which theme the inline script settled on.
  paint(current());
})();
