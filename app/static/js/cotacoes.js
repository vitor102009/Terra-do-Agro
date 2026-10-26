/* Central de cotações: consulta o servidor a cada N segundos (somente dados já coletados das fontes). */
(function () {
  "use strict";
  var app = document.getElementById("cotacoes-app");
  if (!app) return;

  var INTERVALO = (parseInt(app.dataset.intervalo, 10) || 3) * 1000;
  var MAX_RECUO = 30000;
  var grade = document.querySelector("[data-grade]");
  var vazio = document.querySelector("[data-vazio]");
  var dot = app.querySelector("[data-status-dot]");
  var statusTexto = app.querySelector("[data-status-texto]");
  var versao = null, timer = null, emCurso = false, falhas = 0, ultimoOk = null;

  function fmt(v, casas) {
    return Number(v).toLocaleString("pt-BR", { minimumFractionDigits: casas, maximumFractionDigits: casas });
  }
  function el(tag, cls, texto) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (texto !== undefined && texto !== null) e.textContent = texto;
    return e;
  }
  function dataBr(iso) { var p = iso.split("-"); return p[2] + "/" + p[1] + "/" + p[0]; }
  function hora(d) { return d.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }

  function cartao(c) {
    var art = el("article", "quote-card");
    art.dataset.chave = c.chave;
    var topo = el("div", "d-flex justify-content-between align-items-start gap-2");
    var nome = el("div");
    nome.appendChild(el("h3", "quote-name", c.produto));
    nome.appendChild(el("span", "badge text-bg-light", c.categoria_rotulo));
    topo.appendChild(nome);
    topo.appendChild(el("span", "badge " + (c.estado === "atualizada" ? "text-bg-success" : "text-bg-warning"),
      c.estado === "atualizada" ? "Atualizada" : "Desatualizada"));
    art.appendChild(topo);
    art.appendChild(el("div", "quote-value", "R$ " + fmt(c.valor, c.casas)));
    art.appendChild(el("div", "small text-muted", c.unidade));
    if (c.motivo_estado) art.appendChild(el("div", "small text-warning-emphasis", c.motivo_estado));
    var v = el("div", "quote-var");
    if (c.variacao !== null) {
      v.classList.add(c.variacao > 0 ? "up" : c.variacao < 0 ? "down" : "eq");
      var s = c.variacao > 0 ? "+" : "";
      v.textContent = s + fmt(c.variacao, c.casas) + " (" + (c.variacao_pct > 0 ? "+" : "") + fmt(c.variacao_pct, 2) +
        "%) em relação a " + c.comparado_com.data_br;
    } else {
      v.textContent = "Variação indisponível: não há valor de um dia anterior comparável";
    }
    art.appendChild(v);
    var dl = el("dl", "quote-meta");
    function par(t, d) { dl.appendChild(el("dt", null, t)); dl.appendChild(d); }
    par("Praça", el("dd", null, c.praca));
    par(c.publicado_br ? "Publicado" : "Referência",
        el("dd", null, c.publicado_br ? c.publicado_br + " (" + c.fuso_publicacao + ")" : dataBr(c.data_referencia) + " (a fonte não informa horário)"));
    if (c.consultado_br) par(c.origem !== "manual" ? "Consultado" : "Registrado", el("dd", null, c.consultado_br + " (Brasília)"));
    var dd = el("dd");
    if (c.fonte_url && /^https:\/\//.test(c.fonte_url)) {
      var a = el("a", null, c.fonte);
      a.href = c.fonte_url; a.target = "_blank"; a.rel = "noopener noreferrer";
      dd.appendChild(a);
    } else { dd.textContent = c.fonte; }
    par("Fonte", dd);
    art.appendChild(dl);
    var b = el("button", "btn btn-sm btn-outline-secondary w-100", "Histórico");
    b.type = "button"; b.dataset.historico = c.chave; b.dataset.titulo = c.produto;
    art.appendChild(b);
    return art;
  }

  function render(dados) {
    var antes = {};
    grade.querySelectorAll(".quote-card").forEach(function (c) {
      var v = c.querySelector(".quote-value");
      antes[c.dataset.chave] = v ? v.textContent : null;
    });
    var frag = document.createDocumentFragment();
    dados.itens.forEach(function (c) {
      var card = cartao(c);
      if (antes[c.chave] !== undefined && antes[c.chave] !== card.querySelector(".quote-value").textContent) {
        card.classList.add("quote-changed"); // valor novo publicado pela fonte
      }
      frag.appendChild(card);
    });
    grade.replaceChildren(frag);
    if (vazio) {
      vazio.hidden = dados.itens.length > 0;
      var t = vazio.querySelector("[data-vazio-texto]");
      if (t && dados.mensagem_vazia) t.textContent = dados.mensagem_vazia;
    }
    var nomes = { ok: "conectada", indisponivel: "indisponível", desativada: "desativada", aguardando: "aguardando primeira coleta" };
    (dados.fontes || []).forEach(function (f) {
      var li = document.querySelector('[data-fonte="' + f.slug + '"]');
      if (!li) return;
      li.querySelector("[data-fonte-estado]").textContent = nomes[f.estado] || f.estado;
      li.querySelector("[data-fonte-ultima]").textContent = f.ultimo_sucesso_br ? "Última consulta com sucesso: " + f.ultimo_sucesso_br + " (Brasília)." : "";
      li.querySelector("[data-fonte-erro]").textContent = f.ultimo_erro || "";
    });
  }

  function status(tipo, texto) {
    dot.dataset.tipo = tipo;
    statusTexto.textContent = texto;
  }

  function url(forcar) {
    var form = document.querySelector("[data-filtros]");
    var params = new URLSearchParams(form ? new FormData(form) : undefined);
    Array.from(params.keys()).forEach(function (k) { if (!params.get(k)) params.delete(k); });
    if (forcar) params.set("forcar", "1");
    var q = params.toString();
    return app.dataset.url + (q ? "?" + q : "");
  }

  function agendar(ms) {
    clearTimeout(timer);
    if (document.hidden) return; // retoma ao voltar para a aba
    timer = setTimeout(consultar, ms);
  }

  var btnAtualizar = app.querySelector("[data-atualizar]");

  function consultar(forcar) {
    if (emCurso) return;
    emCurso = true;
    if (forcar) {
      btnAtualizar.disabled = true;
      status("ok", "Consultando as fontes…");
    }
    fetch(url(forcar === true), { headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" })
      .then(function (r) {
        if (r.status === 401 || r.redirected) throw new Error("sessao");
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      })
      .then(function (dados) {
        falhas = 0;
        ultimoOk = new Date();
        if (dados.versao !== versao) { versao = dados.versao; render(dados); }
        status("ok", "Tela atualizada a cada " + INTERVALO / 1000 + " s · última verificação " + hora(ultimoOk) +
          ". Os preços mudam somente quando a fonte publica.");
        agendar(INTERVALO);
      })
      .catch(function (e) {
        if (e.message === "sessao") {
          status("erro", "Sua sessão expirou. Atualize a página e entre novamente.");
          return; // não insiste sem sessão
        }
        falhas += 1;
        var espera = Math.min(MAX_RECUO, INTERVALO * Math.pow(2, falhas));
        status("aviso", "Sem conexão com o servidor. Mostrando os últimos dados recebidos" +
          (ultimoOk ? " (" + hora(ultimoOk) + ")" : "") + ". Nova tentativa em " + Math.round(espera / 1000) + " s.");
        agendar(espera);
      })
      .finally(function () { emCurso = false; btnAtualizar.disabled = false; });
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      clearTimeout(timer);
      status("pausa", "Atualização pausada enquanto a página não está visível.");
    } else {
      consultar();
    }
  });
  window.addEventListener("online", function () { consultar(); });
  btnAtualizar.addEventListener("click", function () { clearTimeout(timer); versao = null; consultar(true); });
  var form = document.querySelector("[data-filtros]");
  if (form) form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    history.replaceState(null, "", "?" + new URLSearchParams(new FormData(form)).toString());
    versao = null; clearTimeout(timer); consultar();
  });

  // ---- histórico
  var modalEl = document.getElementById("modal-historico");
  var grafico = null, chaveAtual = null;
  function carregarHistorico(dias) {
    var resumo = modalEl.querySelector("[data-hist-resumo]");
    resumo.textContent = "Carregando…";
    modalEl.querySelectorAll("[data-dias]").forEach(function (b) { b.classList.toggle("active", b.dataset.dias === String(dias)); });
    fetch(app.dataset.historicoUrl + "?" + new URLSearchParams({ chave: chaveAtual, dias: dias }), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(); return r.json(); })
      .then(function (h) {
        if (grafico) { grafico.destroy(); grafico = null; }
        if (h.pontos.length < 2) {
          resumo.textContent = "Histórico insuficiente para gráfico neste período (" + h.pontos.length + " registro).";
          return;
        }
        var vp = h.variacao_periodo;
        resumo.textContent = "Variação de " + dataBr(vp.de) + " a " + dataBr(vp.ate) + ": " + (vp.variacao > 0 ? "+" : "") +
          fmt(vp.variacao, 4) + " (" + (vp.variacao_pct > 0 ? "+" : "") + fmt(vp.variacao_pct, 2) + "%). Unidade: " + h.unidade +
          ". Fonte: " + h.fonte + ". Último valor de cada dia.";
        if (!window.Chart) return;
        grafico = new window.Chart(modalEl.querySelector("[data-hist-canvas]"), {
          type: "line",
          data: { labels: h.pontos.map(function (p) { return dataBr(p.data); }),
                  datasets: [{ label: h.produto, data: h.pontos.map(function (p) { return p.valor; }), borderColor: "#0B5A27", tension: 0.2, pointRadius: 2 }] },
          options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } },
        });
      })
      .catch(function () { resumo.textContent = "Não foi possível carregar o histórico agora."; });
  }
  grade.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-historico]");
    if (!b || !window.bootstrap) return;
    chaveAtual = b.dataset.historico;
    modalEl.querySelector("#historico-titulo").textContent = "Histórico: " + b.dataset.titulo;
    window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
    carregarHistorico(90);
  });
  modalEl.querySelectorAll("[data-dias]").forEach(function (b) {
    b.addEventListener("click", function () { carregarHistorico(parseInt(b.dataset.dias, 10)); });
  });

  agendar(INTERVALO);
})();
