/* Chat: envio idempotente (UUID por mensagem) e atualização periódica.
   Uma mensagem só aparece como enviada depois que o servidor confirma que foi salva. */
(function () {
  "use strict";
  var box = document.getElementById("chat-msgs");
  if (!box) return;
  var form = document.getElementById("chat-form");
  var pollUrl = box.getAttribute("data-poll-url");
  var sendUrl = box.getAttribute("data-send-url");
  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var ultimoId = 0;
  var vistos = {};
  var POLL_MS = 4000;

  function uuid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (crypto.getRandomValues(new Uint8Array(1))[0] & 15);
      return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
    });
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function nearBottom() { return box.scrollHeight - box.scrollTop - box.clientHeight < 120; }
  function toBottom() { box.scrollTop = box.scrollHeight; }

  function render(m, existing) {
    var div = existing || el("div");
    div.className = "msg" + (m.minha ? " mine" : "");
    div.innerHTML = "";
    div.dataset.id = m.id;
    if (m.uuid) div.dataset.uuid = m.uuid;
    div.appendChild(document.createTextNode(m.texto));
    if (m.compartilhado) {
      var a = el("a", "shared");
      a.href = m.compartilhado.url;
      a.appendChild(el("strong", null, m.compartilhado.tipo + ": "));
      a.appendChild(document.createTextNode(m.compartilhado.titulo));
      div.appendChild(a);
    }
    var t = el("div", "time", m.hora);
    t.title = m.data;
    if (m.minha) {
      var ico = el("i", "bi " + (m.lida ? "bi-check2-all" : "bi-check2") + " ms-1");
      ico.setAttribute("aria-label", m.lida ? "lida" : "entregue");
      ico.dataset.leitura = "1";
      t.appendChild(ico);
    }
    div.appendChild(t);
    return div;
  }

  function adicionar(lista) {
    var descer = nearBottom();
    var vazio = box.querySelector("[data-vazio]");
    lista.forEach(function (m) {
      if (vistos[m.id]) return;
      vistos[m.id] = true;
      if (vazio) { vazio.remove(); vazio = null; }
      var pend = m.uuid ? box.querySelector('.msg.pending[data-uuid="' + m.uuid + '"]') : null;
      if (pend) render(m, pend);
      else box.appendChild(render(m));
      if (m.id > ultimoId) ultimoId = m.id;
    });
    if (descer) toBottom();
  }

  function marcarLidas(ateId) {
    box.querySelectorAll(".msg.mine").forEach(function (d) {
      if (+d.dataset.id && +d.dataset.id <= ateId) {
        var i = d.querySelector("[data-leitura]");
        if (i) { i.className = "bi bi-check2-all ms-1"; i.setAttribute("aria-label", "lida"); }
      }
    });
  }

  try { adicionar(JSON.parse(document.getElementById("chat-inicial").textContent)); } catch (e) { /* sem mensagens */ }
  toBottom();

  var polling = false;
  function poll() {
    if (polling || document.hidden || !navigator.onLine) return;
    polling = true;
    fetch(pollUrl + "?depois=" + ultimoId, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) { adicionar(d.mensagens); marcarLidas(d.lida_ate); })
      .catch(function () {})
      .finally(function () { polling = false; });
  }
  setInterval(poll, POLL_MS);
  document.addEventListener("visibilitychange", poll);

  function enviar(payload, bolha) {
    bolha.classList.remove("failed");
    bolha.classList.add("pending");
    var status = bolha.querySelector(".time");
    status.textContent = "enviando…";
    return fetch(sendUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, Accept: "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) {
      return r.json().then(function (d) { return { ok: r.ok, d: d }; });
    }).then(function (res) {
      if (!res.ok) throw new Error(res.d.erro || "Falha ao enviar.");
      bolha.classList.remove("pending");
      adicionar([res.d.mensagem]);
      render(res.d.mensagem, bolha);
    }).catch(function (err) {
      // Não finge envio: a mensagem fica marcada como não enviada, com opção de reenviar (mesmo UUID).
      bolha.classList.remove("pending");
      bolha.classList.add("failed");
      status.textContent = "";
      var b = el("button", "btn btn-sm btn-link p-0 text-danger", "Não enviada. Tentar novamente");
      b.type = "button";
      b.title = err.message;
      b.addEventListener("click", function () { enviar(payload, bolha); });
      status.appendChild(b);
    });
  }

  if (form) {
    var texto = form.querySelector("textarea");
    texto.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); form.requestSubmit(); }
    });
    var rem = form.querySelector("[data-remover-compartilhar]");
    if (rem) rem.addEventListener("click", function () { var c = form.querySelector("[data-compartilhar]"); if (c) c.remove(); });

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var valor = texto.value.trim();
      if (!valor) return;
      var payload = { texto: valor, uuid: uuid() };
      ["anuncio_id", "solicitacao_id", "vaga_id"].forEach(function (k) {
        var i = form.querySelector('input[name="' + k + '"]');
        if (i) payload[k] = i.value;
      });
      var c = form.querySelector("[data-compartilhar]");
      if (c) c.remove();
      var bolha = el("div", "msg mine pending");
      bolha.dataset.uuid = payload.uuid;
      bolha.appendChild(document.createTextNode(valor));
      bolha.appendChild(el("div", "time", "enviando…"));
      var vazio = box.querySelector("[data-vazio]");
      if (vazio) vazio.remove();
      box.appendChild(bolha);
      toBottom();
      texto.value = "";
      enviar(payload, bolha);
    }, true);
  }
})();
