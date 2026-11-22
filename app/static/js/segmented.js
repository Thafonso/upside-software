// The sliding indicator of a segmented control.
//
// The control works without this file: the radios carry the state and the
// checked label is already bold. What this adds is the marker that moves
// between options, which is pure decoration and so is allowed to depend on
// JavaScript.
//
// The marker is moved with a transform rather than `left`, so sliding it
// never asks the browser to lay the page out again.

(() => {
  const place = (group, animate = true) => {
    const marker = group.querySelector(".segment-marker");
    const checked = group.querySelector("input:checked");
    if (!marker) return;

    if (!checked) {
      marker.dataset.ready = "false";
      return;
    }

    const segment = checked.closest(".segment");
    const groupBox = group.getBoundingClientRect();
    const box = segment.getBoundingClientRect();

    if (!animate) marker.style.transition = "none";
    marker.style.width = `${box.width}px`;
    marker.style.transform = `translateX(${box.left - groupBox.left}px)`;
    marker.dataset.ready = "true";
    if (!animate) {
      // Force the style to land before transitions come back, or the
      // first paint slides in from zero.
      void marker.offsetWidth;
      marker.style.transition = "";
    }
  };

  const placeAll = (animate = false) => {
    document
      .querySelectorAll("[data-segmented]")
      .forEach((group) => place(group, animate));
  };

  document.addEventListener("change", (event) => {
    const group = event.target.closest?.("[data-segmented]");
    if (group) place(group, true);
  });

  placeAll(false);
  window.addEventListener("resize", () => placeAll(false));
  document.body.addEventListener("htmx:afterSettle", () => placeAll(false));
  // Labels shift as the webfont lands, and the marker has to follow.
  if (document.fonts?.ready) {
    document.fonts.ready.then(() => placeAll(false));
  }
})();
