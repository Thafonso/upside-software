// Comportamento que o HTML sozinho nao da conta. Delegado no documento,
// para continuar valendo depois que o HTMX troca um pedaco da tela.

document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-phones]");
  if (!button) return;

  const original = button.textContent;
  const done = (message) => {
    button.textContent = message;
    button.disabled = true;
    setTimeout(() => {
      button.textContent = original;
      button.disabled = false;
    }, 2000);
  };

  try {
    const response = await fetch(button.dataset.copyPhones, {
      headers: { "hx-request": "true" },
    });
    const text = (await response.text()).trim();
    if (!text) {
      done("Nenhum telefone");
      return;
    }
    await navigator.clipboard.writeText(text);
    const total = text.split("\n").length;
    done(`${total} copiado${total === 1 ? "" : "s"}`);
  } catch {
    done("Nao deu para copiar");
  }
});
