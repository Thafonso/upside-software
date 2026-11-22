// The command palette: Ctrl/Cmd + K.
//
// Three kinds of entry, in this order, because that is the order people
// look for them: an action they want to perform, a client they have in
// mind, a screen they want to be on.
//
// Clients come from a small JSON endpoint, fetched once on first open and
// kept for the life of the page. A palette that waits on the network for
// every keystroke is slower than the navigation it replaces.

(() => {
  const palette = document.getElementById("palette");
  if (!palette) return;

  const input = palette.querySelector(".palette-input");
  const list = palette.querySelector(".palette-list");
  const CLIENTS_URL = palette.dataset.clientsUrl;

  const PAGES = [
    { label: "Hunter", href: "/hunter", kind: "Tela" },
    { label: "Anúncios", href: "/ads", kind: "Tela" },
    { label: "Relatórios", href: "/reports", kind: "Tela" },
    { label: "Clientes", href: "/clients", kind: "Tela" },
  ];
  if (palette.dataset.admin === "true") {
    PAGES.push({ label: "Jobs", href: "/admin/jobs", kind: "Tela" });
  }

  const ACTIONS = [
    { label: "Nova busca no Hunter", href: "/hunter/searches/new", kind: "Ação" },
    { label: "Gerar relatório", href: "/reports", kind: "Ação" },
    { label: "Cadastrar cliente", href: "/clients/new", kind: "Ação" },
  ];

  let clients = [];
  let loaded = false;
  let entries = [];
  let selected = 0;
  let lastFocus = null;

  // Accent-insensitive, so "relatorio" finds "Relatórios".
  const fold = (value) =>
    value
      .toLowerCase()
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "");

  const loadClients = async () => {
    if (loaded || !CLIENTS_URL) return;
    loaded = true;
    try {
      const response = await fetch(CLIENTS_URL, {
        headers: { accept: "application/json" },
      });
      if (!response.ok) return;
      const data = await response.json();
      clients = (data.clients || []).map((client) => ({
        label: client.name,
        href: `/ads/clients/${client.id}`,
        kind: "Cliente",
      }));
      render(input.value);
    } catch {
      /* offline or signed out: the palette still navigates */
    }
  };

  const render = (term) => {
    const needle = fold(term.trim());
    const all = [...ACTIONS, ...clients, ...PAGES];
    entries = needle
      ? all.filter((entry) => fold(entry.label).includes(needle))
      : all;
    selected = 0;

    list.replaceChildren();
    if (!entries.length) {
      const empty = document.createElement("li");
      empty.className = "palette-empty";
      empty.textContent = "Nada encontrado.";
      list.append(empty);
      return;
    }

    entries.forEach((entry, index) => {
      const item = document.createElement("li");
      item.className = "palette-item";
      item.id = `palette-item-${index}`;
      item.setAttribute("role", "option");
      item.setAttribute("aria-selected", String(index === selected));

      const label = document.createElement("span");
      label.textContent = entry.label;
      const kind = document.createElement("span");
      kind.className = "palette-kind";
      kind.textContent = entry.kind;

      item.append(label, kind);
      item.addEventListener("click", () => go(index));
      list.append(item);
    });
    mark();
  };

  const mark = () => {
    const items = list.querySelectorAll(".palette-item");
    items.forEach((item, index) => {
      item.setAttribute("aria-selected", String(index === selected));
    });
    const active = items[selected];
    if (active) {
      active.scrollIntoView({ block: "nearest" });
      input.setAttribute("aria-activedescendant", active.id);
    }
  };

  const move = (step) => {
    if (!entries.length) return;
    selected = (selected + step + entries.length) % entries.length;
    mark();
  };

  const go = (index) => {
    const entry = entries[index];
    if (!entry) return;
    close();
    window.location.href = entry.href;
  };

  const open = () => {
    if (!palette.hidden) return;
    lastFocus = document.activeElement;
    palette.hidden = false;
    input.value = "";
    render("");
    input.focus();
    loadClients();
  };

  const close = () => {
    if (palette.hidden) return;
    palette.hidden = true;
    input.removeAttribute("aria-activedescendant");
    // Give the keyboard back to whatever had it.
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  };

  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      palette.hidden ? open() : close();
      return;
    }
    if (palette.hidden) return;

    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowDown") {
      event.preventDefault();
      move(1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      move(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(selected);
    } else if (event.key === "Tab") {
      // The palette is the only thing on screen while it is open, so the
      // focus ring stays inside it instead of wandering the page behind.
      event.preventDefault();
      input.focus();
    }
  });

  input.addEventListener("input", () => render(input.value));

  // A click on the backdrop closes; a click inside the box does not.
  palette.addEventListener("mousedown", (event) => {
    if (!event.target.closest(".palette-box")) close();
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-open-palette]")) {
      event.preventDefault();
      open();
    }
  });
})();
