/* Gráficos: cada <canvas data-chart='{...}'> recebe a configuração gerada no servidor a partir de dados reais. */
(function () {
  "use strict";
  if (!window.Chart) return;
  var palette = ["#0E5E2A", "#D1A33A", "#137536", "#073817", "#8FBF9F", "#B7791F", "#5C6B60", "#2E9B54", "#E3C77A", "#1D5F8C", "#A3B8A9", "#7A5A12"];
  var brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  var num = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.color = "#5C6B60";

  document.querySelectorAll("canvas[data-chart]").forEach(function (canvas) {
    var cfg;
    try { cfg = JSON.parse(canvas.getAttribute("data-chart")); } catch (e) { return; }
    var fmt = function (v) {
      if (v === null || v === undefined) return "—";
      return cfg.money ? brl.format(v) : num.format(v) + (cfg.unit ? " " + cfg.unit : "");
    };
    var isPie = cfg.type === "doughnut" || cfg.type === "pie";
    var datasets = cfg.datasets.map(function (d, i) {
      var color = d.color || palette[i % palette.length];
      var kind = d.type || cfg.type;
      var ds = { label: d.label, data: d.data };
      if (d.type) ds.type = d.type;
      if (isPie) {
        ds.backgroundColor = d.data.map(function (_, j) { return palette[j % palette.length]; });
        ds.borderColor = "#fff";
        ds.borderWidth = 2;
      } else if (kind === "line") {
        ds.borderColor = color; ds.backgroundColor = color; ds.tension = 0.3; ds.pointRadius = 3;
        ds.spanGaps = true; ds.fill = false; ds.order = 0; ds.borderWidth = 2.5;
      } else {
        ds.backgroundColor = color; ds.borderRadius = 4; ds.maxBarThickness = 34; ds.order = 1;
      }
      return ds;
    });
    // Pesos e cotações (linhas) não começam em zero, para mostrar a variação real.
    var valueAxis = { ticks: { callback: function (v) { return fmt(v); } }, grid: { color: "#EEF2EF" }, beginAtZero: cfg.type !== "line" };
    var catAxis = { grid: { display: false } };
    new Chart(canvas, {
      type: cfg.type,
      data: { labels: cfg.labels, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: cfg.horizontal ? "y" : "x",
        plugins: {
          legend: { display: isPie || datasets.length > 1, position: isPie ? "right" : "bottom", labels: { boxWidth: 12 } },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var val = isPie ? ctx.parsed : (cfg.horizontal ? ctx.parsed.x : ctx.parsed.y);
                return (isPie ? ctx.label : ctx.dataset.label) + ": " + fmt(val);
              }
            }
          }
        },
        scales: isPie ? {} : (cfg.horizontal ? { x: valueAxis, y: catAxis } : { x: catAxis, y: valueAxis })
      }
    });
  });
})();
