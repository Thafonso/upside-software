// Movement that belongs to the whole app: page transitions, the request
// progress line, button feedback, counting numbers and staggered rows.
//
// Everything is delegated on document and re-applied after every HTMX swap,
// so nothing here depends on the page it started on.

(() => {
  const still = window.matchMedia("(prefers-reduced-motion: reduce)");

  // ── Page transitions ────────────────────────────────────
  // HTMX runs the swap inside a View Transition when this is on. Browsers
  // without the API ignore it and swap the old way.
  if (window.htmx) {
    window.htmx.config.globalViewTransitions = true;
  }

  // ── Request progress ────────────────────────────────────
  // It creeps towards 90% while the request is open, then snaps shut. We
  // cannot know real progress, so it never pretends to.
  const progress = document.getElementById("progress");
  let open = 0;

  const showProgress = () => {
    if (!progress) return;
    open += 1;
    if (open > 1) return;
    progress.dataset.active = "true";
    progress.style.transform = "scaleX(0)";
    // Two frames: the first paints the reset, the second animates away
    // from it. Without this the browser collapses both into no movement.
    requestAnimationFrame(() =>
      requestAnimationFrame(() => {
        progress.style.transform = "scaleX(0.9)";
      }),
    );
  };

  const hideProgress = () => {
    if (!progress) return;
    open = Math.max(0, open - 1);
    if (open > 0) return;
    progress.style.transform = "scaleX(1)";
    window.setTimeout(() => {
      if (open > 0) return;
      progress.dataset.active = "false";
      progress.style.transform = "scaleX(0)";
    }, 180);
  };

  document.body.addEventListener("htmx:beforeRequest", showProgress);
  document.body.addEventListener("htmx:afterSettle", hideProgress);
  // A request that errors out never settles, and a progress bar stuck at
  // 90% is worse than none.
  document.body.addEventListener("htmx:responseError", hideProgress);
  document.body.addEventListener("htmx:sendError", hideProgress);
  document.body.addEventListener("htmx:timeout", hideProgress);

  // ── Buttons ─────────────────────────────────────────────

  document.addEventListener("pointermove", (event) => {
    const button = event.target.closest(".btn--primary");
    if (!button) return;
    const box = button.getBoundingClientRect();
    button.style.setProperty("--mx", `${event.clientX - box.left}px`);
    button.style.setProperty("--my", `${event.clientY - box.top}px`);
  });

  // A button that fired a request is busy until the answer lands.
  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const button = event.detail.elt?.closest?.(".btn");
    if (button) button.setAttribute("aria-busy", "true");
  });
  const clearBusy = () => {
    document
      .querySelectorAll('.btn[aria-busy="true"]')
      .forEach((button) => button.removeAttribute("aria-busy"));
  };
  ["htmx:afterSettle", "htmx:responseError", "htmx:sendError"].forEach(
    (name) => document.body.addEventListener(name, clearBusy),
  );

  // A plain form submit gets the same treatment: the page is about to be
  // replaced, but until it is, the button has to show the click landed.
  document.addEventListener("submit", (event) => {
    const button = event.target.querySelector?.("[data-busy-on-submit]");
    if (button) button.setAttribute("aria-busy", "true");
  });

  // ── Counting numbers ────────────────────────────────────
  // The element already holds the server's formatted value, which is the
  // only correct one. We animate towards it and then put it back verbatim,
  // so a rounding difference in the browser can never survive on screen.

  const formatters = {
    brl: new Intl.NumberFormat("pt-BR", {
      style: "currency",
      currency: "BRL",
    }),
    // Counts are whole things. Without this the animation spends its first
    // frames showing "0,08 empresas", which is not a number of anything.
    number: new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 }),
  };

  const easeOutCubic = (t) => 1 - (1 - t) ** 3;

  const countUp = (element) => {
    const target = Number(element.dataset.count);
    if (!Number.isFinite(target) || element.dataset.counted === "true") return;
    element.dataset.counted = "true";

    const final = element.textContent;
    if (still.matches || target === 0) return;

    const format = formatters[element.dataset.countFormat] || formatters.number;
    const started = performance.now();
    const duration = 600;

    const frame = (now) => {
      const progressed = Math.min(1, (now - started) / duration);
      element.textContent = format.format(target * easeOutCubic(progressed));
      if (progressed < 1) {
        requestAnimationFrame(frame);
      } else {
        element.textContent = final;
      }
    };
    requestAnimationFrame(frame);
  };

  // Only count what the reader can actually see arriving.
  const watcher = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        countUp(entry.target);
        watcher.unobserve(entry.target);
      });
    },
    { threshold: 0.4 },
  );

  // ── Staggered rows ──────────────────────────────────────
  // At most 15 rows animate: past that the cascade stops reading as
  // arrival and starts reading as lag.
  const stagger = (root) => {
    const rows = root.querySelectorAll("tbody tr[data-enter]");
    rows.forEach((row, index) => {
      row.style.setProperty("--enter-index", String(Math.min(index, 15)));
    });
  };

  const scan = (root) => {
    root.querySelectorAll("[data-count]").forEach((element) => {
      if (element.dataset.counted !== "true") watcher.observe(element);
    });
    stagger(root);
  };

  scan(document);
  document.body.addEventListener("htmx:afterSettle", (event) =>
    scan(event.detail.elt || document),
  );
})();
