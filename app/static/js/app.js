/* Terra do Agro — comportamento geral da interface (sem scripts inline, compatível com CSP). */
(function () {
  "use strict";

  var doc = document;
  var metaUser = doc.querySelector('meta[name="ta-user"]');
  var userKey = metaUser ? metaUser.content : null;

  // ------------------------------------------------------------ Usuário atual para o modo offline
  try {
    if (userKey) {
      localStorage.setItem("ta-current-user", userKey);
      var nome = doc.querySelector('meta[name="ta-user-name"]');
      if (nome) localStorage.setItem("ta-current-user-name", nome.content);
    } else if (doc.querySelector('meta[name="csrf-token"]')) {
      // Página pública renderizada pelo servidor: não há sessão ativa.
      localStorage.removeItem("ta-current-user");
      localStorage.removeItem("ta-current-user-name");
    }
  } catch (e) { /* armazenamento indisponível */ }

  // ------------------------------------------------------------ Menu lateral (celular)
  var sidebar = doc.getElementById("sidebar");
  var backdrop = doc.querySelector(".sidebar-backdrop");
  function toggleSidebar(open) {
    if (!sidebar) return;
    sidebar.classList.toggle("open", open);
    if (backdrop) backdrop.classList.toggle("show", open);
    if (open) { var f = sidebar.querySelector("a, button"); if (f) f.focus(); }
  }
  doc.querySelectorAll("[data-sidebar-open]").forEach(function (b) { b.addEventListener("click", function () { toggleSidebar(true); }); });
  doc.querySelectorAll("[data-sidebar-close]").forEach(function (b) { b.addEventListener("click", function () { toggleSidebar(false); }); });
  doc.addEventListener("keydown", function (ev) { if (ev.key === "Escape") toggleSidebar(false); });

  // ------------------------------------------------------------ Ações simples
  doc.addEventListener("click", function (ev) {
    var el = ev.target.closest("[data-action]");
    if (!el) return;
    var action = el.getAttribute("data-action");
    if (action === "back") {
      ev.preventDefault();
      if (history.length > 1 && doc.referrer && doc.referrer.indexOf(location.host) !== -1) history.back();
      else location.href = "/";
    } else if (action === "reload") {
      ev.preventDefault();
      location.reload();
    } else if (action === "print") {
      ev.preventDefault();
      window.print();
    }
  });

  doc.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { if (el.form) el.form.submit(); });
  });

  // Confirmação antes de ações destrutivas e bloqueio de envio duplo.
  doc.addEventListener("submit", function (ev) {
    var form = ev.target;
    var msg = form.getAttribute("data-confirm");
    if (msg && !window.confirm(msg)) { ev.preventDefault(); return; }
    if (form.hasAttribute("data-logout-form")) { handleLogout(ev, form); return; }
    if (form.hasAttribute("data-no-loading")) return;
    if (form.hasAttribute("data-loading") || form.method.toLowerCase() === "post") {
      if (form.dataset.sending === "1") { ev.preventDefault(); return; }
      form.dataset.sending = "1";
      var btn = form.querySelector('button[type="submit"]:not([formnovalidate])');
      if (btn) {
        btn.disabled = true;
        var sp = doc.createElement("span");
        sp.className = "spinner-border spinner-border-sm ms-2";
        sp.setAttribute("aria-hidden", "true");
        btn.appendChild(sp);
      }
      // Libera novamente caso o navegador volte para a página (bfcache).
      window.addEventListener("pageshow", function () { form.dataset.sending = ""; if (btn) { btn.disabled = false; if (sp) sp.remove(); } }, { once: true });
    }
  }, true);

  // Fecha alertas flash automaticamente.
  setTimeout(function () {
    doc.querySelectorAll(".toast-area .alert-success, .toast-area .alert-info").forEach(function (a) {
      if (window.bootstrap) bootstrap.Alert.getOrCreateInstance(a).close();
    });
  }, 6000);

  // Galeria de fotos.
  doc.querySelectorAll("[data-gallery]").forEach(function (g) {
    var main = g.querySelector("[data-gallery-main]");
    g.querySelectorAll("[data-gallery-thumb]").forEach(function (t) {
      t.addEventListener("click", function () {
        main.src = t.getAttribute("data-gallery-thumb");
        g.querySelectorAll("[data-gallery-thumb]").forEach(function (x) { x.classList.remove("active"); });
        t.classList.add("active");
      });
    });
  });

  // Campos condicionais: data-show-when="campo=valor1,valor2"
  doc.querySelectorAll("[data-show-when]").forEach(function (box) {
    var parts = box.getAttribute("data-show-when").split("=");
    var field = doc.querySelector('[name="' + parts[0] + '"]');
    var values = parts[1].split(",");
    if (!field) return;
    function update() {
      var val = field.type === "checkbox" ? (field.checked ? "1" : "") : field.value;
      box.hidden = values.indexOf(val) === -1;
    }
    field.addEventListener("change", update);
    update();
  });

  // ------------------------------------------------------------ Conexão e fila offline
  var pill = doc.getElementById("conn-pill");
  function updateConn() {
    if (!pill) return;
    if (!navigator.onLine) {
      pill.className = "conn-pill offline";
      pill.innerHTML = '<i class="bi bi-wifi-off" aria-hidden="true"></i>Sem conexão';
      return;
    }
    if (window.TAOffline && userKey) {
      TAOffline.countPending(userKey).then(function (n) {
        if (n > 0) {
          pill.className = "conn-pill pending";
          pill.innerHTML = "";
          var a = doc.createElement("a");
          a.href = "/app-offline";
          a.className = "text-reset";
          a.innerHTML = '<i class="bi bi-cloud-arrow-up" aria-hidden="true"></i> ';
          a.appendChild(doc.createTextNode(n + " pendente(s) para sincronizar"));
          pill.appendChild(a);
        } else {
          pill.className = "conn-pill";
          pill.textContent = "";
        }
      }).catch(function () {});
    } else {
      pill.className = "conn-pill";
      pill.textContent = "";
    }
  }
  window.addEventListener("online", function () {
    updateConn();
    if (window.TAOffline && userKey) TAOffline.syncAll(userKey).then(updateConn).catch(updateConn);
  });
  window.addEventListener("offline", updateConn);
  window.TAUpdateConn = updateConn;
  updateConn();

  // Atualiza a cópia local de dados para uso offline (no máximo a cada 30 minutos).
  if (window.TAOffline && userKey && navigator.onLine) {
    TAOffline.syncAll(userKey).catch(function () {}).then(function () {
      return TAOffline.refreshSnapshotIfOld(userKey, 30 * 60 * 1000);
    }).catch(function () {}).then(updateConn);
  }

  function handleLogout(ev, form) {
    if (!window.TAOffline || !userKey || form.dataset.confirmed === "1") return;
    ev.preventDefault();
    TAOffline.countPending(userKey).then(function (n) {
      if (n > 0 && !window.confirm("Há " + n + " registro(s) feitos offline que ainda não foram enviados. " +
          "Eles continuarão guardados neste aparelho e só serão enviados quando você entrar novamente nesta conta. Deseja sair mesmo assim?")) {
        return;
      }
      return TAOffline.clearPrivateData(userKey).then(function () {
        try { localStorage.removeItem("ta-current-user"); localStorage.removeItem("ta-current-user-name"); } catch (e) {}
        form.dataset.confirmed = "1";
        form.submit();
      });
    }).catch(function () { form.dataset.confirmed = "1"; form.submit(); });
  }

  // ------------------------------------------------------------ Service Worker (PWA)
  if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost" || location.hostname === "127.0.0.1")) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/service-worker.js", { scope: "/" }).catch(function (err) {
        console.warn("Service Worker não registrado:", err);
      });
    });
  }

  // Botão de instalação do aplicativo, quando o navegador oferece.
  var installEvt = null;
  window.addEventListener("beforeinstallprompt", function (e) {
    e.preventDefault();
    installEvt = e;
    doc.querySelectorAll("[data-install-app]").forEach(function (b) { b.hidden = false; });
  });
  doc.querySelectorAll("[data-install-app]").forEach(function (b) {
    b.addEventListener("click", function () {
      if (!installEvt) return;
      installEvt.prompt();
      installEvt.userChoice.finally(function () { installEvt = null; b.hidden = true; });
    });
  });
})();
