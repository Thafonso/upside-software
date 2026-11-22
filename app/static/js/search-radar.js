// The live part of a running search.
//
// The server polls every 5 seconds and that is unchanged. What it cannot do
// is make the seconds tick in between, so the elapsed time and the progress
// bar are counted here, from the start instant the server rendered into the
// panel. Nothing here asks the server anything.
//
// Re-armed after every HTMX swap, because the swap replaces the panel.

(() => {
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  let timer = 0;

  const plural = (value, one, many) => (value === 1 ? one : many);

  const spell = (seconds) => {
    const minutes = Math.floor(seconds / 60);
    const rest = seconds % 60;
    if (!minutes) return `${rest} ${plural(rest, "segundo", "segundos")}`;
    return `${minutes} ${plural(minutes, "minuto", "minutos")} e ${rest}s`;
  };

  const tick = () => {
    const panel = document.querySelector("[data-started-at]");
    if (!panel) {
      window.clearInterval(timer);
      timer = 0;
      return;
    }

    const started = Date.parse(panel.dataset.startedAt);
    const budget = Number(panel.dataset.maxTime) || 0;
    if (!Number.isFinite(started)) return;

    const elapsed = Math.max(0, Math.round((Date.now() - started) / 1000));

    const clock = panel.querySelector("[data-elapsed]");
    if (clock) clock.textContent = spell(elapsed);

    // The bar stops at 97%: the search is not done until the server says
    // it is, and a bar sitting full while the panel still spins is a lie.
    const bar = panel.querySelector("[data-elapsed-bar]");
    if (bar && budget > 0) {
      const share = Math.min(97, (elapsed / budget) * 100);
      bar.style.width = `${share.toFixed(1)}%`;
    }
  };

  const arm = () => {
    const panel = document.querySelector("[data-started-at]");
    if (!panel) {
      if (timer) window.clearInterval(timer);
      timer = 0;
      return;
    }
    tick();
    if (timer || still.matches) return;
    timer = window.setInterval(tick, 1000);
  };

  arm();
  document.body.addEventListener("htmx:afterSettle", arm);
})();
