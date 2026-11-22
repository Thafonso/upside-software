// Behaviour that belongs to the prospect list.
//
// Changing a status replaces the whole table, which is correct - the row may
// no longer belong in the current filter - but it also means the change
// leaves no trace on screen. So we remember which prospect was touched and,
// once the new table lands, flash that row. If it is gone, the filter
// removed it, and that is its own kind of answer.

(() => {
  let touched = null;

  document.body.addEventListener("htmx:beforeRequest", (event) => {
    const select = event.detail.elt?.closest?.("[data-status-of]");
    touched = select ? select.dataset.statusOf : null;
  });

  document.body.addEventListener("htmx:afterSettle", () => {
    if (!touched) return;
    const row = document.querySelector(`[data-prospect-id="${touched}"]`);
    touched = null;
    if (!row) return;
    row.classList.remove("row-flash");
    // Reading offsetWidth restarts the animation when the same row is
    // changed twice in a row; without it the class is already there and
    // nothing replays.
    void row.offsetWidth;
    row.classList.add("row-flash");
    window.setTimeout(() => row.classList.remove("row-flash"), 1200);
  });

  // Copying one number, from the row's quick actions.
  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-text]");
    if (!button) return;

    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(button.dataset.copyText);
      button.textContent = "ok";
      window.upsideToast?.({ message: "Telefone copiado.", kind: "success" });
    } catch {
      button.textContent = "erro";
    }
    window.setTimeout(() => {
      button.textContent = original;
    }, 1500);
  });
})();
