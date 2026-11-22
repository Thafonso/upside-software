// The two daily charts of the ads panel.
//
// Deliberately two charts and not one with two axes: spend (R$) and results
// (a count) are on different scales, and stacking them on one plane makes
// the crossing of the lines suggest a relationship the data does not have.
// Same day axis on both, so the comparison still reads, vertically.
//
// The crosshair is what ties them together: hovering a day in one marks the
// same day in the other, which is the comparison the single chart was for,
// without inventing the correlation.

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

  const still = window.matchMedia("(prefers-reduced-motion: reduce)");
  const charts = new Map();

  const tokens = () => {
    const style = getComputedStyle(document.documentElement);
    const token = (name, fallback) =>
      (style.getPropertyValue(name) || "").trim() || fallback;
    return {
      ink: token("--ink", "#16120d"),
      muted: token("--text-muted", "#5b5247"),
      line: token("--line-soft", "rgba(22,18,13,.08)"),
      spend: token("--chart-spend", "#b85f40"),
      results: token("--chart-results", "#6fb3e8"),
      surface: token("--surface-raised", "#f4f1eb"),
      body: token("--font-body", "sans-serif"),
    };
  };

  const brl = new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  });
  // Cents only clutter the axis; in the tooltip they are worth having.
  const brlShort = new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    maximumFractionDigits: 0,
  });
  const count = new Intl.NumberFormat("pt-BR");

  // ── Crosshair, shared by both charts ────────────────────
  // A plugin rather than a DOM overlay: it draws in the same canvas space
  // as the line, so it cannot drift out of alignment with the points.
  let activeIndex = null;

  const crosshair = {
    id: "crosshair",
    afterDatasetsDraw(chart) {
      if (activeIndex === null) return;
      const meta = chart.getDatasetMeta(0);
      const point = meta.data?.[activeIndex];
      if (!point) return;

      const { ctx, chartArea } = chart;
      ctx.save();
      ctx.strokeStyle = chart.$tokens.muted;
      ctx.globalAlpha = 0.45;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(point.x, chartArea.top);
      ctx.lineTo(point.x, chartArea.bottom);
      ctx.stroke();

      ctx.globalAlpha = 1;
      ctx.setLineDash([]);
      ctx.fillStyle = chart.$colour;
      ctx.beginPath();
      ctx.arc(point.x, point.y, 3.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    },
  };

  const syncTo = (index) => {
    if (activeIndex === index) return;
    activeIndex = index;
    charts.forEach((chart) => chart.draw());
  };

  const options = (t, format, axisFormat, colour) => ({
    responsive: true,
    maintainAspectRatio: false,
    // The line draws itself in from the left, once.
    animation: still.matches
      ? false
      : { x: { duration: 700, easing: "easeOutCubic", from: 0 }, y: { duration: 0 } },
    interaction: { mode: "index", intersect: false },
    onHover: (event, elements) => {
      syncTo(elements.length ? elements[0].index : null);
    },
    plugins: {
      // A single series needs no legend: the figure caption names it.
      legend: { display: false },
      tooltip: {
        backgroundColor: t.ink,
        titleFont: { family: t.body, size: 12 },
        bodyFont: { family: t.body, size: 13 },
        padding: 10,
        displayColors: false,
        callbacks: { label: (item) => format(item.parsed.y) },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { color: t.line },
        ticks: {
          color: t.muted,
          font: { family: t.body, size: 11 },
          maxRotation: 0,
          autoSkipPadding: 16,
        },
      },
      y: {
        beginAtZero: true,
        grid: { color: t.line, drawTicks: false },
        border: { display: false },
        ticks: {
          color: t.muted,
          font: { family: t.body, size: 11 },
          padding: 8,
          maxTicksLimit: 5,
          callback: axisFormat,
        },
      },
    },
  });

  // The fill is a gradient down from the series colour, which needs the
  // canvas to exist first - hence a function rather than a value.
  const fill = (colour) => (context) => {
    const { ctx, chartArea } = context.chart;
    if (!chartArea) return `${colour}1f`;
    const gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    gradient.addColorStop(0, `${colour}2e`); // ~18% at the top
    gradient.addColorStop(1, `${colour}00`);
    return gradient;
  };

  const series = (label, values, colour, t) => ({
    label,
    data: values,
    borderColor: colour,
    backgroundColor: fill(colour),
    borderWidth: 2,
    fill: true,
    tension: 0.25,
    pointRadius: 0,
    pointHoverRadius: 0, // the crosshair draws the marker
    pointHitRadius: 12,
  });

  const draw = (id, label, values, colourToken, format, axisFormat) => {
    const canvas = document.getElementById(id);
    if (!canvas) return;

    const existing = charts.get(id);
    if (existing) existing.destroy();

    const t = tokens();
    const colour = t[colourToken];
    const chart = new Chart(canvas, {
      type: "line",
      data: { labels: data.days, datasets: [series(label, values, colour, t)] },
      options: options(t, format, axisFormat, colour),
      plugins: [crosshair],
    });
    chart.$tokens = t;
    chart.$colour = colour;
    charts.set(id, chart);
  };

  const money = (value) => brl.format(value ?? 0);
  const moneyAxis = (value) => brlShort.format(value ?? 0);
  const plain = (value) => count.format(value ?? 0);

  const build = () => {
    draw("chart-spend", "Investimento", data.spend, "spend", money, moneyAxis);
    draw("chart-results", "Resultados", data.results, "results", plain, plain);
  };

  build();

  // Chart.js bakes colours in at build time, so a theme switch needs the
  // charts built again - theme.js announces it.
  document.addEventListener("upside:themechange", build);

  // Leaving either chart clears the crosshair on both.
  document.addEventListener("mouseleave", (event) => {
    if (event.target.closest?.(".chart-box")) syncTo(null);
  }, true);
})();
