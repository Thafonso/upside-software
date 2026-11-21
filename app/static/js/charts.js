// Os dois gráficos diários do painel de anúncios.
//
// Deliberadamente dois gráficos e não um com dois eixos: investimento (R$) e
// resultados (contagem) têm escalas diferentes, e empilhar as duas no mesmo
// plano faz o cruzamento das linhas sugerir uma relação que o dado não tem.
// Mesmo eixo de dias nos dois, para a leitura casar na vertical.

(() => {
  const source = document.getElementById("chart-data");
  if (!source || typeof Chart === "undefined") return;

  let data;
  try {
    data = JSON.parse(source.textContent);
  } catch {
    return;
  }
  if (!data.days || data.days.length === 0) return;

  const style = getComputedStyle(document.documentElement);
  const token = (name, fallback) =>
    (style.getPropertyValue(name) || "").trim() || fallback;

  const ink = token("--ink", "#16120d");
  const muted = token("--text-muted", "#5b5247");
  const line = token("--line-soft", "rgba(22,18,13,.08)");
  const spendColor = token("--chart-spend", "#b85f40");
  const resultColor = token("--chart-results", "#1f6fbf");
  const body = token("--font-body", "sans-serif");

  const brl = new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
  // No eixo os centavos so poluem; no tooltip eles valem.
  const brlShort = new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    maximumFractionDigits: 0,
  });
  const count = new Intl.NumberFormat("pt-BR");

  const common = (format, axisFormat = format) => ({
    responsive: true,
    maintainAspectRatio: false,
    // One crosshair for the whole day, not per point.
    interaction: { mode: "index", intersect: false },
    plugins: {
      // A single series needs no legend: the figure caption names it.
      legend: { display: false },
      tooltip: {
        backgroundColor: ink,
        titleFont: { family: body, size: 12 },
        bodyFont: { family: body, size: 13 },
        padding: 10,
        displayColors: false,
        callbacks: { label: (item) => format(item.parsed.y) },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { color: line },
        ticks: {
          color: muted,
          font: { family: body, size: 11 },
          maxRotation: 0,
          autoSkipPadding: 16,
        },
      },
      y: {
        beginAtZero: true,
        grid: { color: line, drawTicks: false },
        border: { display: false },
        ticks: {
          color: muted,
          font: { family: body, size: 11 },
          padding: 8,
          maxTicksLimit: 5,
          callback: axisFormat,
        },
      },
    },
  });

  const series = (label, values, color) => ({
    label,
    data: values,
    borderColor: color,
    backgroundColor: `${color}1f`,
    borderWidth: 2,
    fill: true,
    tension: 0.25,
    pointRadius: 0,
    pointHoverRadius: 4,
    pointHoverBackgroundColor: color,
    pointHoverBorderColor: token("--paper-3", "#f4f1eb"),
    pointHoverBorderWidth: 2,
  });

  const draw = (id, label, values, color, format, axisFormat) => {
    const canvas = document.getElementById(id);
    if (!canvas) return;
    new Chart(canvas, {
      type: "line",
      data: { labels: data.days, datasets: [series(label, values, color)] },
      options: common(format, axisFormat),
    });
  };

  const money = (value) => brl.format(value ?? 0);
  const moneyAxis = (value) => brlShort.format(value ?? 0);
  const plain = (value) => count.format(value ?? 0);

  draw("chart-spend", "Investimento", data.spend, spendColor, money, moneyAxis);
  draw("chart-results", "Resultados", data.results, resultColor, plain, plain);
})();
