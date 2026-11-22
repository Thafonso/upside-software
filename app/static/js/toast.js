// Toasts.
//
// Two ways in, one way out:
//   - the server sends HX-Trigger: {"toast": {"message": "...", "kind": "success"}}
//     and HTMX dispatches a bubbling "toast" event for us;
//   - anything in the page can fire upside:toast with the same detail.
//
// A toast is an aside, never the only place a result is shown: it closes by
// itself, so it cannot be the record of what happened.

(() => {
  const region = document.getElementById("toast-region");
  if (!region) return;

  const LIFETIME = 4000;
  const KINDS = ["success", "error", "info"];

  const dismiss = (toast) => {
    if (toast.dataset.leaving === "true") return;
    toast.dataset.leaving = "true";
    // Match the CSS exit; if motion is off the animation is instant and
    // this is just a short delay nobody notices.
    window.setTimeout(() => toast.remove(), 200);
  };

  const show = ({ message, kind = "info", href, linkLabel } = {}) => {
    const text = String(message || "").trim();
    if (!text) return;

    const toast = document.createElement("div");
    toast.className = `toast toast--${KINDS.includes(kind) ? kind : "info"}`;
    // status, not alert: it is announced without interrupting whatever the
    // reader is in the middle of.
    toast.setAttribute("role", "status");

    const body = document.createElement("div");
    body.className = "toast-body";
    body.textContent = text;
    if (href) {
      const link = document.createElement("a");
      link.href = href;
      link.textContent = linkLabel || "Abrir";
      body.append(document.createElement("br"), link);
    }

    const close = document.createElement("button");
    close.type = "button";
    close.className = "toast-close";
    close.setAttribute("aria-label", "Fechar aviso");
    close.textContent = "×";
    close.addEventListener("click", () => dismiss(toast));

    toast.append(body, close);
    region.append(toast);

    // The countdown pauses on hover, so a toast cannot vanish from under
    // someone who is reaching for its link.
    let timer = window.setTimeout(() => dismiss(toast), LIFETIME);
    toast.addEventListener("mouseenter", () => window.clearTimeout(timer));
    toast.addEventListener("mouseleave", () => {
      timer = window.setTimeout(() => dismiss(toast), LIFETIME);
    });
  };

  const read = (event) => {
    const detail = event.detail || {};
    // HTMX wraps the HX-Trigger payload and adds its own elt reference.
    show(detail.value || detail);
  };

  document.body.addEventListener("toast", read);
  document.addEventListener("upside:toast", read);

  // A toast the server left in the page. HX-Trigger only reaches us on an
  // HTMX request, and a form that redirects is an ordinary page load, so
  // this is how a redirect carries its "it worked" across.
  document.querySelectorAll("[data-toast]").forEach((seed) => {
    try {
      show(JSON.parse(seed.dataset.toast));
    } catch {
      /* malformed: better silent than broken */
    }
    seed.remove();
  });

  // So other scripts can raise one without knowing about events.
  window.upsideToast = show;
})();
