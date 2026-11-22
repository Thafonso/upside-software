// Anchored popovers: the score ring's "why" and the report preview.
//
// Both use the native popover API, which puts the panel in the top layer.
// That is the whole reason: these panels live inside tables that scroll
// horizontally, and anything positioned normally would be clipped by that
// scroll. The top layer also gives Esc and click-outside for free.
//
// What the top layer does not give is a position, because it has no idea
// where the trigger is. CSS anchor positioning would say it declaratively,
// but it is not in every browser yet, so we measure.
//
// Delegated on document, so both survive every HTMX swap.

(() => {
  const GAP = 8;
  const HOVER_IN = 180;
  const HOVER_OUT = 260;

  // The popover currently open, and the element it belongs to.
  let anchored = null;
  let hoverTimer = 0;

  const place = () => {
    if (!anchored) return;
    const { popover, trigger } = anchored;
    if (!popover.matches(":popover-open") || !document.contains(trigger)) {
      anchored = null;
      return;
    }

    const anchor = trigger.getBoundingClientRect();
    const box = popover.getBoundingClientRect();

    // Below the trigger by default, above it when there is no room below.
    const spaceBelow = window.innerHeight - anchor.bottom;
    const top =
      spaceBelow > box.height + GAP
        ? anchor.bottom + GAP
        : anchor.top - box.height - GAP;

    // Left-aligned with the trigger, nudged back inside the viewport.
    let left = anchor.left;
    left = Math.max(GAP, Math.min(left, window.innerWidth - box.width - GAP));

    popover.style.top = `${Math.max(GAP, top)}px`;
    popover.style.left = `${left}px`;
  };

  const open = (popover, trigger) => {
    if (popover.matches(":popover-open")) return;
    anchored = { popover, trigger };
    popover.showPopover();
    requestAnimationFrame(place);
  };

  // ── Click triggers (the score ring) ─────────────────────
  // The browser toggles these itself through popovertarget; we only have
  // to put the panel in the right place afterwards.
  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[popovertarget]");
    if (!trigger) return;
    const popover = document.getElementById(
      trigger.getAttribute("popovertarget"),
    );
    if (!popover?.matches("[popover]")) return;

    anchored = { popover, trigger };
    requestAnimationFrame(place);
  });

  // ── Hover and focus triggers (the report preview) ───────
  // A short delay in, a longer one out: without the first, the preview
  // flickers as the cursor crosses the row on its way somewhere else.
  const previewOf = (element) => {
    const trigger = element?.closest?.("[data-preview]");
    if (!trigger) return null;
    const popover = document.getElementById(trigger.dataset.preview);
    return popover?.matches("[popover]") ? { popover, trigger } : null;
  };

  const schedule = (target, delay, action) => {
    window.clearTimeout(hoverTimer);
    hoverTimer = window.setTimeout(() => action(target), delay);
  };

  document.addEventListener("pointerover", (event) => {
    const found = previewOf(event.target);
    if (!found) return;
    schedule(found, HOVER_IN, ({ popover, trigger }) => open(popover, trigger));
  });

  document.addEventListener("pointerout", (event) => {
    const found = previewOf(event.target);
    if (!found) return;
    schedule(found, HOVER_OUT, ({ popover }) => {
      if (popover.matches(":popover-open")) popover.hidePopover();
    });
  });

  // The keyboard gets the same preview: focus opens it, leaving closes it.
  document.addEventListener("focusin", (event) => {
    const found = previewOf(event.target);
    if (found) open(found.popover, found.trigger);
  });
  document.addEventListener("focusout", (event) => {
    const found = previewOf(event.target);
    if (found?.popover.matches(":popover-open")) found.popover.hidePopover();
  });

  // Follow the trigger instead of closing. An earlier version closed on
  // scroll, which fought itself: opening a popover can move focus and
  // scroll the page, so it shut in the same frame it opened.
  const follow = () => {
    if (anchored) requestAnimationFrame(place);
  };
  window.addEventListener("scroll", follow, { passive: true, capture: true });
  window.addEventListener("resize", follow);

  document.addEventListener("toggle", (event) => {
    if (!event.target.matches?.("[popover]")) return;
    if (event.newState === "closed" && anchored?.popover === event.target) {
      anchored = null;
    }
  });
})();
