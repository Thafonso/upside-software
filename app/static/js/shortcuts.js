// Keyboard shortcuts.
//
// G is a prefix, the way it is in most tools built for people who live in
// them: G then H goes to the Hunter. The prefix expires after a second, so a
// stray G never eats the next keystroke.
//
// Nothing fires while the caret is in a field, and nothing fires while the
// command palette is open - it owns the keyboard then.

(() => {
  const GO_TIMEOUT = 1000;

  const DESTINATIONS = {
    h: { href: "/hunter", label: "Hunter" },
    a: { href: "/ads", label: "Anúncios" },
    r: { href: "/reports", label: "Relatórios" },
    c: { href: "/clients", label: "Clientes" },
  };

  const typing = (target) => {
    if (!target) return false;
    if (target.isContentEditable) return true;
    return ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
  };

  const panel = document.getElementById("shortcuts");
  const paletteOpen = () => {
    const palette = document.getElementById("palette");
    return palette && !palette.hidden;
  };

  const togglePanel = (open) => {
    if (!panel) return;
    panel.hidden = open === undefined ? !panel.hidden : !open;
  };

  let waitingForGo = false;
  let goTimer = 0;

  const startGo = () => {
    waitingForGo = true;
    window.clearTimeout(goTimer);
    goTimer = window.setTimeout(() => {
      waitingForGo = false;
    }, GO_TIMEOUT);
  };

  const stopGo = () => {
    waitingForGo = false;
    window.clearTimeout(goTimer);
  };

  document.addEventListener("keydown", (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    if (typing(event.target) || paletteOpen()) return;

    const key = event.key.toLowerCase();

    if (event.key === "Escape" && panel && !panel.hidden) {
      togglePanel(false);
      return;
    }

    if (waitingForGo) {
      stopGo();
      const destination = DESTINATIONS[key];
      if (destination) {
        event.preventDefault();
        window.location.href = destination.href;
      }
      return;
    }

    if (key === "g") {
      startGo();
      return;
    }

    if (key === "n") {
      event.preventDefault();
      window.location.href = "/hunter/searches/new";
      return;
    }

    // "?" is a key of its own on some layouts and shift+/ on others, and
    // synthetic events do not always resolve the shifted character.
    if (event.key === "?" || (event.shiftKey && event.key === "/")) {
      event.preventDefault();
      togglePanel();
    }
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-close-shortcuts]")) togglePanel(false);
    if (event.target.closest("[data-open-shortcuts]")) togglePanel(true);
  });
})();
