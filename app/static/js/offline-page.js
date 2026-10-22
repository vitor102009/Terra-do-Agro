/* Página do modo offline: consulta dados sincronizados e registra operações na fila local. */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var chave = null;
  try { chave = localStorage.getItem("ta-current-user"); } catch (e) { chave = null; }
  var dados = null;
  var prop = null;
  var porBrinco = {};

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  function aviso(tipo, titulo, texto, acao) {
    var box = $("off-aviso");
    box.innerHTML = "";
    var card = el("div", "card");
    var body = el("div", "card-body empty-state");
    body.appendChild(el("i", "bi " + tipo));
    body.appendChild(el("h3", null, titulo));
    body.appendChild(el("p", null, texto));
    if (acao) body.appendChild(acao);
    card.appendChild(body);
    box.appendChild(card);
  }

  function hojeISO() {
    var d = new Date();
    d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
    return d.toISOString().slice(0, 10);
  }

  function dataBR(iso) {
    if (!iso) return "—";
    var p = iso.slice(0, 10).split("-");
    return p[2] + "/" + p[1] + "/" + p[0];
  }

  function conexao() {
    var b = $("off-conexao");
    if (navigator.onLine) { b.className = "badge text-bg-success"; b.textContent = "Conectado"; }
    else { b.className = "badge text-bg-danger"; b.textContent = "Sem conexão"; }
    var baixar = $("off-baixar");
    if (baixar) baixar.disabled = !navigator.onLine;
  }

  // ------------------------------------------------------------ Abas
  document.querySelectorAll("[data-aba]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      document.querySelectorAll("[data-aba]").forEach(function (b) {
        var ativo = b === btn;
        b.classList.toggle("active", ativo);
        b.setAttribute("aria-selected", ativo ? "true" : "false");
      });
      document.querySelectorAll("[data-painel]").forEach(function (p) { p.hidden = p.getAttribute("data-painel") !== btn.getAttribute("data-aba"); });
      if (btn.getAttribute("data-aba") === "fila") renderFila();
    });
  });

  // ------------------------------------------------------------ Renderização
  function renderTudo() {
    $("off-app").hidden = false;
    $("off-aviso").innerHTML = "";
    $("off-nome").textContent = dados.usuario || "";
    TAOffline.snapshotTime(chave).then(function (t) {
      $("off-sinc").textContent = t ? "Dados baixados em " + new Date(t).toLocaleString("pt-BR") + ". Alterações feitas depois disso por outras pessoas não aparecem aqui." : "";
    });
    var sel = $("off-prop");
    sel.innerHTML = "";
    dados.propriedades.forEach(function (p) {
      var o = el("option", null, p.nome + " (" + p.municipio + "/" + p.uf + ")");
      o.value = p.id;
      sel.appendChild(o);
    });
    var salvo = null;
    try { salvo = localStorage.getItem("ta-offline-prop-" + chave); } catch (e) {}
    if (salvo && dados.propriedades.some(function (p) { return String(p.id) === salvo; })) sel.value = salvo;
    var tipos = $("s-tipo");
    tipos.innerHTML = "";
    Object.keys(dados.tipos_sanitarios || {}).forEach(function (k) {
      var o = el("option", null, dados.tipos_sanitarios[k]);
      o.value = k;
      tipos.appendChild(o);
    });
    trocarPropriedade();
    renderFila();
  }

  function trocarPropriedade() {
    var id = Number($("off-prop").value);
    prop = dados.propriedades.find(function (p) { return p.id === id; }) || dados.propriedades[0];
    try { localStorage.setItem("ta-offline-prop-" + chave, String(prop.id)); } catch (e) {}
    porBrinco = {};
    var lista = $("off-lista-animais");
    lista.innerHTML = "";
    prop.animais.forEach(function (a) {
      porBrinco[String(a.brinco).toLowerCase()] = a;
      var o = el("option");
      o.value = a.brinco;
      lista.appendChild(o);
    });
    var lotes = $("s-lote");
    lotes.innerHTML = "";
    prop.lotes.forEach(function (l) { var o = el("option", null, l.nome); o.value = l.id; lotes.appendChild(o); });
    $("off-sem-permissao").hidden = prop.pode_editar;
    $("off-forms").hidden = !prop.pode_editar;
    renderAnimais();
    renderAgenda();
  }

  function renderAnimais() {
    var termo = ($("off-busca").value || "").toLowerCase().trim();
    var corpo = $("off-animais");
    corpo.innerHTML = "";
    var lista = prop.animais.filter(function (a) {
      return !termo || String(a.brinco).toLowerCase().indexOf(termo) !== -1 || (a.nome || "").toLowerCase().indexOf(termo) !== -1;
    });
    lista.slice(0, 300).forEach(function (a) {
      var tr = el("tr");
      [["Brinco", a.brinco], ["Categoria", a.categoria || "—"], ["Lote", a.lote || "—"], ["Situação", a.reprodutiva || "—"],
       ["Último peso", a.peso_atual !== null ? a.peso_atual.toLocaleString("pt-BR") + " kg (" + dataBR(a.data_peso) + ")" : "—"]]
        .forEach(function (c, i) {
          var td = el("td", i === 4 ? "num" : (i === 0 ? "fw-semibold" : null), c[1]);
          td.setAttribute("data-label", c[0]);
          tr.appendChild(td);
        });
      corpo.appendChild(tr);
    });
    if (!lista.length) {
      var tr = el("tr");
      var td = el("td", "text-center text-muted py-4", termo ? "Nenhum animal encontrado." : "Nenhum animal ativo nesta propriedade.");
      td.colSpan = 5;
      tr.appendChild(td);
      corpo.appendChild(tr);
    }
    $("off-total").textContent = lista.length + " animal(is)" + (lista.length > 300 ? " (mostrando 300; refine a busca)" : "");
  }

  function renderAgenda() {
    var ag = $("off-agenda");
    ag.innerHTML = "";
    if (!prop.agenda_sanitaria.length) ag.appendChild(el("li", "list-group-item small text-muted", "Nada programado."));
    prop.agenda_sanitaria.forEach(function (x) {
      var li = el("li", "list-group-item small");
      li.appendChild(el("div", "fw-semibold", dataBR(x.data) + " · " + x.produto));
      li.appendChild(el("div", "text-muted", x.tipo + " · " + x.alvo));
      ag.appendChild(li);
    });
    var tf = $("off-tarefas");
    tf.innerHTML = "";
    if (!prop.tarefas.length) tf.appendChild(el("li", "list-group-item small text-muted", "Nenhuma tarefa pendente."));
    prop.tarefas.forEach(function (t) {
      var li = el("li", "list-group-item small");
      li.appendChild(el("div", "fw-semibold", dataBR(t.data) + " · " + t.titulo));
      li.appendChild(el("div", "text-muted", t.tipo || ""));
      tf.appendChild(li);
    });
  }

  var ROTULOS = { pesagem: "Pesagem", sanitario: "Sanitário", tarefa: "Tarefa" };
  var SITUACAO = { pendente: ["Aguardando envio", "badge-soft-gold"], enviando: ["Enviando", "badge-soft-blue"],
                   erro: ["Erro no envio", "badge-soft-red"], conflito: ["Conflito: escolha uma ação", "badge-soft-red"],
                   rejeitada: ["Recusado pelo servidor", "badge-soft-red"] };

  function descricao(op) {
    var d = op.dados || {};
    var nomeProp = (dados && dados.propriedades.find(function (p) { return p.id === op.propriedade_id; }) || {}).nome || "";
    if (op.tipo === "pesagem") return (d.brinco || "?") + " · " + String(d.peso_kg).replace(".", ",") + " kg · " + dataBR(d.data) + " · " + nomeProp;
    if (op.tipo === "sanitario") return d.produto + " · " + (d.alvo === "animal" ? d.brinco : (d.alvo === "lote" ? "lote" : "rebanho")) + " · " + dataBR(d.data_aplicacao) + " · " + nomeProp;
    return d.titulo + " · " + dataBR(d.data) + " · " + nomeProp;
  }

  function renderFila() {
    TAOffline.listQueue(chave).then(function (fila) {
      $("off-qtd-fila").textContent = fila.length || "";
      var ul = $("off-fila");
      ul.innerHTML = "";
      if (!fila.length) { ul.appendChild(el("li", "list-group-item text-muted small", "Nenhum registro pendente. Tudo sincronizado.")); return; }
      fila.forEach(function (op) {
        var li = el("li", "list-group-item");
        var topo = el("div", "d-flex flex-wrap justify-content-between gap-2");
        topo.appendChild(el("span", "fw-semibold", ROTULOS[op.tipo] + ": " + descricao(op)));
        var s = SITUACAO[op.status] || [op.status, "badge-soft-gray"];
        topo.appendChild(el("span", "badge " + s[1], s[0]));
        li.appendChild(topo);
        li.appendChild(el("div", "small text-muted", "Criado em " + new Date(op.criado_em).toLocaleString("pt-BR") + (op.tentativas ? " · " + op.tentativas + " tentativa(s)" : "")));
        if (op.mensagem) li.appendChild(el("div", "small mt-1", op.mensagem));
        var acoes = el("div", "d-flex flex-wrap gap-2 mt-2");
        function botao(rotulo, cls, fn) { var b = el("button", "btn btn-sm " + cls, rotulo); b.type = "button"; b.addEventListener("click", fn); acoes.appendChild(b); }
        if (op.status === "conflito") {
          botao("Manter os dois registros", "btn-primary", function () { TAOffline.resolve(chave, op.uuid, "manter").then(sincronizar); });
        }
        if (op.status === "erro") botao("Tentar novamente", "btn-primary", sincronizar);
        if (op.status !== "enviando") {
          botao("Descartar", "btn-outline-danger", function () {
            if (window.confirm("Descartar este registro? Ele não será enviado ao servidor.")) TAOffline.resolve(chave, op.uuid, "descartar").then(renderFila).then(atualizarPill);
          });
        }
        li.appendChild(acoes);
        ul.appendChild(li);
      });
    });
  }

  function atualizarPill() { if (window.TAUpdateConn) window.TAUpdateConn(); }

  // ------------------------------------------------------------ Registro offline
  function msg(form, texto, ok) {
    var m = form.querySelector("[data-msg]");
    m.className = "small mt-2 " + (ok ? "text-success" : "text-danger");
    m.textContent = texto;
  }

  function guardar(form, tipo, d) {
    return TAOffline.enqueue(chave, tipo, prop.id, d).then(function () {
      form.reset();
      definirDatas();
      msg(form, navigator.onLine ? "Guardado. Enviando ao servidor…" : "Guardado neste aparelho. Será enviado quando a conexão voltar.", true);
      renderFila();
      atualizarPill();
      if (navigator.onLine) sincronizar();
    }).catch(function (e) { msg(form, "Não foi possível guardar: " + e.message, false); });
  }

  function animalDoCampo(input) {
    return porBrinco[String(input.value || "").toLowerCase().trim()];
  }

  $("f-pesagem").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = ev.target;
    var a = animalDoCampo($("p-animal"));
    var bruto = String($("p-peso").value).trim();
    var peso = Number(bruto.indexOf(",") !== -1 ? bruto.replace(/\./g, "").replace(",", ".") : bruto);
    if (!a) return msg(f, "Informe um brinco existente na lista.", false);
    if (!$("p-data").value || $("p-data").value > hojeISO()) return msg(f, "Informe uma data válida (não futura).", false);
    if (!(peso > 0 && peso <= 5000)) return msg(f, "Informe um peso entre 0 e 5000 kg.", false);
    guardar(f, "pesagem", { animal_id: a.id, brinco: a.brinco, data: $("p-data").value, peso_kg: peso, observacoes: $("p-obs").value.trim() });
  });

  $("s-alvo").addEventListener("change", function () {
    $("s-box-animal").hidden = this.value !== "animal";
    $("s-box-lote").hidden = this.value !== "lote";
  });

  $("f-sanitario").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = ev.target;
    var alvo = $("s-alvo").value;
    var d = { tipo: $("s-tipo").value, produto: $("s-produto").value.trim(), alvo: alvo, data_aplicacao: $("s-data").value,
              proxima_data: $("s-proxima").value || null, dose: $("s-dose").value.trim() };
    if (!d.produto) return msg(f, "Informe o produto.", false);
    if (!d.data_aplicacao || d.data_aplicacao > hojeISO()) return msg(f, "Informe uma data de aplicação válida (não futura).", false);
    if (d.proxima_data && d.proxima_data < d.data_aplicacao) return msg(f, "A próxima data deve ser depois da aplicação.", false);
    if (alvo === "animal") {
      var a = animalDoCampo($("s-animal"));
      if (!a) return msg(f, "Informe um brinco existente na lista.", false);
      d.animal_id = a.id; d.brinco = a.brinco;
    } else if (alvo === "lote") {
      if (!$("s-lote").value) return msg(f, "Escolha o lote.", false);
      d.lote_id = Number($("s-lote").value);
    }
    guardar(f, "sanitario", d);
  });

  $("f-tarefa").addEventListener("submit", function (ev) {
    ev.preventDefault();
    var f = ev.target;
    var d = { titulo: $("t-titulo").value.trim(), data: $("t-data").value, descricao: $("t-desc").value.trim(), tipo: "tarefa" };
    if (!d.titulo || !d.data) return msg(f, "Informe título e data.", false);
    guardar(f, "tarefa", d);
  });

  function definirDatas() {
    ["p-data", "s-data", "t-data"].forEach(function (id) { if (!$(id).value) $(id).value = hojeISO(); });
  }

  // ------------------------------------------------------------ Sincronização
  function sincronizar() {
    if (!navigator.onLine) { renderFila(); return Promise.resolve(); }
    var btn = $("off-sincronizar");
    btn.disabled = true;
    return TAOffline.syncAll(chave).then(function (r) {
      if (r && r.total) {
        var partes = [r.enviados + " enviado(s)"];
        if (r.conflitos) partes.push(r.conflitos + " com conflito");
        if (r.rejeitados) partes.push(r.rejeitados + " recusado(s)");
        if (r.erros) partes.push(r.erros + " com erro");
        $("off-sinc").textContent = "Sincronização: " + partes.join(", ") + ".";
        if (r.enviados) return TAOffline.downloadSnapshot(chave).then(function (d) { dados = d; renderTudo(); }).catch(function () {});
      }
    }).catch(function (e) {
      $("off-sinc").textContent = e.message;
    }).then(function () { btn.disabled = false; renderFila(); atualizarPill(); });
  }

  // ------------------------------------------------------------ Início
  conexao();
  window.addEventListener("online", function () { conexao(); sincronizar(); });
  window.addEventListener("offline", conexao);
  $("off-prop").addEventListener("change", trocarPropriedade);
  $("off-busca").addEventListener("input", renderAnimais);
  $("off-sincronizar").addEventListener("click", sincronizar);
  $("off-baixar").addEventListener("click", function () {
    var b = this;
    b.disabled = true;
    TAOffline.downloadSnapshot(chave).then(function (d) { dados = d; renderTudo(); })
      .catch(function (e) { $("off-sinc").textContent = e.message; })
      .then(function () { b.disabled = !navigator.onLine; });
  });
  definirDatas();

  if (!chave || !window.TAOffline) {
    var link = el("a", "btn btn-primary", "Entrar na conta");
    link.href = "/entrar";
    aviso("bi-person-lock", "Modo offline não preparado", "Entre na sua conta com internet pelo menos uma vez neste aparelho. Os dados das suas propriedades serão baixados automaticamente para consulta offline.", link);
    return;
  }

  TAOffline.getSnapshot(chave).then(function (d) {
    if (d && d.propriedades) { dados = d; }
    if (!dados && navigator.onLine) return TAOffline.downloadSnapshot(chave).then(function (n) { dados = n; });
  }).catch(function () {}).then(function () {
    if (!dados) {
      aviso("bi-cloud-slash", "Nenhum dado baixado neste aparelho", "Conecte-se à internet e abra esta página para baixar os dados das suas propriedades.");
      $("off-app").hidden = true;
      renderFila();
      return;
    }
    if (!dados.propriedades.length) {
      var l = el("a", "btn btn-primary", "Cadastrar propriedade");
      l.href = "/propriedades/nova";
      aviso("bi-house-door", "Nenhuma propriedade", "Cadastre uma propriedade para usar o registro offline.", l);
      return;
    }
    renderTudo();
    if (navigator.onLine) sincronizar();
  });
})();
