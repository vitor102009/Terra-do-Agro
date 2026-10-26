/* IA Agro: envio de perguntas, exibição das respostas e voz (APIs nativas do navegador, quando existirem). */
(function () {
  "use strict";
  var app = document.getElementById("ia-app");
  if (!app) return;
  var csrf = document.querySelector('meta[name="csrf-token"]').content;
  var lista = app.querySelector("[data-mensagens]");
  var form = app.querySelector("[data-form]");
  var campo = form.querySelector("textarea");
  var enviar = form.querySelector("[data-enviar]");
  var conversaId = app.dataset.conversa || null;
  var ocupado = false;

  function bolha(papel, texto, extra) {
    var d = document.createElement("div");
    d.className = "ia-msg " + papel + (extra && extra.erro ? " erro" : "");
    d.textContent = texto;
    if (extra && extra.ferramentas && extra.ferramentas.length) {
      var s = document.createElement("span");
      s.className = "ia-ferr";
      s.textContent = "Dados consultados: " + extra.ferramentas.join(", ");
      d.appendChild(s);
    }
    var bv = lista.querySelector("[data-boas-vindas]");
    if (bv) bv.remove();
    lista.appendChild(d);
    lista.scrollTop = lista.scrollHeight;
    return d;
  }

  function falar(texto) {
    var ler = form.querySelector("[data-ler]");
    if (!ler || !ler.checked || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    var u = new SpeechSynthesisUtterance(texto);
    u.lang = "pt-BR";
    window.speechSynthesis.speak(u);
  }

  function perguntar(texto) {
    if (ocupado || !texto) return;
    ocupado = true;
    enviar.disabled = true;
    bolha("user", texto);
    var aguardando = bolha("assistant", "Consultando seus dados…");
    aguardando.setAttribute("aria-busy", "true");
    fetch(app.dataset.url, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf, Accept: "application/json" },
      body: JSON.stringify({ pergunta: texto, conversa_id: conversaId }),
    })
      .then(function (r) {
        return r.json().catch(function () { return { erro: "Resposta inválida do servidor (" + r.status + ")." }; });
      })
      .then(function (d) {
        aguardando.remove();
        if (d.mensagem) {
          bolha("assistant", d.mensagem.texto, d.mensagem);
          if (!d.mensagem.erro) falar(d.mensagem.texto);
          if (d.conversa_id && String(d.conversa_id) !== String(conversaId)) {
            conversaId = d.conversa_id;
            history.replaceState(null, "", d.url);
          }
        } else {
          bolha("assistant", d.erro || "Não foi possível enviar a pergunta.", { erro: true });
        }
      })
      .catch(function () {
        aguardando.remove();
        bolha("assistant", "Sem conexão. Verifique a internet e tente novamente.", { erro: true });
      })
      .finally(function () { ocupado = false; enviar.disabled = false; campo.focus(); });
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var t = campo.value.trim();
    if (!t) return;
    campo.value = "";
    perguntar(t);
  });
  campo.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && !ev.shiftKey && !("ontouchstart" in window)) { ev.preventDefault(); form.requestSubmit(); }
  });
  lista.addEventListener("click", function (ev) {
    var b = ev.target.closest("[data-sugestao]");
    if (b && !campo.disabled) perguntar(b.textContent.trim());
  });

  // Voz: só aparece quando o navegador oferece as APIs (Chrome/Edge no computador e Android; Safari parcial).
  var Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  var btnVoz = form.querySelector("[data-voz]");
  if (Rec && btnVoz && window.isSecureContext) {
    btnVoz.hidden = false;
    form.querySelector("[data-voz-aviso]").hidden = false;
    var rec = new Rec();
    rec.lang = "pt-BR"; rec.interimResults = false; rec.maxAlternatives = 1;
    var ouvindo = false;
    rec.onresult = function (e) { campo.value = (campo.value + " " + e.results[0][0].transcript).trim(); campo.focus(); };
    rec.onend = function () { ouvindo = false; btnVoz.setAttribute("aria-pressed", "false"); btnVoz.classList.remove("active"); };
    rec.onerror = function (e) {
      if (e.error === "not-allowed") bolha("assistant", "O navegador não permitiu o uso do microfone.", { erro: true });
    };
    btnVoz.addEventListener("click", function () {
      if (ouvindo) { rec.stop(); return; }
      try { rec.start(); ouvindo = true; btnVoz.setAttribute("aria-pressed", "true"); btnVoz.classList.add("active"); } catch (e) { /* já ativo */ }
    });
  }
  if (window.speechSynthesis) form.querySelector("[data-ler-wrap]").hidden = false;
  lista.scrollTop = lista.scrollHeight;
})();
