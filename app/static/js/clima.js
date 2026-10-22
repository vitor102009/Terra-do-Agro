/* Cartão de clima do painel: busca o resumo no servidor (que consulta o Open-Meteo e mantém cache). */
(function () {
  "use strict";
  var card = document.getElementById("clima-card");
  if (!card) return;
  var body = card.querySelector("[data-clima-body]");

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function aviso(msg) {
    body.innerHTML = "";
    var p = el("p", "text-muted small mb-0");
    p.appendChild(el("i", "bi bi-cloud-slash me-1"));
    p.appendChild(document.createTextNode(msg));
    body.appendChild(p);
  }

  if (!navigator.onLine) { aviso("Sem conexão. A previsão do tempo não está disponível offline."); }

  fetch(card.getAttribute("data-clima-url"), { headers: { Accept: "application/json" }, credentials: "same-origin" })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.ok) { aviso(d.mensagem || "Previsão indisponível no momento."); return; }
      body.innerHTML = "";
      var now = el("div", "weather-now mb-2");
      now.appendChild(el("i", "bi " + d.atual.icone));
      var t = el("div");
      t.appendChild(el("div", "temp", Math.round(d.atual.temperatura) + "°C"));
      t.appendChild(el("div", "text-muted small", d.atual.descricao));
      now.appendChild(t);
      body.appendChild(now);
      body.appendChild(el("div", "small mb-2", d.local));
      var info = el("div", "small text-muted mb-3",
        "Umidade " + d.atual.umidade + "% · Vento " + Math.round(d.atual.vento) + " km/h");
      body.appendChild(info);
      var grid = el("div", "forecast");
      d.dias.slice(0, 3).forEach(function (dia) {
        var box = el("div", "day");
        box.appendChild(el("div", "small fw-semibold", dia.rotulo));
        box.appendChild(el("i", "bi " + dia.icone));
        box.appendChild(el("div", "small", Math.round(dia.max) + "° / " + Math.round(dia.min) + "°"));
        box.appendChild(el("div", "small text-muted", (dia.chuva_mm || 0).toFixed(1).replace(".", ",") + " mm"));
        grid.appendChild(box);
      });
      body.appendChild(grid);
      var src = el("p", "small text-muted mb-0 mt-2",
        "Fonte: " + d.fonte + " · Atualizado em " + d.atualizado_em + (d.cache_antigo ? " (último dado disponível)" : ""));
      body.appendChild(src);
    })
    .catch(function () { aviso("Não foi possível carregar a previsão agora."); });
})();
